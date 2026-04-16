"""Shared helpers for message and streaming routers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from fastapi import HTTPException, UploadFile
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from orchid_ai.core.state import AuthContext

from ..context import app_ctx
from ..settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class PreparedState:
    """Result of preparing graph input state from an API request."""

    initial_state: dict
    message: str  # original (non-augmented) user message
    history_rows: list  # raw DB rows (for auto-titling check)
    mcp_auth_status: dict[str, bool] = field(default_factory=dict)


async def prepare_graph_state(
    chat_id: str,
    message: str,
    files: list[UploadFile],
    auth: AuthContext,
    settings: Settings,
) -> PreparedState:
    """
    Shared preparation for both sync and streaming message endpoints.

    Validates ownership, processes files, loads history, checks MCP auth,
    and builds the initial graph state.
    """
    if app_ctx.graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialised")
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    # Verify chat ownership
    chat = await app_ctx.chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    # ── Process attached files ───────────────────────────────
    file_context_parts: list[str] = []
    reader = app_ctx.runtime.get_reader()

    if files:
        from orchid_ai.core.repository import VectorWriter
        from orchid_ai.documents.chunker import ChunkConfig
        from orchid_ai.documents.pipeline import extract_text, ingest_document
        from orchid_ai.rag.scopes import RAGScope

        if isinstance(reader, VectorWriter):
            scope = RAGScope(
                tenant_id=auth.tenant_key,
                user_id=auth.user_id,
                chat_id=chat_id,
            )
            chunk_config = ChunkConfig(
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            )
            vision_model = settings.vision_model or settings.litellm_model

            for file in files:
                if not file.filename:
                    continue
                file_bytes = await file.read()
                max_bytes = settings.upload_max_size_mb * 1024 * 1024
                if len(file_bytes) > max_bytes:
                    logger.warning("[Upload] %s too large (%d bytes), skipping", file.filename, len(file_bytes))
                    continue

                try:
                    extracted_text = await extract_text(
                        file_bytes=file_bytes,
                        filename=file.filename,
                        vision_model=vision_model,
                    )
                    if extracted_text.strip():
                        file_context_parts.append(
                            f"--- Content of attached file: {file.filename} ---\n{extracted_text}"
                        )

                    await ingest_document(
                        file_bytes=file_bytes,
                        filename=file.filename,
                        scope=scope,
                        namespace=settings.upload_namespace,
                        writer=reader,
                        chunk_config=chunk_config,
                        pre_extracted_text=extracted_text,
                    )
                except Exception as exc:
                    logger.error("[Upload] Failed to process %s: %s", file.filename, exc)

    # ── Build the augmented prompt ───────────────────────────
    if file_context_parts:
        augmented_message = "\n\n".join(file_context_parts) + f"\n\n--- User message ---\n{message}"
        logger.info(
            "[API] Augmented prompt with %d file(s), total context=%d chars. First 300: %s",
            len(file_context_parts),
            len(augmented_message),
            augmented_message[:300],
        )
    else:
        augmented_message = message

    # Load conversation history
    history_rows = await app_ctx.chat_repo.get_messages(chat_id, limit=50)
    history_messages: list[BaseMessage] = []
    for row in history_rows:
        if row.role == "user":
            history_messages.append(HumanMessage(content=row.content, id=row.id))
        elif row.role == "assistant":
            history_messages.append(AIMessage(content=row.content, id=row.id))

    # ── Pre-flight MCP auth check ─────────────────────────────
    mcp_auth_status: dict[str, bool] = {}
    registry = app_ctx.runtime.mcp_auth_registry
    store = app_ctx.mcp_token_store
    if registry and not registry.empty and store:
        for name in registry.oauth_servers:
            token = await store.get_token(auth.tenant_key, auth.user_id, name)
            mcp_auth_status[name] = token is not None and not token.is_expired

    # Build initial state.
    # When a checkpointer is active the graph persists conversation state
    # internally.  Sending full history would duplicate messages via the
    # add_messages annotation — only send the new user message.
    has_checkpointer = app_ctx.runtime is not None and app_ctx.runtime.checkpointer is not None

    if has_checkpointer:
        initial_state: dict = {
            "messages": [HumanMessage(content=augmented_message)],
            "auth_context": auth,
            "chat_id": chat_id,
        }
    else:
        initial_state: dict = {
            "messages": history_messages + [HumanMessage(content=augmented_message)],
            "auth_context": auth,
            "chat_id": chat_id,
        }
    if mcp_auth_status:
        initial_state["mcp_auth_status"] = mcp_auth_status

    logger.info(
        "[API] /chats/%s/messages user=%s history=%d files=%d message=%s…",
        chat_id[:8],
        auth.user_id[:8] if auth.user_id else "?",
        len(history_messages),
        len(files),
        message[:80],
    )

    return PreparedState(
        initial_state=initial_state,
        message=message,
        history_rows=history_rows,
        mcp_auth_status=mcp_auth_status,
    )
