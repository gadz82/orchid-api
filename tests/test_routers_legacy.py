"""Tests for orchid_api.routers.legacy — health, legacy chat, index."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import OrchidAuthContext

from orchid_api.models import ChatRequest, IndexRequest
from orchid_api.routers.legacy import chat_legacy, health, index_data
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return OrchidAuthContext(access_token="tok", tenant_key="t1", user_id="u1")


# ── health ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_graph_ready():
    settings = Settings()
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = "some-graph"
        ctx.runtime.default_model = "ollama/llama3.2"
        result = await health(settings=settings)
    assert result["status"] == "ok"
    assert result["graph_ready"] is True
    assert result["model"] == "ollama/llama3.2"


@pytest.mark.asyncio
async def test_health_graph_not_ready():
    settings = Settings()
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = None
        ctx.runtime.default_model = "ollama/llama3.2"
        result = await health(settings=settings)
    assert result["graph_ready"] is False


# ── chat_legacy ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_legacy_success(auth):
    graph = AsyncMock()
    graph.ainvoke.return_value = {
        "final_response": "Hello back!",
        "active_agents": ["assistant"],
    }
    result = await chat_legacy(ChatRequest(message="Hello"), auth=auth, graph=graph)
    assert result.response == "Hello back!"
    assert result.agents_used == ["assistant"]
    assert result.tenant_id == "t1"


@pytest.mark.asyncio
async def test_chat_legacy_uses_provided_chat_id(auth):
    graph = AsyncMock()
    graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    result = await chat_legacy(ChatRequest(message="Hi", chat_id="custom-id"), auth=auth, graph=graph)
    assert result.chat_id == "custom-id"


@pytest.mark.asyncio
async def test_chat_legacy_generates_chat_id(auth):
    graph = AsyncMock()
    graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    result = await chat_legacy(ChatRequest(message="Hi"), auth=auth, graph=graph)
    assert len(result.chat_id) == 36  # UUID format


# ── index_data ─────────────────────────────────────────────


def _runtime(reader) -> MagicMock:
    rt = MagicMock()
    rt.get_reader.return_value = reader
    return rt


@pytest.mark.asyncio
async def test_index_disabled_by_default(auth):
    """The /index endpoint is disabled by default (allow_index_endpoint=False)."""
    with pytest.raises(HTTPException) as exc:
        await index_data(IndexRequest(), auth=auth, settings=Settings(), runtime=_runtime(object()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_index_no_writer(auth):
    """When enabled but reader isn't a OrchidVectorWriter, index_data should 503."""
    settings = Settings(allow_index_endpoint=True)
    with pytest.raises(HTTPException) as exc:
        await index_data(IndexRequest(), auth=auth, settings=settings, runtime=_runtime(object()))
    assert exc.value.status_code == 503
