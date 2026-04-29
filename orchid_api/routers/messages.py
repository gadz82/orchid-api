"""Message sending and document upload endpoints."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from langgraph.errors import GraphInterrupt

from orchid_ai.core.mcp import OrchidMCPTokenStore
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.observability import OrchidMetricsHandler
from orchid_ai.persistence.base import OrchidChatStorage
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import get_chat_repo, get_graph, get_mcp_token_store_optional, get_runtime
from ..models import ChatResponse, InterruptResponse
from ..settings import Settings, get_settings
from ._helpers import (
    auto_title_if_first_message,
    build_interrupt_response,
    prepare_graph_state,
    verify_chat_ownership,
)

logger = logging.getLogger(__name__)
perf_logger = logging.getLogger("orchid.perf")

router = APIRouter(prefix="/chats", tags=["messages"])


@router.post("/{chat_id}/messages", response_model=ChatResponse | InterruptResponse)
async def send_chat_message(
    chat_id: str,
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    auth: OrchidAuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
    runtime: OrchidRuntime = Depends(get_runtime),
    graph: Any = Depends(get_graph),
    mcp_token_store: OrchidMCPTokenStore | None = Depends(get_mcp_token_store_optional),
):
    """
    Send a message in a chat — optionally with attached files (non-streaming).

    Returns ``ChatResponse`` on normal completion, or ``InterruptResponse``
    when the graph pauses for human-in-the-loop tool approval.
    """
    request_id = uuid.uuid4().hex[:8]
    request_start = time.perf_counter()
    perf_logger.info(
        "[PERF][req=%s] === REQUEST START === chat=%s files=%d msg_len=%d",
        request_id,
        chat_id[:8],
        len(files),
        len(message),
    )

    prep_start = time.perf_counter()
    prepared = await prepare_graph_state(
        chat_id,
        message,
        files,
        auth,
        settings,
        chat_repo=chat_repo,
        runtime=runtime,
        mcp_token_store=mcp_token_store,
    )
    prep_elapsed = (time.perf_counter() - prep_start) * 1000
    perf_logger.info("[PERF][req=%s] prepare_graph_state took %.1f ms", request_id, prep_elapsed)

    # Run the agent graph (blocking — returns full response)
    graph_config = {"configurable": {"thread_id": chat_id, "request_id": request_id}}
    metrics = OrchidMetricsHandler()
    graph_config["callbacks"] = [metrics]

    graph_start = time.perf_counter()
    try:
        result = await graph.ainvoke(prepared.initial_state, config=graph_config)
    except GraphInterrupt as exc:
        # HITL: graph paused for tool approval — don't persist messages yet
        graph_elapsed = (time.perf_counter() - graph_start) * 1000
        total_elapsed = (time.perf_counter() - request_start) * 1000
        perf_logger.info(
            "[PERF][req=%s] graph.ainvoke (interrupted) took %.1f ms | total=%.1f ms",
            request_id,
            graph_elapsed,
            total_elapsed,
        )
        return build_interrupt_response(exc, chat_id, auth.tenant_key)
    graph_elapsed = (time.perf_counter() - graph_start) * 1000

    response_text = result.get("final_response", "No response generated.")
    agents_used = result.get("active_agents", [])

    # Persist the original user message (not augmented) + assistant response
    persist_start = time.perf_counter()
    await chat_repo.add_message(chat_id, "user", prepared.message)
    await chat_repo.add_message(chat_id, "assistant", response_text, agents_used=agents_used)
    await auto_title_if_first_message(chat_id, prepared.message, prepared.history_rows, chat_repo)
    persist_elapsed = (time.perf_counter() - persist_start) * 1000

    total_elapsed = (time.perf_counter() - request_start) * 1000

    # ── Aggregate metrics summary ──
    m = metrics.get_metrics()
    perf_logger.info(
        "[PERF][req=%s] graph.ainvoke took %.1f ms | persist=%.1f ms | total=%.1f ms",
        request_id,
        graph_elapsed,
        persist_elapsed,
        total_elapsed,
    )
    perf_logger.info(
        "[PERF][req=%s] LLM stats: calls=%d errors=%d avg_latency=%.3fs total_tokens=%d (prompt=%d completion=%d)",
        request_id,
        m["llm_calls"],
        m["llm_errors"],
        m["avg_llm_latency_s"],
        m["total_tokens"],
        m["prompt_tokens"],
        m["completion_tokens"],
    )
    perf_logger.info(
        "[PERF][req=%s] Tool stats: tool_calls=%d retries=%d",
        request_id,
        m["tool_calls"],
        m["retries"],
    )
    if m["agent_latencies_s"]:
        perf_logger.info(
            "[PERF][req=%s] Agent latencies (avg s): %s | call_counts: %s",
            request_id,
            m["agent_latencies_s"],
            m["agent_call_counts"],
        )
    perf_logger.info("[PERF][req=%s] === REQUEST END === total=%.1f ms", request_id, total_elapsed)

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
    auth: OrchidAuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
    runtime: OrchidRuntime = Depends(get_runtime),
):
    """Upload documents for chat-scoped RAG."""
    from orchid_ai.core.repository import OrchidVectorWriter
    from orchid_ai.documents.chunker import ChunkConfig
    from orchid_ai.documents.pipeline import ingest_document
    from orchid_ai.rag.scopes import OrchidRAGScope

    reader = runtime.get_reader()

    if not isinstance(reader, OrchidVectorWriter):
        raise HTTPException(status_code=503, detail="Vector store does not support writing")

    await verify_chat_ownership(chat_id, auth, chat_repo)

    scope = OrchidRAGScope(
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

            await chat_repo.add_message(
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
