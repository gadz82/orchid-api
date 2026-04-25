"""``POST /auth/resolve-identity`` — server-side proxy for the
upstream identity bridge.

Rationale (Phase 4 boundary).  In Phase 1 / 2 / 3 the MCP gateway
still held one piece of upstream config the rest of orchid-api
didn't: the userinfo endpoint URL + JSON-path hints for non-OIDC
shapes (e.g. Docebo's ``data.user_id``).  Whenever a user completed
the OAuth dance the gateway hit the upstream ``userinfo_endpoint``
itself to build an :type:`OrchidIdentity`.  That split the
identity-extraction logic across the gateway and any custom
scripted resolver the operator wrote, and required the gateway to
know tenant-shape details the rest of the stack already knew.

Phase 4 exposes the existing :class:`OrchidIdentityResolver`
(already wired into :attr:`AppContext.identity_resolver`) over an
HTTP endpoint.  Downstream OAuth clients POST the raw upstream
access token and get back a normalised identity payload.  The
gateway then stops needing ``userinfo_endpoint`` /
``userinfo_sub_path`` / ``userinfo_email_path`` settings — the same
custom :class:`OrchidIdentityResolver` orchid-api already runs at
every MCP request does double-duty as the gateway's identity
bridge.

The endpoint is deliberately **unauthenticated** — its protection
is that the caller must already possess a valid upstream access
token, which is itself the proof of identity.  Echoing identity
fields from the token adds no new attack surface: anyone who can
call this endpoint with a valid token could call the upstream
userinfo endpoint directly and learn exactly the same thing.
Operators wanting defence-in-depth (rate limits, mTLS, allow-list)
should layer it at the reverse-proxy tier.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchid_ai.core.identity import OrchidIdentityError

from ..context import app_ctx
from ..settings import Settings, get_settings
from fastapi import Depends

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth-identity"])


class ResolveIdentityRequest(BaseModel):
    """Body of ``POST /auth/resolve-identity``.

    ``access_token`` is the raw upstream access token (OAuth2
    bearer) obtained via the authorization-code dance.  The
    downstream consumer typically received it either directly from
    the upstream ``token_endpoint`` or (Phase 2) from
    :mod:`orchid_api.routers.auth_exchange`.

    ``auth_domain`` lets a multi-tenant operator override the
    operator-level default domain on a per-call basis — useful when
    a single orchid-api deployment serves several platform tenants
    under one :class:`OrchidIdentityResolver`.  When omitted, the
    resolver uses ``settings.auth_domain`` as the default.  Identity
    resolvers that don't care about domains (OIDC-homogeneous
    stacks) can ignore both fields.
    """

    access_token: str = Field(..., min_length=1, description="Upstream access token.")
    auth_domain: str | None = Field(
        None,
        description=(
            "Platform / tenant domain to resolve against (e.g. "
            "``acme.example.com``).  When omitted, orchid-api falls back to "
            "``settings.auth_domain``."
        ),
    )


class ResolveIdentityResponse(BaseModel):
    """Normalised identity payload.

    Mirrors the fields the MCP gateway's :type:`OrchidIdentity`
    needs verbatim — ``subject`` / ``bearer`` / ``auth_domain`` —
    plus optional ``email`` for downstream UX (e.g. a
    'signed-in as ...' hint in the gateway logs).  Extension
    fields live under ``extra``, mirroring
    :attr:`OrchidAuthContext.extra`, so platform-specific resolvers
    can expose additional claims without requiring a new endpoint
    or a schema migration on the wire.
    """

    subject: str
    bearer: str
    auth_domain: str = ""
    email: str = ""
    extra: dict[str, object] = Field(default_factory=dict)


@router.post("/auth/resolve-identity", response_model=ResolveIdentityResponse)
async def resolve_identity(
    request: ResolveIdentityRequest,
    settings: Settings = Depends(get_settings),
) -> ResolveIdentityResponse:
    """Resolve an upstream access token into a normalised identity.

    Returns
    -------
    ResolveIdentityResponse
        Subject + bearer (usually echoes the input access token) +
        optional domain / email + resolver-specific extras.

    Raises
    ------
    HTTPException
        ``503`` when no :class:`OrchidIdentityResolver` is wired.
        ``401`` when the resolver explicitly rejected the token
        (e.g. expired or invalidated at the upstream).
        ``502`` when the resolver couldn't reach the upstream.
    """
    resolver = app_ctx.identity_resolver
    if resolver is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Identity resolver is not configured.  Wire an "
                "``OrchidIdentityResolver`` subclass via "
                "``auth.identity_resolver_class`` in ``orchid.yml``."
            ),
        )
    domain = request.auth_domain or settings.auth_domain
    try:
        auth_ctx = await resolver.resolve(domain=domain, bearer_token=request.access_token)
    except OrchidIdentityError as err:
        # 4xx from the upstream → token is bad.  5xx / 0 → upstream
        # misbehaving; surface as 502 so the caller distinguishes
        # "user needs to re-authenticate" from "orchid-api side is
        # flaky".  The resolver ABC only guarantees ``status_code``
        # semantics informally (see :class:`OrchidIdentityError`);
        # map anything under 500 as 401 — the safer read.
        status = 401 if 0 < err.status_code < 500 else 502
        logger.warning(
            "[auth-identity] resolver rejected token: status=%s detail=%s",
            err.status_code,
            err,
        )
        raise HTTPException(status_code=status, detail=str(err)) from err

    # :class:`OrchidAuthContext` uses ``user_id`` as the stable
    # per-user key.  Map it to ``subject`` for wire parity with the
    # gateway's :type:`OrchidIdentity` (which uses OAuth-style
    # naming rather than orchid-native naming).
    extra = dict(auth_ctx.extra) if auth_ctx.extra else {}
    email = str(extra.pop("email", "")) if "email" in extra else ""
    # Prefer the resolver-provided domain (stored either as a top-level
    # subclass attribute like :class:`DoceboAuthContext.domain` or
    # under ``extra['domain']``) and fall back to the caller's hint so
    # a resolver that doesn't carry the field still round-trips it.
    resolver_domain = getattr(auth_ctx, "domain", None) or extra.pop("domain", "") or domain
    return ResolveIdentityResponse(
        subject=auth_ctx.user_id,
        bearer=auth_ctx.access_token,
        auth_domain=str(resolver_domain or ""),
        email=email,
        extra=extra,
    )
