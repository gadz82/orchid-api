"""Tests for orchid_api.context — AppContext dataclass + FastAPI deps."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from orchid_ai.runtime import OrchidRuntime

from orchid_api.context import (
    AppContext,
    app_ctx,
    get_agents_config,
    get_chat_repo,
    get_graph,
    get_mcp_token_store,
    get_oauth_state_store,
    get_runtime,
)


class TestAppContext:
    def test_default_values(self):
        ctx = AppContext()
        # runtime is a read-through property; returns a default ``OrchidRuntime``
        # when ``ctx.orchid`` is None.
        assert isinstance(ctx.runtime, OrchidRuntime)
        assert ctx.graph is None
        assert ctx.http_client is None
        assert ctx.identity_resolver is None
        assert ctx.chat_repo is None
        assert ctx.oauth_state_store is None
        assert ctx.agents_config is None
        assert ctx.mcp_token_store is None
        assert ctx.orchid is None

    def test_can_set_flat_fields(self):
        ctx = AppContext()
        ctx.http_client = "test-client"
        ctx.identity_resolver = "test-resolver"
        assert ctx.http_client == "test-client"
        assert ctx.identity_resolver == "test-resolver"

    def test_runtime_provides_reader(self):
        ctx = AppContext()
        reader = ctx.runtime.get_reader()
        # Default runtime returns NullVectorReader.
        assert reader is not None

    def test_read_through_properties_follow_orchid(self):
        """``graph``/``chat_repo``/``mcp_token_store``/``agents_config`` are
        read-through properties delegating to ``ctx.orchid``."""
        ctx = AppContext()
        fake = MagicMock()
        fake.graph = "G"
        fake.chat_repo = "CR"
        fake.mcp_token_store = "TS"
        fake.config = "CF"
        fake.runtime = OrchidRuntime(default_model="openai/gpt-4o")
        ctx.orchid = fake

        assert ctx.graph == "G"
        assert ctx.chat_repo == "CR"
        assert ctx.mcp_token_store == "TS"
        assert ctx.agents_config == "CF"
        assert ctx.runtime.default_model == "openai/gpt-4o"


class TestDependencyHelpers:
    """Each helper raises a clear 503 when its resource is unset.

    Swaps ``app_ctx.orchid`` / ``app_ctx.oauth_state_store`` for the
    test and restores them afterward so tests don't leak state.
    """

    @pytest.fixture(autouse=True)
    def _clean_slate(self):
        saved_orchid = app_ctx.orchid
        saved_oauth = app_ctx.oauth_state_store
        app_ctx.orchid = None
        app_ctx.oauth_state_store = None
        yield
        app_ctx.orchid = saved_orchid
        app_ctx.oauth_state_store = saved_oauth

    def test_get_runtime_always_returns(self):
        # runtime has a property fallback — never None.
        assert isinstance(get_runtime(), OrchidRuntime)

    def test_get_chat_repo_raises_503_when_unset(self):
        with pytest.raises(HTTPException) as exc:
            get_chat_repo()
        assert exc.value.status_code == 503

    def test_get_graph_raises_503_when_unset(self):
        with pytest.raises(HTTPException) as exc:
            get_graph()
        assert exc.value.status_code == 503

    def test_get_agents_config_raises_503_when_unset(self):
        with pytest.raises(HTTPException) as exc:
            get_agents_config()
        assert exc.value.status_code == 503

    def test_get_oauth_state_store_raises_503_when_unset(self):
        with pytest.raises(HTTPException) as exc:
            get_oauth_state_store()
        assert exc.value.status_code == 503

    def test_get_mcp_token_store_raises_503_when_unset(self):
        with pytest.raises(HTTPException) as exc:
            get_mcp_token_store()
        assert exc.value.status_code == 503

    def test_deps_return_populated_values(self):
        fake = MagicMock()
        fake.graph = "graph-sentinel"
        fake.chat_repo = "repo-sentinel"
        app_ctx.orchid = fake
        assert get_graph() == "graph-sentinel"
        assert get_chat_repo() == "repo-sentinel"
