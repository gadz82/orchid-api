"""``POST /auth/exchange-code`` — secret-bearing proxy for the
upstream-OAuth authorization-code exchange.

Rationale (Phase 2 boundary).  Downstream OAuth clients (the MCP
gateway and Next.js frontends) used to hold their own copy of
``client_secret`` so they could exchange authorization codes with
the upstream IdP directly.  That scatters the secret across three
places.  Centralising the exchange here means the secret exists in
exactly one process (orchid-api), and every other component runs as
a public PKCE client.

The endpoint is deliberately **unauthenticated** — its protection
relies on the natural guard already present in the OAuth dance:

1. The ``code`` is single-use and time-limited at the upstream;
2. PKCE binds the code to a verifier the attacker cannot guess;
3. The upstream itself will reject a ``code`` that was not issued
   for our ``client_id`` / ``redirect_uri``.

A malicious caller who can construct a valid ``(code, verifier)``
pair has already compromised the user's browser and could have
completed the flow themselves; routing through orchid-api adds no
new surface.  If you want defence-in-depth (e.g. rate limiting,
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
