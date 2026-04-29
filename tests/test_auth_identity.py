"""Tests for ``orchid_api.routers.auth_identity`` — Phase 4 upstream
identity-bridge proxy.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from orchid_ai.core.identity import OrchidIdentityError, OrchidIdentityResolver
from orchid_ai.core.state import OrchidAuthContext

from orchid_api.context import app_ctx
from orchid_api.routers.auth_identity import (
    ResolveIdentityRequest,
    ResolveIdentityResponse,
    resolve_identity,
)
from orchid_api.settings import Settings


class _StubResolver(OrchidIdentityResolver):
    """Configurable stub returning either an auth context or an error.

    Mirrors the real resolvers in shape: accepts ``(domain,
    bearer_token)``, yields an :class:`OrchidAuthContext`, and can
    carry ``email`` / ``domain`` through :attr:`OrchidAuthContext.extra`
    so the router's projection into ``ResolveIdentityResponse`` has
    realistic data to work with.
    """

    def __init__(
        self,
        *,
        context: OrchidAuthContext | None = None,
        error: OrchidIdentityError | None = None,
    ) -> None:
        self._context = context
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, domain: str, bearer_token: str) -> OrchidAuthContext:
        self.calls.append((domain, bearer_token))
        if self._error is not None:
            raise self._error
        assert self._context is not None
        return self._context


@pytest.fixture
def reset_identity_resolver():
    original = app_ctx.identity_resolver
    try:
        yield
    finally:
        app_ctx.identity_resolver = original


class TestResolveIdentityEndpoint:
    @pytest.mark.asyncio
    async def test_503_when_resolver_unwired(self, reset_identity_resolver):
        app_ctx.identity_resolver = None
        req = ResolveIdentityRequest(access_token="tok")
        with pytest.raises(HTTPException) as exc:
            await resolve_identity(req, settings=Settings())
        assert exc.value.status_code == 503
        assert "not configured" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_happy_path_projects_auth_context(self, reset_identity_resolver):
        """Base :class:`OrchidAuthContext` stores ``domain`` / ``email``
        under ``extra``; the endpoint projects them to the top-level
        response fields.
        """
        stub = _StubResolver(
            context=OrchidAuthContext(
                access_token="tok-xyz",
                tenant_key="195128",
                user_id="u-42",
                extra={"domain": "acme.example.com", "email": "a@b.c", "foo": "bar"},
            ),
        )
        app_ctx.identity_resolver = stub

        result = await resolve_identity(
            ResolveIdentityRequest(access_token="tok-xyz", auth_domain="acme.example.com"),
            settings=Settings(),
        )

        assert isinstance(result, ResolveIdentityResponse)
        assert result.subject == "u-42"
        assert result.bearer == "tok-xyz"
        assert result.auth_domain == "acme.example.com"
        assert result.email == "a@b.c"
        # Unknown extras round-trip verbatim so platform-specific
        # resolvers can surface additional claims.
        assert result.extra == {"foo": "bar"}
        # Resolver received the caller-specified domain, not the
        # operator-level default.
        assert stub.calls == [("acme.example.com", "tok-xyz")]

    @pytest.mark.asyncio
    async def test_omits_auth_domain_falls_back_to_settings(self, reset_identity_resolver):
        """When the caller doesn't specify a domain, orchid-api uses
        ``settings.auth_domain`` — the operator-level default.
        """
        stub = _StubResolver(
            context=OrchidAuthContext(access_token="tok", tenant_key="1", user_id="u-1"),
        )
        app_ctx.identity_resolver = stub
        await resolve_identity(
            ResolveIdentityRequest(access_token="tok"),
            settings=Settings(auth_domain="default.example.com"),
        )
        assert stub.calls == [("default.example.com", "tok")]

    @pytest.mark.asyncio
    async def test_prefers_subclass_domain_over_request_hint(self, reset_identity_resolver):
        """A platform-specific :class:`OrchidAuthContext` subclass carries
        the domain as a top-level attribute.  That wins over the caller's
        hint — the resolver knows which tenant the token actually belongs
        to.
        """

        class _SubCtx(OrchidAuthContext):
            def __init__(self, **kw):
                super().__init__(**kw)
                self.domain = "real-tenant.example.com"

        app_ctx.identity_resolver = _StubResolver(
            context=_SubCtx(access_token="tok", tenant_key="1", user_id="u-1"),
        )
        result = await resolve_identity(
            ResolveIdentityRequest(access_token="tok", auth_domain="hint.example.com"),
            settings=Settings(),
        )
        assert result.auth_domain == "real-tenant.example.com"

    @pytest.mark.asyncio
    async def test_resolver_4xx_becomes_401(self, reset_identity_resolver):
        """Upstream token rejection maps to 401 — the caller needs to
        re-authenticate, not retry.
        """
        app_ctx.identity_resolver = _StubResolver(
            error=OrchidIdentityError("expired token", status_code=401),
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_identity(
                ResolveIdentityRequest(access_token="bad"),
                settings=Settings(),
            )
        assert exc.value.status_code == 401
        assert "expired" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_resolver_5xx_becomes_502(self, reset_identity_resolver):
        app_ctx.identity_resolver = _StubResolver(
            error=OrchidIdentityError("upstream broken", status_code=500),
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_identity(
                ResolveIdentityRequest(access_token="tok"),
                settings=Settings(),
            )
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_resolver_unreachable_becomes_502(self, reset_identity_resolver):
        """status_code=0 means "didn't reach upstream" — a 502 is the
        right shape so the caller distinguishes network problems from
        a user's expired token.
        """
        app_ctx.identity_resolver = _StubResolver(
            error=OrchidIdentityError("connection refused"),
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_identity(
                ResolveIdentityRequest(access_token="tok"),
                settings=Settings(),
            )
        assert exc.value.status_code == 502

    def test_endpoint_is_unauthenticated(self):
        """Same posture as ``/auth/exchange-code``: the token itself
        is the proof of identity; no orchid-api bearer required.  A
        regression here would break every downstream client that
        doesn't yet have a bearer (Phase-4 discovery happens BEFORE
        auth).
        """
        import inspect

        sig = inspect.signature(resolve_identity)
        assert set(sig.parameters.keys()) == {"request", "settings"}


class TestResolveIdentityRequestValidation:
    def test_requires_access_token(self):
        with pytest.raises(Exception):
            ResolveIdentityRequest()  # type: ignore[call-arg]

    def test_rejects_empty_access_token(self):
        with pytest.raises(Exception):
            ResolveIdentityRequest(access_token="")

    def test_auth_domain_optional(self):
        req = ResolveIdentityRequest(access_token="tok")
        assert req.auth_domain is None
