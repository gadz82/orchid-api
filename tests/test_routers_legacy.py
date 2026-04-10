"""Tests for orchid_api.routers.legacy — health, legacy chat, index."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid.core.state import AuthContext

from orchid_api.models import ChatRequest, IndexRequest
from orchid_api.routers.legacy import chat_legacy, health, index_data
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return AuthContext(access_token="tok", tenant_key="t1", user_id="u1")


# ── health ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_graph_ready():
    settings = Settings()
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = "some-graph"
        result = await health(settings=settings)
    assert result["status"] == "ok"
    assert result["graph_ready"] is True


@pytest.mark.asyncio
async def test_health_graph_not_ready():
    settings = Settings()
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = None
        result = await health(settings=settings)
    assert result["graph_ready"] is False


# ── chat_legacy ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_legacy_success(auth):
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "final_response": "Hello back!",
        "active_agents": ["assistant"],
    }
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = mock_graph
        result = await chat_legacy(ChatRequest(message="Hello"), auth=auth)
    assert result.response == "Hello back!"
    assert result.agents_used == ["assistant"]
    assert result.tenant_id == "t1"


@pytest.mark.asyncio
async def test_chat_legacy_no_graph(auth):
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = None
        with pytest.raises(HTTPException) as exc:
            await chat_legacy(ChatRequest(message="Hi"), auth=auth)
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_chat_legacy_uses_provided_chat_id(auth):
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = mock_graph
        result = await chat_legacy(ChatRequest(message="Hi", chat_id="custom-id"), auth=auth)
    assert result.chat_id == "custom-id"


@pytest.mark.asyncio
async def test_chat_legacy_generates_chat_id(auth):
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.graph = mock_graph
        result = await chat_legacy(ChatRequest(message="Hi"), auth=auth)
    assert len(result.chat_id) == 36  # UUID format


# ── index_data ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_no_writer():
    with patch("orchid_api.routers.legacy.app_ctx") as ctx:
        ctx.reader = None
        with pytest.raises(HTTPException) as exc:
            await index_data(IndexRequest())
        assert exc.value.status_code == 503
