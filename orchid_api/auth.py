"""Authentication dependency (ADR-010)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import Depends, Header, HTTPException

from orchid_ai.core.identity import OrchidIdentityError
from orchid_ai.core.state import OrchidAuthContext

from .context import app_ctx
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)


async def _safe_warm_for_user(auth: OrchidAuthContext) -> None:
    """Fire-and-forget warm wrapper that swallows every exception.

    Used as the lazy backstop in :func:`get_auth_context` — frontends
    that don't yet call ``POST /session/warm`` still get warmed caches
    on the first authenticated request.  Must NEVER crash the request
    handler that scheduled it.
    """
    if app_ctx.orchid is None:
        return
    try:
        await app_ctx.orchid.session_warmer.warm_for_user(auth)
    except Exception as exc:
        logger.warning(
            "[Auth] Background MCP warm-up for tenant=%s user=%s raised: %s",
            auth.tenant_key,
            auth.user_id,
            exc,
        )


def _schedule_warm_for_user(auth: OrchidAuthContext) -> None:
    """Schedule :func:`_safe_warm_for_user` once per ``(tenant, user)``.

    Idempotency lives inside the warmer's own ``is_warmed`` cache, so a
    duplicate schedule is cheap; we still gate here to avoid creating
    needless background tasks on hot endpoints.
    """
    if app_ctx.orchid is None:
        return
    warmer = app_ctx.orchid.session_warmer
    if warmer.is_warmed(auth):
        return
    try:
        asyncio.create_task(_safe_warm_for_user(auth))
    except RuntimeError:
        # No running loop (extremely unlikely under FastAPI) — skip.
        return


async def get_auth_context(
    authorization: str = Header(..., description="Bearer <token>"),
    x_auth_domain: str | None = Header(None, alias="x-auth-domain", description="Platform domain (from frontend)"),
    settings: Settings = Depends(get_settings),
) -> OrchidAuthContext:
    """Resolve the Bearer token into a full OrchidAuthContext."""
    if settings.dev_auth_bypass:
        logger.info("[Auth] DEV_AUTH_BYPASS enabled — using dummy OrchidAuthContext")
        bypass_ctx = OrchidAuthContext(
            access_token="dev-token",
            tenant_key="99999",
            user_id="dev-user-00000000",
        )
        _schedule_warm_for_user(bypass_ctx)
        return bypass_ctx

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization[7:]

    if app_ctx.identity_resolver is None:
        raise HTTPException(status_code=503, detail="Identity resolver not configured")

    domain = x_auth_domain or settings.auth_domain

    try:
        auth_context = await app_ctx.identity_resolver.resolve(
            domain=domain,
            bearer_token=token,
        )
    except OrchidIdentityError as exc:
        # Log the full error (internal IdP URLs, upstream status codes,
        # etc.) but only tell the client a generic 401/403 — the
        # original message may leak internal hostnames.
        status = exc.status_code if exc.status_code in (401, 403) else 401
        detail = "Forbidden" if status == 403 else "Authentication failed"
        logger.warning("[Auth] Identity resolution failed (status=%d): %s", status, exc)
        raise HTTPException(status_code=status, detail=detail)

    if auth_context.is_expired:
        raise HTTPException(status_code=401, detail="Token is expired")

    _schedule_warm_for_user(auth_context)
    return auth_context
