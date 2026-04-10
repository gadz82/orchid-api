"""Tests for orchid_api.context — AppContext dataclass."""

from __future__ import annotations

from orchid.runtime import OrchidRuntime

from orchid_api.context import AppContext


class TestAppContext:
    def test_default_values(self):
        ctx = AppContext()
        assert isinstance(ctx.runtime, OrchidRuntime)
        assert ctx.graph is None
        assert ctx.http_client is None
        assert ctx.identity_resolver is None
        assert ctx.chat_repo is None

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
