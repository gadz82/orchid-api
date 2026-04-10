"""Tests for orchid_api.auth — authentication dependency."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid.core.identity import IdentityError
from orchid.core.state import AuthContext

from orchid_api.auth import get_auth_context
from orchid_api.settings import Settings


@pytest.mark.asyncio
async def test_dev_auth_bypass():
    """DEV_AUTH_BYPASS returns a dummy AuthContext."""
    settings = Settings(dev_auth_bypass=True)
    ctx = await get_auth_context(
        authorization="Bearer anything",
        x_auth_domain=None,
        settings=settings,
    )
    assert ctx.access_token == "dev-token"
    assert ctx.tenant_key == "99999"
    assert ctx.user_id == "dev-user-00000000"


@pytest.mark.asyncio
async def test_missing_bearer_prefix():
    """Non-Bearer authorization header raises 401."""
    settings = Settings(dev_auth_bypass=False)
    with pytest.raises(HTTPException) as exc_info:
        await get_auth_context(
            authorization="Token abc",
            x_auth_domain=None,
            settings=settings,
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_no_identity_resolver():
    """Missing identity resolver returns 503."""
    settings = Settings(dev_auth_bypass=False)
    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.identity_resolver = None
        with pytest.raises(HTTPException) as exc_info:
            await get_auth_context(
                authorization="Bearer valid-token",
                x_auth_domain=None,
                settings=settings,
            )
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_successful_resolution():
    """Valid token resolves to AuthContext."""
    settings = Settings(dev_auth_bypass=False, auth_domain="example.com")
    expected = AuthContext(access_token="tok", tenant_key="t1", user_id="u1")

    resolver = AsyncMock()
    resolver.resolve = AsyncMock(return_value=expected)

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.identity_resolver = resolver
        ctx = await get_auth_context(
            authorization="Bearer tok",
            x_auth_domain=None,
            settings=settings,
        )
    assert ctx.tenant_key == "t1"
    assert ctx.user_id == "u1"
    resolver.resolve.assert_called_once_with(domain="example.com", bearer_token="tok")


@pytest.mark.asyncio
async def test_x_auth_domain_overrides_settings():
    """x-auth-domain header overrides settings.auth_domain."""
    settings = Settings(dev_auth_bypass=False, auth_domain="default.com")
    expected = AuthContext(access_token="tok", tenant_key="t1", user_id="u1")

    resolver = AsyncMock()
    resolver.resolve = AsyncMock(return_value=expected)

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.identity_resolver = resolver
        await get_auth_context(
            authorization="Bearer tok",
            x_auth_domain="override.com",
            settings=settings,
        )
    resolver.resolve.assert_called_once_with(domain="override.com", bearer_token="tok")


@pytest.mark.asyncio
async def test_identity_error_raises_http_exception():
    """IdentityError from resolver maps to HTTPException."""
    settings = Settings(dev_auth_bypass=False, auth_domain="x.com")
    resolver = AsyncMock()
    resolver.resolve = AsyncMock(side_effect=IdentityError("Invalid token", status_code=401))

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.identity_resolver = resolver
        with pytest.raises(HTTPException) as exc_info:
            await get_auth_context(
                authorization="Bearer bad",
                x_auth_domain=None,
                settings=settings,
            )
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_raises_401():
    """Expired token is rejected with 401."""
    settings = Settings(dev_auth_bypass=False, auth_domain="x.com")
    expired_ctx = AuthContext(access_token="tok", tenant_key="t1", user_id="u1", expires_at=1.0)

    resolver = AsyncMock()
    resolver.resolve = AsyncMock(return_value=expired_ctx)

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.identity_resolver = resolver
        with pytest.raises(HTTPException) as exc_info:
            await get_auth_context(
                authorization="Bearer tok",
                x_auth_domain=None,
                settings=settings,
            )
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()
