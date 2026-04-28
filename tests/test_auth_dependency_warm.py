"""Tests for the lazy-warm backstop in ``get_auth_context``.

When ``setup_orchid`` has wired the framework, every successful auth
resolution (including ``DEV_AUTH_BYPASS=true``) schedules a fire-and-
forget per-user warm — but never crashes the request handler if the
warmer raises and never re-warms the same user twice in a row.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchid_ai.core.state import OrchidAuthContext

from orchid_api.auth import get_auth_context
from orchid_api.settings import Settings


def _fake_orchid(*, already_warmed: bool = False, raises: Exception | None = None) -> MagicMock:
    """Build an Orchid stand-in with a session_warmer we can probe."""
    fake = MagicMock()
    warmer = MagicMock()
    warmer.is_warmed = MagicMock(return_value=already_warmed)
    if raises is not None:
        warmer.warm_for_user = AsyncMock(side_effect=raises)
    else:
        warmer.warm_for_user = AsyncMock(return_value=MagicMock())
    fake.session_warmer = warmer
    return fake


async def _settle_background_tasks() -> None:
    """Yield twice so any ``asyncio.create_task`` we scheduled runs."""
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_dev_bypass_schedules_warm():
    fake_orchid = _fake_orchid()
    settings = Settings(dev_auth_bypass=True)

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.orchid = fake_orchid
        ctx = await get_auth_context(
            authorization="Bearer ignored",
            x_auth_domain=None,
            settings=settings,
        )
        await _settle_background_tasks()

    assert ctx.user_id == "dev-user-00000000"
    fake_orchid.session_warmer.is_warmed.assert_called_once()
    fake_orchid.session_warmer.warm_for_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_warmed_user_skips_scheduling():
    fake_orchid = _fake_orchid(already_warmed=True)
    settings = Settings(dev_auth_bypass=True)

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.orchid = fake_orchid
        await get_auth_context(
            authorization="Bearer ignored",
            x_auth_domain=None,
            settings=settings,
        )
        await _settle_background_tasks()

    fake_orchid.session_warmer.is_warmed.assert_called_once()
    fake_orchid.session_warmer.warm_for_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_warm_exception_does_not_crash_request():
    """Background warm raising MUST NOT bubble up to the request handler."""
    fake_orchid = _fake_orchid(raises=RuntimeError("warmer boom"))
    settings = Settings(dev_auth_bypass=True)

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.orchid = fake_orchid
        # Returning normally is the test — no exception escapes.
        ctx = await get_auth_context(
            authorization="Bearer ignored",
            x_auth_domain=None,
            settings=settings,
        )
        await _settle_background_tasks()

    assert ctx.tenant_key == "99999"
    fake_orchid.session_warmer.warm_for_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_orchid_means_no_warm_scheduled():
    settings = Settings(dev_auth_bypass=True)

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.orchid = None
        # Does not raise even though there's nothing to warm.
        ctx = await get_auth_context(
            authorization="Bearer ignored",
            x_auth_domain=None,
            settings=settings,
        )
    assert ctx.user_id == "dev-user-00000000"


@pytest.mark.asyncio
async def test_resolved_user_schedules_warm_with_resolved_context():
    """Production path: identity_resolver returns a real context, then warm."""
    fake_orchid = _fake_orchid()
    resolved = OrchidAuthContext(access_token="t", tenant_key="acme", user_id="alice")
    resolver = AsyncMock()
    resolver.resolve = AsyncMock(return_value=resolved)
    settings = Settings(dev_auth_bypass=False, auth_domain="acme.example.com")

    with patch("orchid_api.auth.app_ctx") as mock_ctx:
        mock_ctx.orchid = fake_orchid
        mock_ctx.identity_resolver = resolver
        ctx = await get_auth_context(
            authorization="Bearer abc",
            x_auth_domain=None,
            settings=settings,
        )
        await _settle_background_tasks()

    assert ctx.user_id == "alice"
    fake_orchid.session_warmer.warm_for_user.assert_awaited_once()
    args, _kwargs = fake_orchid.session_warmer.warm_for_user.call_args
    # The warm call must use the resolver's auth context, not a fresh one.
    assert args[0] is resolved
