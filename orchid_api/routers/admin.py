"""Operator / admin endpoints — bulk indexing.

Lives in its own router so it can be auth-gated independently from
the user-facing chat endpoints. Currently only carries ``POST /index``;
future additions (cache flush, capability re-warm, …) belong here too.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import get_runtime
from ..models import IndexRequest, IndexResponse
from ..rate_limit import rate_limit
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

_settings = get_settings()
_index_rate_limit = rate_limit(
    "index",
    calls=_settings.rate_limit_index_per_minute,
    period=60.0,
)


@router.post(
    "/index",
    response_model=IndexResponse,
    dependencies=[Depends(_index_rate_limit)],
)
async def index_data(
    request: IndexRequest,
    auth: OrchidAuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    runtime: OrchidRuntime = Depends(get_runtime),
) -> IndexResponse:
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

    from orchid_ai.core.repository import OrchidVectorWriter

    reader = runtime.get_reader()

    if not isinstance(reader, OrchidVectorWriter):
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
