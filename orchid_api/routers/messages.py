"""Message sending and document upload endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from langchain_core.messages import AIMessage, HumanMessage

from orchid.core.state import AuthContext

from ..auth import get_auth_context
from ..context import app_ctx
from ..models import ChatResponse
from ..settings import Settings, get_settings

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
    Send a message in a chat — optionally with attached files.

    When files are provided they are:
      1. Parsed and their text extracted (immediate context for the LLM).
      2. Chunked and indexed into Qdrant (long-term RAG for future turns).
      3. The extracted text is prepended to the user's message so the
         agent can see the file content in the current turn.
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

    if files and app_ctx.reader is not None:
        from orchid.core.repository import VectorWriter
        from orchid.documents.chunker import ChunkConfig
        from orchid.documents.pipeline import extract_text, ingest_document
        from orchid.rag.scopes import RAGScope

        if isinstance(app_ctx.reader, VectorWriter):
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
                        writer=app_ctx.reader,
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
    history_messages = []
    for row in history_rows:
        if row.role == "user":
            history_messages.append(HumanMessage(content=row.content, id=row.id))
        elif row.role == "assistant":
            history_messages.append(AIMessage(content=row.content, id=row.id))

    # Build initial state with history + augmented message
    initial_state = {
        "messages": history_messages + [HumanMessage(content=augmented_message)],
        "auth_context": auth,
        "chat_id": chat_id,
    }

    logger.info(
        "[API] /chats/%s/messages user=%s history=%d files=%d message=%s…",
        chat_id[:8],
        auth.user_id[:8] if auth.user_id else "?",
        len(history_messages),
        len(files),
        message[:80],
    )

    # Run the agent graph
    result = await app_ctx.graph.ainvoke(initial_state)

    response_text = result.get("final_response", "No response generated.")
    agents_used = result.get("active_agents", [])

    # Persist the original user message (not augmented) + assistant response
    await app_ctx.chat_repo.add_message(chat_id, "user", message)
    await app_ctx.chat_repo.add_message(chat_id, "assistant", response_text, agents_used=agents_used)

    # Auto-title from first message
    if not history_rows:
        title = message[:50].strip()
        if len(message) > 50:
            title += "…"
        await app_ctx.chat_repo.update_title(chat_id, title)

    return ChatResponse(
        response=response_text,
        chat_id=chat_id,
        tenant_id=auth.tenant_key,
        agents_used=agents_used,
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
    from orchid.core.repository import VectorWriter
    from orchid.documents.chunker import ChunkConfig
    from orchid.documents.pipeline import ingest_document
    from orchid.rag.scopes import RAGScope

    if app_ctx.reader is None or not isinstance(app_ctx.reader, VectorWriter):
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
                writer=app_ctx.reader,
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
