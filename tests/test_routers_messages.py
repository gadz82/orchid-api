"""Tests for orchid_api.routers.messages — send message and upload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid.core.state import AuthContext

from orchid_api.routers.messages import send_chat_message, upload_documents
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return AuthContext(access_token="tok", tenant_key="t1", user_id="u1")


# ── send_chat_message ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_no_graph(auth):
    """Returns 503 when graph is not initialised."""
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = None
        ctx.chat_repo = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("chat-1", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_send_message_no_chat_repo(auth):
    """Returns 503 when chat repo is not initialised."""
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = AsyncMock()
        ctx.chat_repo = None
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("chat-1", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_send_message_chat_not_found(auth):
    """Returns 404 when chat doesn't exist."""
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = AsyncMock()
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("chat-1", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_send_message_wrong_user(auth, sample_session):
    """Returns 404 when chat belongs to a different user."""
    sample_session.user_id = "other-user"
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = AsyncMock()
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("chat-001", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_send_message_success(auth, sample_session):
    """Successful message returns ChatResponse."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "final_response": "Hello!",
        "active_agents": ["helper"],
    }
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = mock_graph
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        ctx.chat_repo.get_messages = AsyncMock(return_value=[])
        # runtime.get_reader() returns a NullVectorReader-like mock (no VectorWriter)
        ctx.runtime.get_reader.return_value = MagicMock()
        result = await send_chat_message("chat-001", message="Hi there", files=[], auth=auth, settings=Settings())
    assert result.response == "Hello!"
    assert result.agents_used == ["helper"]
    assert result.chat_id == "chat-001"


@pytest.mark.asyncio
async def test_send_message_persists_messages(auth, sample_session):
    """Both user and assistant messages are persisted."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"final_response": "Reply", "active_agents": []}
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = mock_graph
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        ctx.chat_repo.get_messages = AsyncMock(return_value=[])
        ctx.runtime.get_reader.return_value = MagicMock()
        await send_chat_message("chat-001", message="Hello", files=[], auth=auth, settings=Settings())
    calls = ctx.chat_repo.add_message.call_args_list
    assert len(calls) == 2
    assert calls[0].args == ("chat-001", "user", "Hello")
    assert calls[1].args == ("chat-001", "assistant", "Reply")


@pytest.mark.asyncio
async def test_send_message_auto_titles_first(auth, sample_session):
    """First message in a chat auto-generates a title."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = mock_graph
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        ctx.chat_repo.get_messages = AsyncMock(return_value=[])
        ctx.runtime.get_reader.return_value = MagicMock()
        await send_chat_message("chat-001", message="Tell me about LeBron", files=[], auth=auth, settings=Settings())
    ctx.chat_repo.update_title.assert_called_once()
    title = ctx.chat_repo.update_title.call_args.args[1]
    assert "LeBron" in title


@pytest.mark.asyncio
async def test_send_message_no_auto_title_with_history(auth, sample_session, sample_message):
    """Subsequent messages do NOT auto-title."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = mock_graph
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        ctx.chat_repo.get_messages = AsyncMock(return_value=[sample_message])
        ctx.runtime.get_reader.return_value = MagicMock()
        await send_chat_message("chat-001", message="Follow up", files=[], auth=auth, settings=Settings())
    ctx.chat_repo.update_title.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_truncates_long_title(auth, sample_session):
    """Auto-title is truncated to 50 chars with ellipsis."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.graph = mock_graph
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=sample_session)
        ctx.chat_repo.get_messages = AsyncMock(return_value=[])
        ctx.runtime.get_reader.return_value = MagicMock()
        long_msg = "A" * 100
        await send_chat_message("chat-001", message=long_msg, files=[], auth=auth, settings=Settings())
    title = ctx.chat_repo.update_title.call_args.args[1]
    assert len(title) <= 52  # 50 + "…"


# ── upload_documents ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_no_writer(auth):
    """Returns 503 when vector store doesn't support writing."""
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        # Return a plain object that is NOT a VectorWriter instance
        ctx.runtime.get_reader.return_value = object()
        ctx.chat_repo = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await upload_documents("chat-1", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_upload_no_chat_repo(auth):
    """Returns 503 when chat repo is not initialised."""
    with patch("orchid_api.routers.messages.app_ctx") as ctx, \
         patch("orchid_api.routers.messages.isinstance", return_value=True):
        ctx.runtime.get_reader.return_value = MagicMock()
        ctx.chat_repo = None
        with pytest.raises(HTTPException) as exc:
            await upload_documents("chat-1", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_upload_chat_not_found(auth):
    """Returns 404 when chat doesn't exist."""
    mock_reader = MagicMock()
    with patch("orchid_api.routers.messages.app_ctx") as ctx, \
         patch("orchid_api.routers.messages.isinstance", return_value=True):
        ctx.runtime.get_reader.return_value = mock_reader
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await upload_documents("chat-1", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 404
