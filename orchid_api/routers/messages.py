"""Message sending and document upload endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from orchid_ai.core.state import AuthContext

from ..auth import get_auth_context
from ..context import app_ctx
from ..models import ChatResponse
from ..settings import Settings, get_settings
from ._helpers import prepare_graph_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["messages"])


@router.post("/{chat_id}/messages", response_model=ChatResponse)
async def send_chat_message(
    chat_id: str,
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
):
    """
    Send a message in a chat — optionally with attached files (non-streaming).

    When files are provided they are:
      1. Parsed and their text extracted (immediate context for the LLM).
      2. Chunked and indexed into Qdrant (long-term RAG for future turns).
      3. The extracted text is prepended to the user's message so the
         agent can see the file content in the current turn.
    """
    prepared = await prepare_graph_state(chat_id, message, files, auth, settings)

    # Run the agent graph (blocking — returns full response)
    result = await app_ctx.graph.ainvoke(prepared.initial_state)

    response_text = result.get("final_response", "No response generated.")
    agents_used = result.get("active_agents", [])

    # Persist the original user message (not augmented) + assistant response
    await app_ctx.chat_repo.add_message(chat_id, "user", prepared.message)
    await app_ctx.chat_repo.add_message(chat_id, "assistant", response_text, agents_used=agents_used)

    # Auto-title from first message
    if not prepared.history_rows:
        title = prepared.message[:50].strip()
        if len(prepared.message) > 50:
            title += "…"
        await app_ctx.chat_repo.update_title(chat_id, title)

    auth_required = [name for name, ok in prepared.mcp_auth_status.items() if not ok]
    return ChatResponse(
        response=response_text,
        chat_id=chat_id,
        tenant_id=auth.tenant_key,
        agents_used=agents_used,
        auth_required=auth_required,
    )


# ── Document Upload ──────────────────────────────────────────


@router.post("/{chat_id}/upload")
async def upload_documents(
    chat_id: str,
    files: list[UploadFile],
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
):
    """Upload documents for chat-scoped RAG."""
    from orchid_ai.core.repository import VectorWriter
    from orchid_ai.documents.chunker import ChunkConfig
    from orchid_ai.documents.pipeline import ingest_document
    from orchid_ai.rag.scopes import RAGScope

    reader = app_ctx.runtime.get_reader()

    if not isinstance(reader, VectorWriter):
        raise HTTPException(status_code=503, detail="Vector store does not support writing")
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    chat = await app_ctx.chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    scope = RAGScope(
        tenant_id=auth.tenant_key,
        user_id=auth.user_id,
        chat_id=chat_id,
    )
    chunk_config = ChunkConfig(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    results = []
    for file in files:
        if not file.filename:
            continue

        file_bytes = await file.read()
        max_bytes = settings.upload_max_size_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            results.append(
                {"filename": file.filename, "error": f"File too large (max {settings.upload_max_size_mb}MB)"}
            )
            continue

        try:
            chunks = await ingest_document(
                file_bytes=file_bytes,
                filename=file.filename,
                scope=scope,
                namespace=settings.upload_namespace,
                writer=reader,
                chunk_config=chunk_config,
                vision_model=settings.vision_model or settings.litellm_model,
            )
            results.append({"filename": file.filename, "chunks_indexed": chunks})

            await app_ctx.chat_repo.add_message(
                chat_id,
                "system",
                f"Uploaded {file.filename} ({chunks} chunks indexed)",
            )
        except ValueError as exc:
            results.append({"filename": file.filename, "error": str(exc)})
        except Exception as exc:
            logger.error("[Upload] Failed to process %s: %s", file.filename, exc)
            results.append({"filename": file.filename, "error": "Processing failed"})

    return {"status": "ok", "files": results}
