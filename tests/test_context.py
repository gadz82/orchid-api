"""Tests for orchid_api.context — AppContext dataclass."""
from __future__ import annotations

from orchid_api.context import AppContext


class TestAppContext:
    def test_default_values(self):
        ctx = AppContext()
        assert ctx.graph is None
        assert ctx.reader is None
        assert ctx.http_client is None
        assert ctx.identity_resolver is None
        assert ctx.chat_repo is None

    def test_can_set_fields(self):
        ctx = AppContext()
        ctx.graph = "test-graph"
        ctx.reader = "test-reader"
        assert ctx.graph == "test-graph"
        assert ctx.reader == "test-reader"
