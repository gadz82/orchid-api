"""Legacy and utility endpoints — single-shot chat, indexing, health."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage

from orchid_ai.core.state import AuthContext
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import app_ctx, get_graph, get_runtime
from ..models import ChatRequest, ChatResponse, IndexRequest, IndexResponse
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["legacy"])


@router.post("/chat", response_model=ChatResponse, deprecated=True)
async def chat_legacy(
    request: ChatRequest,
    auth: AuthContext = Depends(get_auth_context),
    graph: Any = Depends(get_graph),
):
    """
    Legacy single-shot chat — no persistence.
    Creates an ephemeral chat_id for RAG scoping.

    .. deprecated::
        Use ``POST /chats`` to create a chat, then
        ``POST /chats/{chat_id}/messages`` to send a message.  This
        endpoint is retained for backwards compatibility and will be
        removed in a future release.
    """
    chat_id = request.chat_id or str(uuid.uuid4())

    initial_state = {
        "messages": [HumanMessage(content=request.message)],
        "auth_context": auth,
        "chat_id": chat_id,
    }

    logger.info(
        "[API] /chat (legacy) tenant=%s message=%s…",
        auth.tenant_key,
        request.message[:80],
    )

    graph_config = {"configurable": {"thread_id": chat_id}}
    result = await graph.ainvoke(initial_state, config=graph_config)

    return ChatResponse(
        response=result.get("final_response", "No response generated."),
        chat_id=chat_id,
        tenant_id=auth.tenant_key,
        agents_used=result.get("active_agents", []),
    )


@router.post("/index", response_model=IndexResponse)
async def index_data(
    request: IndexRequest,
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    runtime: OrchidRuntime = Depends(get_runtime),
):
    """Manually index seed data into the vector store for a tenant.

    Gated by ``settings.allow_index_endpoint``: disabled by default so a
    plain authenticated user cannot trigger an expensive reindex.  Flip
    the setting (or the ``ALLOW_INDEX_ENDPOINT`` env var) to enable in
    dev / ops flows.
    """
    if not settings.allow_index_endpoint:
        raise HTTPException(
            status_code=403,
            detail="The /index endpoint is disabled. Set ALLOW_INDEX_ENDPOINT=true to enable.",
        )

    from orchid_ai.core.repository import VectorWriter

    reader = runtime.get_reader()

    if not isinstance(reader, VectorWriter):
        raise HTTPException(
            status_code=503,
            detail="Vector store does not support writing (backend may be 'null')",
        )

    from orchid_ai.rag.indexer import StaticIndexer

    indexer = StaticIndexer(writer=reader)
    counts = await indexer.index_all(tenant_key=request.tenant_id)

    logger.info("[API] /index tenant=%s counts=%s", request.tenant_id, counts)
    return IndexResponse(
        status="ok",
        tenant_id=request.tenant_id,
        indexed=counts,
    )


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    return {
        "status": "ok",
        "model": app_ctx.runtime.default_model,
        "domain": settings.auth_domain,
        "vector_backend": settings.vector_backend,
        "graph_ready": app_ctx.graph is not None,
    }
