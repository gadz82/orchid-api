"""Tests for orchid_api.context — AppContext dataclass + FastAPI deps."""

from __future__ import annotations

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
        assert isinstance(ctx.runtime, OrchidRuntime)
        assert ctx.graph is None
        assert ctx.http_client is None
        assert ctx.identity_resolver is None
        assert ctx.chat_repo is None
        assert ctx.oauth_state_store is None
        assert ctx.agents_config is None

    def test_can_set_fields(self):
        ctx = AppContext()
        ctx.graph = "test-graph"
        assert ctx.graph == "test-graph"

    def test_runtime_provides_reader(self):
        ctx = AppContext()
        reader = ctx.runtime.get_reader()
        # Default runtime returns NullVectorReader
        assert reader is not None

    def test_custom_runtime(self):
        runtime = OrchidRuntime(default_model="openai/gpt-4o")
        ctx = AppContext(runtime=runtime)
        assert ctx.runtime.default_model == "openai/gpt-4o"


class TestDependencyHelpers:
    """Each helper raises a clear 503 when its resource is unset.

    Uses a context-manager pattern to save/restore the singleton so tests
    don't leak state.
    """

    @pytest.fixture(autouse=True)
    def _clean_slate(self):
        saved = {
            "graph": app_ctx.graph,
            "chat_repo": app_ctx.chat_repo,
            "agents_config": app_ctx.agents_config,
            "oauth_state_store": app_ctx.oauth_state_store,
            "mcp_token_store": app_ctx.mcp_token_store,
        }
        for k in saved:
            setattr(app_ctx, k, None)
        yield
        for k, v in saved.items():
            setattr(app_ctx, k, v)

    def test_get_runtime_always_returns(self):
        # runtime has default_factory — never None
        assert get_runtime() is app_ctx.runtime

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
        sentinel_graph = "graph-sentinel"
        sentinel_repo = object()
        app_ctx.graph = sentinel_graph
        app_ctx.chat_repo = sentinel_repo
        assert get_graph() is sentinel_graph
        assert get_chat_repo() is sentinel_repo
