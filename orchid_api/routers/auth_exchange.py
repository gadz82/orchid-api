"""``POST /auth/exchange-code`` + ``POST /auth/refresh-token`` —
secret-bearing proxies for the upstream-OAuth authorization-code
and refresh-token grant types.

Rationale (Phase 2 + Phase 4 boundaries).  Downstream OAuth clients
(the MCP gateway and Next.js frontends) used to hold their own
copy of ``client_secret`` so they could exchange authorization
codes (Phase 1) and refresh tokens (pre-Phase-4) with the upstream
IdP directly.  That scattered the secret across three places.
Centralising both grant types here means the secret exists in
exactly one process (orchid-api), and every other component runs as
a public PKCE client.

The endpoints are deliberately **unauthenticated** — their
protection relies on the natural guards already present in the
OAuth grant types:

1. For the code grant: the ``code`` is single-use and time-limited
   at the upstream; PKCE binds the code to a verifier the attacker
   cannot guess; the upstream itself will reject a ``code`` that
   was not issued for our ``client_id`` / ``redirect_uri``.
2. For the refresh grant: the ``refresh_token`` is itself the
   bearer-style credential; presenting it is authentication.
   Upstreams rotate it on every refresh (OAuth 2.1), so a stolen
   token is useful for at most one refresh cycle.

A malicious caller who can construct a valid ``(code, verifier)``
or hold a valid ``refresh_token`` has already compromised the
user's session at a deeper level; routing through orchid-api adds
no new surface.  If you want defence-in-depth (e.g. rate limiting,
allow-listed IPs, mTLS), wire it at the reverse proxy layer — we
deliberately don't bake it in here because deployment topologies
vary.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchid_ai.core.auth_config import OrchidAuthExchangeError

from ..context import app_ctx

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth-exchange"])


class ExchangeCodeRequest(BaseModel):
    """Body of ``POST /auth/exchange-code``.

    Mirrors RFC 6749 §4.1.3 (authorization-code grant) plus the
    PKCE ``code_verifier`` from RFC 7636.  Downstream clients build
    this from the callback they received from the upstream IdP.
    """

    code: str = Field(..., min_length=1, description="Upstream authorization code.")
    redirect_uri: str = Field(
        ...,
        min_length=1,
        description=(
            "Redirect URI the downstream consumer registered at the upstream; "
            "must byte-for-byte match what was sent on ``/authorize``."
        ),
    )
    code_verifier: str | None = Field(
        None,
        description=(
            "PKCE verifier matching the challenge sent on ``/authorize``. "
            "Required when the upstream enforces PKCE (which includes every "
            "MCP 2025-03-26 client)."
        ),
    )


class ExchangeCodeResponse(BaseModel):
    """Response from a successful exchange.

    Shape mirrors RFC 6749 §5.1 — values are forwarded verbatim from
    the upstream token response.  Downstream consumers stash
    ``access_token`` and (if provided) ``refresh_token`` inside their
    own session records.
    """

    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_in: int | None = None
    scope: str | None = None


@router.post("/auth/exchange-code", response_model=ExchangeCodeResponse)
async def exchange_code(request: ExchangeCodeRequest) -> ExchangeCodeResponse:
    """Proxy an upstream-OAuth code exchange.

    Returns
    -------
    ExchangeCodeResponse
        Normalised token payload from the upstream IdP.

    Raises
    ------
    HTTPException
        ``503`` when no :class:`OrchidAuthExchangeClient` is wired.
        ``400`` when the upstream rejected the exchange
        (``invalid_grant`` / ``invalid_client`` / …).
        ``502`` when the exchange failed without reaching the
        upstream (DNS, transport, misconfiguration).
    """
    if app_ctx.auth_exchange_client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth exchange proxy is not configured.  Wire an "
                "``OrchidAuthExchangeClient`` subclass via "
                "``auth.auth_exchange_client_class`` in ``orchid.yml``."
            ),
        )
    try:
        token = await app_ctx.auth_exchange_client.exchange_code(
            code=request.code,
            redirect_uri=request.redirect_uri,
            code_verifier=request.code_verifier,
        )
    except OrchidAuthExchangeError as err:
        # Map upstream failures (4xx) to 400 so downstream clients
        # can distinguish them from "proxy is broken".  5xx / 0
        # (unreachable) become 502.
        status = 400 if 400 <= err.status_code < 500 else 502
        logger.warning(
            "[auth-exchange] upstream rejected exchange: status=%s detail=%s",
            err.status_code,
            err,
        )
        raise HTTPException(status_code=status, detail=str(err)) from err
    return ExchangeCodeResponse(
        access_token=token.access_token,
        token_type=token.token_type,
        refresh_token=token.refresh_token,
        expires_in=token.expires_in,
        scope=token.scope,
    )


class RefreshTokenRequest(BaseModel):
    """Body of ``POST /auth/refresh-token``.

    Mirrors RFC 6749 §6 (refresh-token grant).  Downstream clients
    stash an upstream ``refresh_token`` at
    :meth:`exchange_code` / :meth:`refresh_token` time and post it
    here when the paired access token expires.  orchid-api exchanges
    it for a fresh pair using its copy of ``client_secret``.
    """

    refresh_token: str = Field(..., min_length=1, description="Upstream refresh token.")


@router.post("/auth/refresh-token", response_model=ExchangeCodeResponse)
async def refresh_token(request: RefreshTokenRequest) -> ExchangeCodeResponse:
    """Proxy an upstream-OAuth refresh-token grant.

    Shares :class:`ExchangeCodeResponse` with the code-exchange
    endpoint because the upstream token response shape is
    identical for both grant types (RFC 6749 §5.1).  Downstream
    clients treat both endpoints as drop-in replacements for the
    upstream ``token_endpoint``.

    Raises
    ------
    HTTPException
        ``503`` when no :class:`OrchidAuthExchangeClient` is wired
        **or** when the wired client hasn't implemented
        :meth:`refresh_token` (via :class:`NotImplementedError`).
        ``400`` when the upstream rejected the refresh
        (``invalid_grant`` / ``invalid_client`` / …).
        ``502`` when the refresh failed without reaching the
        upstream.
    """
    if app_ctx.auth_exchange_client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth exchange proxy is not configured.  Wire an "
                "``OrchidAuthExchangeClient`` subclass via "
                "``auth.auth_exchange_client_class`` in ``orchid.yml``."
            ),
        )
    try:
        token = await app_ctx.auth_exchange_client.refresh_token(
            refresh_token=request.refresh_token,
        )
    except NotImplementedError as err:
        # The wired client subclass exists but hasn't implemented
        # the refresh grant.  503 matches the "feature unwired"
        # semantics of the code-exchange endpoint — downstream
        # clients treat it the same way.
        logger.warning(
            "[auth-exchange] refresh_token not implemented on %s",
            type(app_ctx.auth_exchange_client).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth refresh proxy is not available — the wired "
                "``OrchidAuthExchangeClient`` does not implement "
                "``refresh_token``."
            ),
        ) from err
    except OrchidAuthExchangeError as err:
        status = 400 if 400 <= err.status_code < 500 else 502
        logger.warning(
            "[auth-exchange] upstream rejected refresh: status=%s detail=%s",
            err.status_code,
            err,
        )
        raise HTTPException(status_code=status, detail=str(err)) from err
    return ExchangeCodeResponse(
        access_token=token.access_token,
        token_type=token.token_type,
        refresh_token=token.refresh_token,
        expires_in=token.expires_in,
        scope=token.scope,
    )
