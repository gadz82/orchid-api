"""Tests for ``orchid_api.routers.auth_info`` — public posture probe
and upstream-OAuth discovery.

Covers:
- Bare posture (no :class:`OrchidIdentityResolver`, no
  :class:`OrchidAuthConfigProvider`) — the original contract.
- :attr:`AuthInfoResponse.oauth` populated when a provider is wired
  and resolves to a non-None config.
- :attr:`AuthInfoResponse.oauth` stays ``None`` when the provider is
  wired but resolves to None (e.g. operator declared the class but
  hasn't set ``AUTH_DOMAIN``).
- The endpoint remains **unauthenticated** — it must not gain an
  ``auth_context`` dependency by accident.
"""

from __future__ import annotations

import pytest

from orchid_ai.core.auth_config import (
    OrchidAuthConfigProvider,
    OrchidUpstreamOAuthConfig,
)

from orchid_api.context import app_ctx
from orchid_api.routers.auth_info import (
    AuthInfoOAuth,
    AuthInfoResponse,
    get_auth_info,
)
from orchid_api.settings import Settings


class DummyResolver:
    """Minimal stand-in for :class:`OrchidIdentityResolver` — presence marker."""


class _FixedProvider(OrchidAuthConfigProvider):
    """Test provider that returns a pre-baked config verbatim.

    The ``domain`` kwarg is accepted (matching the ABC) but ignored —
    these tests focus on the gate / posture logic, not on multi-tenant
    URL templating.  Multi-tenant tests live alongside the concrete
    consumer impls (e.g. ``docebo/tests/test_auth_config.py``).
    """

    def __init__(self, config: OrchidUpstreamOAuthConfig | None) -> None:
        self._config = config
        self.calls: list[str | None] = []

    def get_oauth_config(
        self,
        *,
        domain: str | None = None,
    ) -> OrchidUpstreamOAuthConfig | None:
        self.calls.append(domain)
        return self._config


@pytest.fixture
def reset_app_ctx():
    """Snapshot + restore ``identity_resolver``, ``auth_config_provider``
    and ``auth_exchange_client`` on ``app_ctx`` so tests don't leak
    state into each other.
    """
    original_resolver = app_ctx.identity_resolver
    original_provider = app_ctx.auth_config_provider
    original_exchange = app_ctx.auth_exchange_client
    try:
        yield
    finally:
        app_ctx.identity_resolver = original_resolver
        app_ctx.auth_config_provider = original_provider
        app_ctx.auth_exchange_client = original_exchange


