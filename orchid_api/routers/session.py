"""Per-user MCP capability warm-up endpoint.

The frontend calls ``POST /session/warm`` once after the user finishes
the login dance to populate every ``auth.mode: passthrough`` /
``auth.mode: oauth`` server's per-server capability cache up front,
removing the first-message latency spike that lazy discovery would
otherwise pay on the first chat invocation.

Idempotent — a second call by the same user returns an empty-but-OK
report immediately.  Failures on individual servers are reported in
the response body, never as HTTP errors.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.session_warmer import OrchidWarmReport

from ..auth import get_auth_context
from ..context import app_ctx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["session"])


def _report_to_dict(report: OrchidWarmReport) -> dict:
    return {
        "warmed": list(report.warmed),
        "skipped": list(report.skipped),
        "failed": dict(report.failed),
    }


@router.post("/warm")
async def warm_session(auth: OrchidAuthContext = Depends(get_auth_context)) -> dict:
    """Warm passthrough/oauth MCP caches for the authenticated user.

    Idempotent within a process lifetime — the second call short-
    circuits inside :class:`OrchidSessionWarmer` and returns a report
    where every list is empty, signalling "already warmed".  Frontends
    can call this synchronously after login (await it before showing
    the chat UI) or fire-and-forget; either way is fine.
    """
    if app_ctx.orchid is None:
        # Misconfigured deployment — fail fast.  In practice
        # ``setup_orchid`` builds the facade before any request lands.
        raise HTTPException(status_code=503, detail="Orchid runtime not initialised")

    warmer = app_ctx.orchid.session_warmer
    report = await warmer.warm_for_user(auth)
    return _report_to_dict(report)
