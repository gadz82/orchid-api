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

from orchid_ai.core.auth_config import (
    OrchidAuthExchangeClient,
    OrchidUpstreamOAuthConfig,
)

from ..context import app_ctx
from ..settings import Settings, get_settings

router = APIRouter(tags=["auth-info"])


def _refresh_via_api_available(resolved: OrchidUpstreamOAuthConfig) -> bool:
    """Advertise ``refresh_via_api`` only when the plumbing actually
    works end-to-end.

    The check has three layers:

    1. Provider opt-in (``resolved.refresh_via_api``) — operator has
       to explicitly enable the feature in ``orchid.yml``.
    2. Exchange client wired — the router holds a concrete
       :class:`OrchidAuthExchangeClient` instance.
    3. Client overrides ``refresh_token`` — the default method on
       the ABC raises :class:`NotImplementedError`, so comparing the
       method identity against the ABC's guarantees we only advertise
       the feature when a real implementation exists.

    Skipping (3) would make the ``/auth-info`` output lie when an
    operator wires an exchange client written pre-Phase-4: the flag
    would be ``true`` but ``/auth/refresh-token`` would 503.
    """
    if not resolved.refresh_via_api:
        return False
    client = app_ctx.auth_exchange_client
    if client is None:
        return False
    return type(client).refresh_token is not OrchidAuthExchangeClient.refresh_token


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
    # Platform domain (e.g. ``mytenant.example.com``) that
    # downstream consumers should attach as ``X-Auth-Domain`` on
    # upstream requests.  Distinct from the user's email domain —
    # see :class:`orchid_ai.OrchidUpstreamOAuthConfig` for semantics.
    auth_domain: str | None = None
    # Optional JSON-path hints for non-OIDC userinfo responses. See
    # :class:`orchid_ai.OrchidUpstreamOAuthConfig` for semantics.
    userinfo_sub_path: str | None = None
    userinfo_email_path: str | None = None
    # When True, downstream OAuth clients should POST the upstream
    # authorization code to orchid-api's ``/auth/exchange-code``
    # instead of exchanging directly with the IdP — the
    # ``client_secret`` lives only on orchid-api (Phase 2 boundary).
    exchange_via_api: bool = False
    # When True, downstream OAuth clients should POST the upstream
    # access token to orchid-api's ``/auth/resolve-identity`` instead
    # of hitting the upstream ``userinfo_endpoint`` themselves.  The
    # gateway then drops its own ``userinfo_endpoint`` + JSON-path
    # hint configuration — orchid-api's already-wired identity
    # resolver does the work (Phase 4 boundary).
    resolve_via_api: bool = False
    # When True, orchid-api exposes ``POST /auth/refresh-token`` as
    # the refresh-grant equivalent of ``/auth/exchange-code``.  The
    # downstream consumer presents its upstream ``refresh_token`` and
    # orchid-api performs the upstream exchange with the
    # ``client_secret`` it holds.  Phase 4 complement to
    # :attr:`exchange_via_api` — when both are enabled the downstream
    # gateway never hits the upstream ``token_endpoint`` directly.
    refresh_via_api: bool = False


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
async def get_auth_info(
    domain: str | None = None,
    settings: Settings = Depends(get_settings),
) -> AuthInfoResponse:
    """Return non-secret upstream auth posture + OAuth discovery.

    The optional ``domain`` query parameter lets multi-tenant
    front-ends pass the user-supplied platform host so the wired
    :class:`OrchidAuthConfigProvider` can build tenant-scoped URLs
    (e.g. each end-user types their own ``mycompany.example.com`` at
    login and the same orchid-api routes the request to that
    tenant's IdP).  Single-tenant deployments call ``/auth-info``
    without it and the provider returns its operator-level fixed
    config.
    """
    oauth_block: AuthInfoOAuth | None = None
    if app_ctx.auth_config_provider is not None:
        resolved = app_ctx.auth_config_provider.get_oauth_config(domain=domain)
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
                # Advertise server-side exchange only when a client
                # is actually wired — otherwise the flag lies about
                # orchid-api's real capabilities and downstream
                # clients would hit a 503 on ``/auth/exchange-code``.
                exchange_via_api=(resolved.exchange_via_api and app_ctx.auth_exchange_client is not None),
                # Same gating logic: advertise the
                # ``/auth/resolve-identity`` endpoint only when an
                # :class:`OrchidIdentityResolver` is wired.  A bare
                # dev-bypass deployment has no resolver, so the
                # endpoint would 503.
                resolve_via_api=(resolved.resolve_via_api and app_ctx.identity_resolver is not None),
                # Phase 4 refresh flag gates on **three** conditions:
                # provider opted in, exchange client wired, AND the
                # wired client's class actually overrides
                # ``refresh_token``.  Without the third check we'd
                # advertise a feature that 503s mid-request — the
                # default ABC implementation raises
                # NotImplementedError.  We inspect the class method
                # identity rather than calling it so a 'feature
                # probe' never hits the upstream IdP.
                refresh_via_api=_refresh_via_api_available(resolved),
            )
    return AuthInfoResponse(
        dev_bypass=settings.dev_auth_bypass,
        identity_resolver_configured=app_ctx.identity_resolver is not None,
        oauth=oauth_block,
    )
