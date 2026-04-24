"""``GET /auth-info`` — public auth posture probe.

Consumed by MCP gateways (``orchid-mcp``) at startup so they can
validate their own auth-mode against the upstream.  Returns only
*non-secret* posture: whether dev-bypass is active and whether an
identity resolver is configured.  No token, domain, client id, or
endpoint URL is exposed.

Intentionally **unauthenticated** — the endpoint exists so a gateway
that does not yet have a valid bearer can still discover the posture
and react appropriately.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..context import app_ctx
from ..settings import Settings, get_settings

router = APIRouter(tags=["auth-info"])


@router.get("/auth-info")
async def get_auth_info(settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    """Return non-secret upstream auth posture.

    Fields
    ------
    dev_bypass : bool
        When ``True``, ``/mcp-gateway/config`` and the chat/message
        endpoints accept unauthenticated calls.  Consumer gateways
        should treat this as "anything-goes local dev mode".
    identity_resolver_configured : bool
        When ``True``, a concrete :class:`OrchidIdentityResolver` has
        been wired at startup.  When ``False``, dev-bypass is required
        because no resolver can validate real tokens.
    """
    return {
        "dev_bypass": settings.dev_auth_bypass,
        "identity_resolver_configured": app_ctx.identity_resolver is not None,
    }
