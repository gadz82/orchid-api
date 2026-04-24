"""Tests for ``orchid_api.routers.auth_exchange`` — the Phase 2
server-side OAuth code exchange proxy.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from orchid_ai.core.auth_config import (
    OrchidAuthExchangeClient,
    OrchidAuthExchangeError,
    OrchidUpstreamTokenResponse,
)

from orchid_api.context import app_ctx
from orchid_api.routers.auth_exchange import (
    ExchangeCodeRequest,
    ExchangeCodeResponse,
    exchange_code,
)


class _StubExchange(OrchidAuthExchangeClient):
    """Configurable stub used across happy-path + error tests."""

    def __init__(
        self,
        *,
        success: OrchidUpstreamTokenResponse | None = None,
        error: OrchidAuthExchangeError | None = None,
    ) -> None:
        self.success = success
        self.error = error
        self.calls: list[dict[str, str | None]] = []

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> OrchidUpstreamTokenResponse:
        self.calls.append({"code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier})
        if self.error is not None:
            raise self.error
        assert self.success is not None
        return self.success


@pytest.fixture
def reset_exchange_client():
    original = app_ctx.auth_exchange_client
    try:
        yield
    finally:
        app_ctx.auth_exchange_client = original


class TestExchangeCodeEndpoint:
    @pytest.mark.asyncio
    async def test_503_when_no_client_configured(self, reset_exchange_client):
        app_ctx.auth_exchange_client = None
        req = ExchangeCodeRequest(code="c", redirect_uri="http://cb", code_verifier="v")
        with pytest.raises(HTTPException) as exc:
            await exchange_code(req)
        assert exc.value.status_code == 503
        assert "not configured" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_happy_path_returns_token_verbatim(self, reset_exchange_client):
        stub = _StubExchange(
            success=OrchidUpstreamTokenResponse(
                access_token="at-xyz",
                refresh_token="rt-abc",
                expires_in=3600,
                scope="api",
            )
        )
        app_ctx.auth_exchange_client = stub

        result = await exchange_code(
            ExchangeCodeRequest(
                code="the-code",
                redirect_uri="http://cb",
                code_verifier="the-verifier",
            )
        )

        assert isinstance(result, ExchangeCodeResponse)
        assert result.access_token == "at-xyz"
        assert result.refresh_token == "rt-abc"
        assert result.expires_in == 3600
        assert result.scope == "api"
        assert stub.calls == [
            {
                "code": "the-code",
                "redirect_uri": "http://cb",
                "code_verifier": "the-verifier",
            }
        ]

    @pytest.mark.asyncio
    async def test_upstream_4xx_becomes_400(self, reset_exchange_client):
        """RFC 6749 §5.2 errors (invalid_grant, invalid_client, …) map to 400."""
        app_ctx.auth_exchange_client = _StubExchange(error=OrchidAuthExchangeError("invalid_grant", status_code=400))
        with pytest.raises(HTTPException) as exc:
            await exchange_code(ExchangeCodeRequest(code="bad-code", redirect_uri="http://cb", code_verifier="v"))
        assert exc.value.status_code == 400
        assert "invalid_grant" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_upstream_5xx_becomes_502(self, reset_exchange_client):
        """Upstream 5xx must surface as 502 so downstream can distinguish
        'IdP is having a bad day' from 'your payload is wrong'."""
        app_ctx.auth_exchange_client = _StubExchange(error=OrchidAuthExchangeError("upstream broke", status_code=503))
        with pytest.raises(HTTPException) as exc:
            await exchange_code(ExchangeCodeRequest(code="c", redirect_uri="http://cb"))
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_unreachable_upstream_becomes_502(self, reset_exchange_client):
        """Error with status_code=0 (didn't reach the IdP) becomes 502."""
        app_ctx.auth_exchange_client = _StubExchange(error=OrchidAuthExchangeError("connection refused"))
        with pytest.raises(HTTPException) as exc:
            await exchange_code(ExchangeCodeRequest(code="c", redirect_uri="http://cb"))
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_omits_optional_fields(self, reset_exchange_client):
        """A response without refresh_token / expires_in / scope is still valid."""
        app_ctx.auth_exchange_client = _StubExchange(success=OrchidUpstreamTokenResponse(access_token="only-at"))
        result = await exchange_code(ExchangeCodeRequest(code="c", redirect_uri="http://cb"))
        assert result.access_token == "only-at"
        assert result.token_type == "Bearer"
        assert result.refresh_token is None
        assert result.expires_in is None
        assert result.scope is None

    def test_endpoint_signature_has_no_auth_dep(self):
        """The exchange endpoint is intentionally unauthenticated — its
        protection is PKCE + upstream code binding, not an orchid-api
        bearer token.  A regression here would silently break every
        downstream MCP client (they don't have an orchid-api bearer to
        present).
        """
        import inspect

        sig = inspect.signature(exchange_code)
        assert set(sig.parameters.keys()) == {"request"}


class TestExchangeCodeRequestValidation:
    def test_requires_code(self):
        with pytest.raises(Exception):
            ExchangeCodeRequest(redirect_uri="http://cb")  # type: ignore[call-arg]

    def test_requires_redirect_uri(self):
        with pytest.raises(Exception):
            ExchangeCodeRequest(code="c")  # type: ignore[call-arg]

    def test_code_verifier_optional(self):
        req = ExchangeCodeRequest(code="c", redirect_uri="http://cb")
        assert req.code_verifier is None

    def test_rejects_empty_code(self):
        with pytest.raises(Exception):
            ExchangeCodeRequest(code="", redirect_uri="http://cb")
