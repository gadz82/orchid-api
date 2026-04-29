"""Tests for ``orchid_api.routers.diagnostics`` — health probe."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from orchid_api.routers.diagnostics import health
from orchid_api.settings import Settings


@pytest.mark.asyncio
async def test_health_graph_ready():
    settings = Settings()
    with patch("orchid_api.routers.diagnostics.app_ctx") as ctx:
        ctx.graph = "some-graph"
        ctx.runtime.default_model = "ollama/llama3.2"
        result = await health(settings=settings)
    assert result["status"] == "ok"
    assert result["graph_ready"] is True
    assert result["model"] == "ollama/llama3.2"


@pytest.mark.asyncio
async def test_health_graph_not_ready():
    settings = Settings()
    with patch("orchid_api.routers.diagnostics.app_ctx") as ctx:
        ctx.graph = None
        ctx.runtime.default_model = "ollama/llama3.2"
        result = await health(settings=settings)
    assert result["graph_ready"] is False
