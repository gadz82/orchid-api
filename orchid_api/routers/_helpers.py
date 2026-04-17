"""Shared helpers for message, streaming, and resume routers.

``prepare_graph_state`` orchestrates four single-responsibility steps:
  1. :func:`process_uploaded_files` — parse + ingest attached files.
  2. :func:`load_conversation_history` — read persisted messages (skipped
     when a LangGraph checkpointer owns the state).
  3. :func:`check_mcp_auth` — pre-flight per-user OAuth status.
  4. :func:`build_initial_graph_state` — assemble the ``GraphState`` dict.

Keeping each concern callable on its own makes the code testable in
isolation and lets future endpoints (e.g. a "preview" endpoint that
only renders the augmented prompt) reuse individual steps without
pulling in the rest.

The helpers accept their dependencies (``chat_repo``, ``runtime``,
``mcp_token_store``) as arguments — null-checks live in the FastAPI
dependency helpers (``context.get_chat_repo`` etc.), not here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, UploadFile
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from orchid_ai.core.mcp import MCPTokenStore
from orchid_ai.core.repository import VectorReader, VectorWriter
from orchid_ai.core.state import AuthContext
from orchid_ai.mcp.auth_registry import MCPAuthRegistry
from orchid_ai.persistence.base import ChatStorage
from orchid_ai.runtime import OrchidRuntime

from ..models import InterruptResponse, ToolApprovalRequest
from ..settings import Settings

logger = logging.getLogger(__name__)


# ── Ownership verification ────────────────────────────────────


async def verify_chat_ownership(chat_id: str, auth: AuthContext, chat_repo: ChatStorage) -> Any:
    """Verify the chat exists and belongs to the authenticated user+tenant.

    Returns the chat object on success; raises HTTP 404 on failure.  The
    caller is expected to have resolved ``chat_repo`` via
    :func:`context.get_chat_repo` (which already guarantees non-None).
    """
    chat = await chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


# ── Auto-titling ──────────────────────────────────────────────


async def auto_title_if_first_message(
    chat_id: str,
    message: str,
    history_rows: list,
    chat_repo: ChatStorage,
) -> None:
    """Set the chat title from the first user message (if no prior messages)."""
    if history_rows:
        return
    title = message[:50].strip()
    if len(message) > 50:
        title += "..."
    await chat_repo.update_title(chat_id, title)


# ── GraphInterrupt → InterruptResponse conversion ────────────


def build_interrupt_response(exc: Exception, chat_id: str, tenant_key: str) -> InterruptResponse:
    """Convert a ``GraphInterrupt`` exception into an ``InterruptResponse``.

    Defensive against unexpected interrupt shapes — anything that isn't a
    list of interrupt objects becomes an empty ``approvals_needed`` list
    with a warning log.
    """
    raw = exc.args[0] if exc.args else []
    if not isinstance(raw, (list, tuple)):
        logger.warning("[HITL] Unexpected interrupt payload (%s), treating as empty", type(raw).__name__)
        raw = []
    approvals = [
        ToolApprovalRequest(
            tool=i.value.get("tool", "") if isinstance(i.value, dict) else str(i.value),
            args=i.value.get("args", {}) if isinstance(i.value, dict) else {},
            agent=i.value.get("agent", "") if isinstance(i.value, dict) else "",
            interrupt_id=str(i.id),
        )
        for i in raw
    ]
    return InterruptResponse(
        chat_id=chat_id,
        tenant_id=tenant_key,
        approvals_needed=approvals,
    )


# ── Single-responsibility steps used by prepare_graph_state ───


async def process_uploaded_files(
    chat_id: str,
    files: list[UploadFile],
    auth: AuthContext,
    settings: Settings,
    reader: VectorReader,
) -> list[str]:
    """Parse + ingest attached files; return prompt-augmentation parts.

    Ingestion is skipped when the reader does not implement
    :class:`VectorWriter`; in that case the files are only parsed into
    the returned context strings and not written to the vector store.
    """
    if not files:
        return []

    from orchid_ai.documents.chunker import ChunkConfig
    from orchid_ai.documents.pipeline import extract_text, ingest_document
    from orchid_ai.rag.scopes import RAGScope

    can_ingest = isinstance(reader, VectorWriter)
    chunk_config = ChunkConfig(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    scope = RAGScope(
        tenant_id=auth.tenant_key,
        user_id=auth.user_id,
        chat_id=chat_id,
    )
    vision_model = settings.vision_model or settings.litellm_model
    max_bytes = settings.upload_max_size_mb * 1024 * 1024

    parts: list[str] = []
    for file in files:
        if not file.filename:
            continue
        file_bytes = await file.read()
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
                parts.append(f"--- Content of attached file: {file.filename} ---\n{extracted_text}")

            if can_ingest:
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

    return parts


async def load_conversation_history(
    chat_id: str,
    chat_repo: ChatStorage,
    *,
    limit: int = 50,
) -> tuple[list[BaseMessage], list]:
    """Load persisted conversation history from the chat repo.

    Returns ``(history_messages, raw_rows)``.  The raw rows are handed
    back so ``auto_title_if_first_message`` can detect a brand-new chat.
    """
    rows = await chat_repo.get_messages(chat_id, limit=limit)
    messages: list[BaseMessage] = []
    for row in rows:
        if row.role == "user":
            messages.append(HumanMessage(content=row.content, id=row.id))
        elif row.role == "assistant":
            messages.append(AIMessage(content=row.content, id=row.id))
    return messages, rows


async def check_mcp_auth(
    auth: AuthContext,
    registry: MCPAuthRegistry | None,
    store: MCPTokenStore | None,
) -> dict[str, bool]:
    """Return ``{server_name: is_authorized}`` for every OAuth MCP server."""
    if not registry or registry.empty or store is None:
        return {}
    status: dict[str, bool] = {}
    for name in registry.oauth_servers:
        token = await store.get_token(auth.tenant_key, auth.user_id, name)
        status[name] = token is not None and not token.is_expired
    return status


def build_augmented_message(message: str, file_parts: list[str]) -> str:
    """Compose the prompt that goes into the graph (original + file text)."""
    if not file_parts:
        return message
    augmented = "\n\n".join(file_parts) + f"\n\n--- User message ---\n{message}"
    logger.info(
        "[API] Augmented prompt with %d file(s), total=%d chars. First 300: %s",
        len(file_parts),
        len(augmented),
        augmented[:300],
    )
    return augmented


def build_initial_graph_state(
    *,
    augmented_message: str,
    history: list[BaseMessage],
    auth: AuthContext,
    chat_id: str,
    mcp_auth_status: dict[str, bool],
    has_checkpointer: bool,
) -> dict[str, Any]:
    """Assemble the ``GraphState`` dict passed to ``graph.ainvoke``.

    When a checkpointer is active the graph persists conversation state
    internally — sending full history would duplicate messages via the
    ``add_messages`` annotation, so we only send the new user message.
    """
    new_user_msg = HumanMessage(content=augmented_message)
    messages = [new_user_msg] if has_checkpointer else [*history, new_user_msg]

    state: dict[str, Any] = {
        "messages": messages,
        "auth_context": auth,
        "chat_id": chat_id,
    }
    if mcp_auth_status:
        state["mcp_auth_status"] = mcp_auth_status
    return state


# ── Top-level orchestration (used by message / streaming endpoints) ──


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
    *,
    chat_repo: ChatStorage,
    runtime: OrchidRuntime,
    mcp_token_store: MCPTokenStore | None,
) -> PreparedState:
    """Compose the :class:`PreparedState` for both sync and streaming endpoints.

    Delegates each concern to a focused helper; the routers call this
    single entry point, but unit tests can exercise the individual steps.
    The ``chat_repo`` / ``runtime`` / ``mcp_token_store`` are injected by
    the calling router (via FastAPI ``Depends``) rather than resolved
    from ``app_ctx``.
    """
    await verify_chat_ownership(chat_id, auth, chat_repo)

    reader = runtime.get_reader()

    file_parts = await process_uploaded_files(chat_id, files, auth, settings, reader)
    augmented_message = build_augmented_message(message, file_parts)

    history_messages, history_rows = await load_conversation_history(chat_id, chat_repo)

    mcp_auth_status = await check_mcp_auth(auth, runtime.mcp_auth_registry, mcp_token_store)

    initial_state = build_initial_graph_state(
        augmented_message=augmented_message,
        history=history_messages,
        auth=auth,
        chat_id=chat_id,
        mcp_auth_status=mcp_auth_status,
        has_checkpointer=runtime.checkpointer is not None,
    )

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
