"""Infrastructure diagnostics — readiness check.

Sits in its own router so the ``/health`` URL can be reached without
touching any user-facing logic, and can later grow more probes
(``/ready``, ``/metrics``, …) without bloating ``main.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..context import app_ctx
from ..settings import Settings, get_settings

router = APIRouter(tags=["diagnostics"])


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness probe — returns deployment metadata + graph readiness."""
    return {
        "status": "ok",
        "model": app_ctx.runtime.default_model,
        "domain": settings.auth_domain,
        "vector_backend": settings.vector_backend,
        "graph_ready": app_ctx.graph is not None,
    }
