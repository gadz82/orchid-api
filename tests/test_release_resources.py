"""Tests for ``AppContext.release_resources`` + consolidated optional deps."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchid_api.context import (
    AppContext,
    get_agents_config_optional,
    get_mcp_token_store_optional,
    app_ctx,
)


class TestReleaseResources:
    @pytest.mark.asyncio
    async def test_tears_down_bootstrap_and_oauth_store(self):
        ctx = AppContext()

        bootstrap = MagicMock()
        bootstrap.runtime = MagicMock()
        bootstrap.runtime.checkpointer = None
        bootstrap.mcp_token_store = AsyncMock()
        bootstrap.chat_repo = AsyncMock()
        ctx._bootstrap = bootstrap

        oauth_store = AsyncMock()
        ctx.oauth_state_store = oauth_store
        ctx.chat_repo = bootstrap.chat_repo
        ctx.mcp_token_store = bootstrap.mcp_token_store

        await ctx.release_resources()

        bootstrap.mcp_token_store.close.assert_awaited_once()
        bootstrap.chat_repo.close.assert_awaited_once()
        oauth_store.close.assert_awaited_once()
        assert ctx._bootstrap is None
        assert ctx.oauth_state_store is None
        assert ctx.chat_repo is None
        assert ctx.mcp_token_store is None

    @pytest.mark.asyncio
    async def test_idempotent(self):
        ctx = AppContext()
        # No bootstrap wired — second call must still be a no-op.
        await ctx.release_resources()
        await ctx.release_resources()


class TestOptionalDepHelpers:
    @pytest.fixture(autouse=True)
    def _clean_slate(self):
        saved = {
            "mcp_token_store": app_ctx.mcp_token_store,
            "agents_config": app_ctx.agents_config,
        }
        for k in saved:
            setattr(app_ctx, k, None)
        yield
        for k, v in saved.items():
            setattr(app_ctx, k, v)

    def test_mcp_token_store_optional_returns_none_when_unset(self):
        assert get_mcp_token_store_optional() is None

    def test_mcp_token_store_optional_returns_value_when_set(self):
        sentinel = object()
        app_ctx.mcp_token_store = sentinel
        assert get_mcp_token_store_optional() is sentinel

    def test_agents_config_optional_returns_none_when_unset(self):
        assert get_agents_config_optional() is None

    def test_agents_config_optional_returns_value_when_set(self):
        sentinel = object()
        app_ctx.agents_config = sentinel
        assert get_agents_config_optional() is sentinel
