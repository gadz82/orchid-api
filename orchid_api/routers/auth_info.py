"""``GET /auth-info`` — public auth posture + upstream-OAuth discovery.

Consumed by downstream OAuth clients (``orchid-mcp``, Next.js
frontends) at startup so they can auto-configure against the
operator's ``orchid.yml`` instead of duplicating endpoint URLs +
``client_id`` across their own env vars.

Two kinds of data live here, both strictly **non-secret**:

1. Posture — ``dev_bypass`` flag and whether an
   :class:`OrchidIdentityResolver` is wired.  Consumers use it to
   validate that their own auth-mode matches upstream requirements.
2. Upstream OAuth discovery — endpoints (authorization / token /
   userinfo / issuer) and the **public** ``client_id``, emitted when
   an :class:`OrchidAuthConfigProvider` is configured.  Never
   includes ``client_secret``, user tokens, or refresh tokens — those
   stay on the server(s) that legitimately hold them.

Intentionally **unauthenticated** — the endpoint exists so a gateway
that does not yet have a valid bearer can still discover the posture
and react appropriately.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..context import app_ctx
from ..settings import Settings, get_settings

router = APIRouter(tags=["auth-info"])


class AuthInfoOAuth(BaseModel):
    """Upstream-OAuth discovery block — mirrors
    :class:`orchid_ai.OrchidUpstreamOAuthConfig` over the wire.
    """

    issuer_url: str
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    userinfo_endpoint: str | None = None
    scope: str = ""
    # Platform domain (e.g. ``mytenant.docebosaas.com``) that
    # downstream consumers should attach as ``X-Auth-Domain`` on
    # upstream requests.  Distinct from the user's email domain —
    # see :class:`orchid_ai.OrchidUpstreamOAuthConfig` for semantics.
    auth_domain: str | None = None
    # Optional JSON-path hints for non-OIDC userinfo responses. See
    # :class:`orchid_ai.OrchidUpstreamOAuthConfig` for semantics.
    userinfo_sub_path: str | None = None
    userinfo_email_path: str | None = None


class AuthInfoResponse(BaseModel):
    """Response shape for ``GET /auth-info``.

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
    oauth : AuthInfoOAuth | None
        Present when an :class:`OrchidAuthConfigProvider` is wired AND
        it resolves to a non-None config.  Downstream OAuth clients
        auto-configure from this block.  ``None`` means "no upstream
        discovery available — fall back to your own env-var overrides
        or refuse to start".
    """

    dev_bypass: bool
    identity_resolver_configured: bool
    oauth: AuthInfoOAuth | None = None


@router.get("/auth-info", response_model=AuthInfoResponse)
async def get_auth_info(settings: Settings = Depends(get_settings)) -> AuthInfoResponse:
    """Return non-secret upstream auth posture + OAuth discovery."""
    oauth_block: AuthInfoOAuth | None = None
    if app_ctx.auth_config_provider is not None:
        resolved = app_ctx.auth_config_provider.get_oauth_config()
        if resolved is not None:
            oauth_block = AuthInfoOAuth(
                issuer_url=resolved.issuer_url,
                authorization_endpoint=resolved.authorization_endpoint,
                token_endpoint=resolved.token_endpoint,
                client_id=resolved.client_id,
                userinfo_endpoint=resolved.userinfo_endpoint,
                scope=resolved.scope,
                auth_domain=resolved.auth_domain,
                userinfo_sub_path=resolved.userinfo_sub_path,
                userinfo_email_path=resolved.userinfo_email_path,
            )
    return AuthInfoResponse(
        dev_bypass=settings.dev_auth_bypass,
        identity_resolver_configured=app_ctx.identity_resolver is not None,
        oauth=oauth_block,
    )
