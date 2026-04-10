"""Tests for orchid_api.routers.sharing — chat sharing endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid.core.state import AuthContext

from orchid_api.routers.sharing import share_chat
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return AuthContext(access_token="tok", tenant_key="t1", user_id="u1")


@pytest.mark.asyncio
async def test_share_no_chat_repo(auth):
    """Returns 503 when chat repo is not initialised."""
    with patch("orchid_api.routers.sharing.app_ctx") as ctx:
        ctx.chat_repo = None
        with pytest.raises(HTTPException) as exc:
            await share_chat("chat-1", auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_share_no_qdrant_backend(auth):
    """Returns 503 when reader is not a QdrantRepository."""
    with patch("orchid_api.routers.sharing.app_ctx") as ctx:
        ctx.chat_repo = AsyncMock()
        ctx.runtime.get_reader.return_value = MagicMock()  # Not a QdrantRepository
        with pytest.raises(HTTPException) as exc:
            await share_chat("chat-1", auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_share_chat_not_found(auth):
    """Returns 404 when chat doesn't exist."""
    with patch("orchid_api.routers.sharing.app_ctx") as ctx, \
         patch("orchid_api.routers.sharing.isinstance", return_value=True):
        mock_reader = MagicMock()
        ctx.runtime.get_reader.return_value = mock_reader
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await share_chat("chat-1", auth=auth, settings=Settings())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_share_wrong_user(auth, sample_session):
    """Returns 404 when chat belongs to a different user."""
    sample_session.user_id = "other-user"
    with patch("orchid_api.routers.sharing.app_ctx") as ctx, \
         patch("orchid_api.routers.sharing.isinstance", return_value=True):
        ctx.runtime.get_reader.return_value = MagicMock()
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        with pytest.raises(HTTPException) as exc:
            await share_chat("chat-001", auth=auth, settings=Settings())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_share_success(auth, sample_session):
    """Successful share promotes scope and marks chat as shared."""
    mock_reader = MagicMock()
    mock_reader.promote_scope = AsyncMock(return_value=5)

    mock_config = MagicMock()
    agent_cfg = MagicMock()
    agent_cfg.rag.enabled = True
    agent_cfg.rag.namespace = "knowledge"
    mock_config.agents = {"helper": agent_cfg}

    with patch("orchid_api.routers.sharing.app_ctx") as ctx, \
         patch("orchid_api.routers.sharing.isinstance", return_value=True), \
         patch("orchid_api.routers.sharing.load_config", return_value=mock_config):
        ctx.runtime.get_reader.return_value = mock_reader
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        result = await share_chat("chat-001", auth=auth, settings=Settings())

    assert result["status"] == "shared"
    assert result["chat_id"] == "chat-001"
    assert result["points_promoted"] > 0
    ctx.chat_repo.mark_shared.assert_called_once_with("chat-001")
