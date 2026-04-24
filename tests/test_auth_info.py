"""Tests for orchid_api.routers.auth_info — public posture probe."""

from __future__ import annotations


import pytest

from orchid_api.context import app_ctx
from orchid_api.routers.auth_info import get_auth_info
from orchid_api.settings import Settings


class DummyResolver:
    """Minimal stand-in for OrchidIdentityResolver — used as a presence marker."""


class TestAuthInfoEndpoint:
    @pytest.mark.asyncio
    async def test_dev_bypass_true_no_resolver(self):
        settings = Settings(dev_auth_bypass=True)
        original = app_ctx.identity_resolver
        app_ctx.identity_resolver = None
        try:
            result = await get_auth_info(settings=settings)
        finally:
            app_ctx.identity_resolver = original
        assert result == {"dev_bypass": True, "identity_resolver_configured": False}

    @pytest.mark.asyncio
    async def test_dev_bypass_false_with_resolver(self):
        settings = Settings(dev_auth_bypass=False)
        original = app_ctx.identity_resolver
        app_ctx.identity_resolver = DummyResolver()  # type: ignore[assignment]
        try:
            result = await get_auth_info(settings=settings)
        finally:
            app_ctx.identity_resolver = original
        assert result == {"dev_bypass": False, "identity_resolver_configured": True}

    @pytest.mark.asyncio
    async def test_dev_bypass_false_without_resolver(self):
        """Degenerate but valid shape — useful signal for a misconfigured deploy."""
        settings = Settings(dev_auth_bypass=False)
        original = app_ctx.identity_resolver
        app_ctx.identity_resolver = None
        try:
            result = await get_auth_info(settings=settings)
        finally:
            app_ctx.identity_resolver = original
        assert result == {"dev_bypass": False, "identity_resolver_configured": False}

    @pytest.mark.asyncio
    async def test_no_auth_required_on_endpoint(self):
        """Endpoint must be unauthenticated — no ``get_auth_context`` dep."""
        # Sanity: the function signature should take only `settings`, never
        # an auth context.  Catches accidental auth-wiring regressions.
        import inspect

        sig = inspect.signature(get_auth_info)
        param_names = set(sig.parameters.keys())
        assert "auth" not in param_names
        assert "auth_context" not in param_names
        assert param_names == {"settings"}