class TestAuthInfoEndpoint:
    @pytest.mark.asyncio
    async def test_dev_bypass_true_no_resolver(self, reset_app_ctx):
        """Posture-only response when the provider is unset."""
        settings = Settings(dev_auth_bypass=True)
        app_ctx.identity_resolver = None
        app_ctx.auth_config_provider = None

        result = await get_auth_info(settings=settings)

        assert isinstance(result, AuthInfoResponse)
        assert result.dev_bypass is True
        assert result.identity_resolver_configured is False
        assert result.oauth is None

    @pytest.mark.asyncio
    async def test_dev_bypass_false_with_resolver(self, reset_app_ctx):
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = DummyResolver()  # type: ignore[assignment]
        app_ctx.auth_config_provider = None

        result = await get_auth_info(settings=settings)

        assert result.dev_bypass is False
        assert result.identity_resolver_configured is True
        assert result.oauth is None

    @pytest.mark.asyncio
    async def test_dev_bypass_false_without_resolver(self, reset_app_ctx):
        """Degenerate but valid shape — useful signal for a misconfigured deploy."""
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_config_provider = None

        result = await get_auth_info(settings=settings)

        assert result.dev_bypass is False
        assert result.identity_resolver_configured is False
        assert result.oauth is None

    @pytest.mark.asyncio
    async def test_oauth_block_populated_when_provider_resolves(self, reset_app_ctx):
        """When the provider returns a config, it flows through verbatim."""
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = DummyResolver()  # type: ignore[assignment]
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/oauth2/authorize",
                token_endpoint="https://acme.example.com/oauth2/token",
                client_id="mcp-gateway",
                userinfo_endpoint="https://acme.example.com/manage/v1/user/session",
                scope="api",
            ),
        )

        result = await get_auth_info(settings=settings)

        assert result.oauth is not None
        assert isinstance(result.oauth, AuthInfoOAuth)
        assert result.oauth.issuer_url == "https://acme.example.com"
        assert result.oauth.authorization_endpoint == ("https://acme.example.com/oauth2/authorize")
        assert result.oauth.token_endpoint == "https://acme.example.com/oauth2/token"
        assert result.oauth.client_id == "mcp-gateway"
        assert result.oauth.userinfo_endpoint == ("https://acme.example.com/manage/v1/user/session")
        assert result.oauth.scope == "api"

    @pytest.mark.asyncio
    async def test_oauth_block_omitted_when_provider_returns_none(self, reset_app_ctx):
        """Provider wired but declines to emit a config (e.g. missing domain)."""
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = DummyResolver()  # type: ignore[assignment]
        app_ctx.auth_config_provider = _FixedProvider(None)

        result = await get_auth_info(settings=settings)

        assert result.oauth is None
        # Posture fields still present.
        assert result.identity_resolver_configured is True

    @pytest.mark.asyncio
    async def test_oauth_block_passes_through_auth_domain(self, reset_app_ctx):
        """The platform ``auth_domain`` is shipped verbatim — downstream
        OAuth clients use it as ``X-Auth-Domain`` on upstream requests.
        """
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                auth_domain="acme.example.com",
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.auth_domain == "acme.example.com"

    @pytest.mark.asyncio
    async def test_oauth_block_passes_through_json_path_hints(self, reset_app_ctx):
        """Non-OIDC upstreams include ``userinfo_sub_path`` / ``..._email_path``
        so downstream OAuth clients can pluck claims from wrapped shapes
        (e.g. ``{"data": {"user_id", "email"}}``).
        """
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                userinfo_endpoint="https://acme.example.com/user/session",
                scope="api",
                userinfo_sub_path="data.user_id",
                userinfo_email_path="data.email",
            ),
        )

        result = await get_auth_info(settings=settings)

        assert result.oauth is not None
        assert result.oauth.userinfo_sub_path == "data.user_id"
        assert result.oauth.userinfo_email_path == "data.email"

    @pytest.mark.asyncio
    async def test_exchange_via_api_true_only_when_client_wired(self, reset_app_ctx):
        """``exchange_via_api=True`` in discovery means "orchid-api will
        handle the code exchange".  That claim must be backed by a real
        :class:`OrchidAuthExchangeClient` in ``app_ctx`` — otherwise
        downstream clients would POST to ``/auth/exchange-code`` and hit
        a 503 surprise.
        """
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        # Provider claims the feature, but no exchange client is wired:
        app_ctx.auth_exchange_client = None
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                exchange_via_api=True,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.exchange_via_api is False  # gated off

    @pytest.mark.asyncio
    async def test_exchange_via_api_true_when_both_wired(self, reset_app_ctx):
        """With a client wired AND the provider opting in, the flag flows
        through to downstream clients.
        """

        class _StubExchange:
            async def exchange_code(self, **_kw):  # pragma: no cover (unused)
                raise NotImplementedError

        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_exchange_client = _StubExchange()  # type: ignore[assignment]
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                exchange_via_api=True,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.exchange_via_api is True

    @pytest.mark.asyncio
    async def test_exchange_via_api_false_when_provider_opts_out(self, reset_app_ctx):
        """Even with a wired exchange client, a provider can set
        ``exchange_via_api=False`` to keep Phase 1 behaviour.
        """

        class _StubExchange:
            async def exchange_code(self, **_kw):  # pragma: no cover (unused)
                raise NotImplementedError

        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_exchange_client = _StubExchange()  # type: ignore[assignment]
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                exchange_via_api=False,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.exchange_via_api is False

    @pytest.mark.asyncio
    async def test_oauth_block_preserves_none_userinfo_endpoint(self, reset_app_ctx):
        """A provider may omit the userinfo endpoint (pure OAuth2 IdPs)."""
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://idp.example.com",
                authorization_endpoint="https://idp.example.com/authorize",
                token_endpoint="https://idp.example.com/token",
                client_id="client-xyz",
                userinfo_endpoint=None,
                scope="",
            ),
        )

        result = await get_auth_info(settings=settings)

        assert result.oauth is not None
        assert result.oauth.userinfo_endpoint is None
        assert result.oauth.scope == ""

    @pytest.mark.asyncio
    async def test_resolve_via_api_true_only_when_resolver_wired(self, reset_app_ctx):
        """Phase 4 parallel of ``exchange_via_api``: the provider
        claims the feature, but without an :class:`OrchidIdentityResolver`
        the ``/auth/resolve-identity`` endpoint would 503.  Gate the
        flag off in that case so downstream doesn't get a surprise.
        """
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                resolve_via_api=True,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.resolve_via_api is False  # gated off

    @pytest.mark.asyncio
    async def test_resolve_via_api_true_when_both_wired(self, reset_app_ctx):
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = DummyResolver()  # type: ignore[assignment]
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                resolve_via_api=True,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.resolve_via_api is True

    @pytest.mark.asyncio
    async def test_refresh_via_api_false_when_no_exchange_client(self, reset_app_ctx):
        """Provider claims ``refresh_via_api=true`` but no client is
        wired — gate off so downstream doesn't get a 503 surprise.
        """
        from orchid_ai.core.auth_config import (
            OrchidAuthExchangeClient,
            OrchidUpstreamTokenResponse,
        )

        class _StubExchange(OrchidAuthExchangeClient):
            async def exchange_code(self, **_kw) -> OrchidUpstreamTokenResponse:  # type: ignore[override]
                raise NotImplementedError  # pragma: no cover

            async def refresh_token(self, **_kw) -> OrchidUpstreamTokenResponse:  # type: ignore[override]
                raise NotImplementedError  # pragma: no cover

        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_exchange_client = None
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                refresh_via_api=True,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.refresh_via_api is False

    @pytest.mark.asyncio
    async def test_refresh_via_api_false_when_client_lacks_refresh_impl(self, reset_app_ctx):
        """Exchange client is wired but its class inherits the default
        :meth:`OrchidAuthExchangeClient.refresh_token` (raises
        :class:`NotImplementedError`).  Gate off — advertising the
        feature would lie about orchid-api's real capabilities.
        """
        from orchid_ai.core.auth_config import (
            OrchidAuthExchangeClient,
            OrchidUpstreamTokenResponse,
        )

        class _ExchangeOnly(OrchidAuthExchangeClient):
            async def exchange_code(self, **_kw) -> OrchidUpstreamTokenResponse:  # type: ignore[override]
                raise NotImplementedError  # pragma: no cover

        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_exchange_client = _ExchangeOnly()
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                refresh_via_api=True,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.refresh_via_api is False  # gated off by method-identity check

    @pytest.mark.asyncio
    async def test_refresh_via_api_true_when_fully_wired(self, reset_app_ctx):
        """All three conditions met — provider opts in, client is
        wired, and the client actually overrides :meth:`refresh_token`.
        """
        from orchid_ai.core.auth_config import (
            OrchidAuthExchangeClient,
            OrchidUpstreamTokenResponse,
        )

        class _FullExchange(OrchidAuthExchangeClient):
            async def exchange_code(self, **_kw) -> OrchidUpstreamTokenResponse:  # type: ignore[override]
                raise NotImplementedError  # pragma: no cover

            async def refresh_token(self, **_kw) -> OrchidUpstreamTokenResponse:  # type: ignore[override]
                raise NotImplementedError  # pragma: no cover

        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = None
        app_ctx.auth_exchange_client = _FullExchange()
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                refresh_via_api=True,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.refresh_via_api is True

    @pytest.mark.asyncio
    async def test_resolve_via_api_false_when_provider_opts_out(self, reset_app_ctx):
        """Operator can keep the Phase-1 userinfo-from-gateway path
        even with a resolver wired — useful during staged rollouts."""
        settings = Settings(dev_auth_bypass=False)
        app_ctx.identity_resolver = DummyResolver()  # type: ignore[assignment]
        app_ctx.auth_config_provider = _FixedProvider(
            OrchidUpstreamOAuthConfig(
                issuer_url="https://acme.example.com",
                authorization_endpoint="https://acme.example.com/authorize",
                token_endpoint="https://acme.example.com/token",
                client_id="c",
                resolve_via_api=False,
            ),
        )
        result = await get_auth_info(settings=settings)
        assert result.oauth is not None
        assert result.oauth.resolve_via_api is False

    @pytest.mark.asyncio
    async def test_no_auth_required_on_endpoint(self):
        """Endpoint must be unauthenticated — no ``get_auth_context`` dep.

        The endpoint accepts a ``domain`` query parameter (multi-tenant
        per-request hint) and a ``settings`` dependency injection.
        Neither is an auth gate, but they must be the only inputs —
        a regression that adds e.g. an ``auth_context`` dep would
        break the public discovery contract.
        """
        import inspect

        sig = inspect.signature(get_auth_info)
        param_names = set(sig.parameters.keys())
        assert "auth" not in param_names
        assert "auth_context" not in param_names
        assert param_names == {"domain", "settings"}
