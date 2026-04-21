"""Tests for ``AppContext.release_resources`` + consolidated optional deps."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchid_api.context import (
    AppContext,
    app_ctx,
    get_agents_config_optional,
    get_mcp_token_store_optional,
)


class TestReleaseResources:
    @pytest.mark.asyncio
    async def test_tears_down_orchid_and_oauth_store(self):
        """``release_resources`` closes the owned :class:`Orchid` plus the OAuth store."""
        ctx = AppContext()

        orchid = MagicMock()
        orchid.close = AsyncMock()
        ctx.orchid = orchid

        oauth_store = AsyncMock()
        ctx.oauth_state_store = oauth_store

        await ctx.release_resources()

        orchid.close.assert_awaited_once()
        oauth_store.close.assert_awaited_once()
        assert ctx.orchid is None
        assert ctx.oauth_state_store is None

    @pytest.mark.asyncio
    async def test_idempotent(self):
        ctx = AppContext()
        # No orchid wired — second call must still be a no-op.
        await ctx.release_resources()
        await ctx.release_resources()


class TestOptionalDepHelpers:
    """The optional FastAPI deps must tolerate ``app_ctx.orchid is None``.

    Before the refactor these read flat ``app_ctx.mcp_token_store`` /
    ``app_ctx.agents_config`` fields; now both are properties that
    delegate to ``app_ctx.orchid``.  The fixture swaps ``app_ctx.orchid``
    with a mock so the deps see something to read through.
    """

    @pytest.fixture(autouse=True)
    def _clean_slate(self):
        saved_orchid = app_ctx.orchid
        app_ctx.orchid = None
        yield
        app_ctx.orchid = saved_orchid

    def test_mcp_token_store_optional_returns_none_when_unset(self):
        assert get_mcp_token_store_optional() is None

    def test_mcp_token_store_optional_returns_value_when_set(self):
        sentinel = object()
        fake = MagicMock()
        fake.mcp_token_store = sentinel
        app_ctx.orchid = fake
        assert get_mcp_token_store_optional() is sentinel

    def test_agents_config_optional_returns_none_when_unset(self):
        assert get_agents_config_optional() is None

    def test_agents_config_optional_returns_value_when_set(self):
        sentinel = object()
        fake = MagicMock()
        fake.config = sentinel
        app_ctx.orchid = fake
        assert get_agents_config_optional() is sentinel
