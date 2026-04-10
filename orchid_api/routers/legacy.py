"""Legacy and utility endpoints — single-shot chat, indexing, health."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage

from orchid.core.state import AuthContext

from ..auth import get_auth_context
from ..context import app_ctx
from ..models import ChatRequest, ChatResponse, IndexRequest, IndexResponse
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["legacy"])


@router.post("/chat", response_model=ChatResponse)
async def chat_legacy(
    request: ChatRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """
    Legacy single-shot chat — no persistence.
    Creates an ephemeral chat_id for RAG scoping.
    """
    if app_ctx.graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialised")

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

    result = await app_ctx.graph.ainvoke(initial_state)

    return ChatResponse(
        response=result.get("final_response", "No response generated."),
        chat_id=chat_id,
        tenant_id=auth.tenant_key,
        agents_used=result.get("active_agents", []),
    )


@router.post("/index", response_model=IndexResponse)
async def index_data(request: IndexRequest):
    """Manually index test data into the vector store for a tenant."""
    from orchid.core.repository import VectorWriter

    reader = app_ctx.runtime.get_reader()

    if not isinstance(reader, VectorWriter):
        raise HTTPException(
            status_code=503,
            detail="Vector store does not support writing (backend may be 'null')",
        )

    from orchid.rag.indexer import StaticIndexer

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
