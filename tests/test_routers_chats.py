"""Tests for orchid_api.routers.chats — CRUD endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import AuthContext

from orchid_api.routers.chats import create_chat, delete_chat, get_messages, list_chats
from orchid_api.models import CreateChatRequest


@pytest.fixture
def auth():
    return AuthContext(access_token="tok", tenant_key="t1", user_id="u1")


# ── create_chat ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_chat_success(auth, mock_chat_repo, sample_session):
    mock_chat_repo.create_chat.return_value = sample_session
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        result = await create_chat(CreateChatRequest(title="Test"), auth=auth)
    assert result.id == "chat-001"
    assert result.title == "Test Chat"
    mock_chat_repo.create_chat.assert_called_once_with(tenant_id="t1", user_id="u1", title="Test")


@pytest.mark.asyncio
async def test_create_chat_no_repo(auth):
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = None
        with pytest.raises(HTTPException) as exc:
            await create_chat(CreateChatRequest(title="Test"), auth=auth)
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_create_chat_default_title(auth, mock_chat_repo, sample_session):
    mock_chat_repo.create_chat.return_value = sample_session
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        await create_chat(CreateChatRequest(), auth=auth)
    mock_chat_repo.create_chat.assert_called_once_with(tenant_id="t1", user_id="u1", title="New chat")


# ── list_chats ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_chats_empty(auth, mock_chat_repo):
    mock_chat_repo.list_chats.return_value = []
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        result = await list_chats(auth=auth)
    assert result == []


@pytest.mark.asyncio
async def test_list_chats_returns_sessions(auth, mock_chat_repo, sample_session):
    mock_chat_repo.list_chats.return_value = [sample_session]
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        result = await list_chats(auth=auth)
    assert len(result) == 1
    assert result[0].id == "chat-001"


# ── get_messages ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_messages_success(auth, mock_chat_repo, sample_session, sample_message):
    mock_chat_repo.get_chat.return_value = sample_session
    mock_chat_repo.get_messages.return_value = [sample_message]
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        result = await get_messages("chat-001", auth=auth)
    assert len(result) == 1
    assert result[0].content == "Hello"


@pytest.mark.asyncio
async def test_get_messages_chat_not_found(auth, mock_chat_repo):
    mock_chat_repo.get_chat.return_value = None
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        with pytest.raises(HTTPException) as exc:
            await get_messages("nonexistent", auth=auth)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_messages_wrong_user(auth, mock_chat_repo, sample_session):
    sample_session.user_id = "other-user"
    mock_chat_repo.get_chat.return_value = sample_session
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        with pytest.raises(HTTPException) as exc:
            await get_messages("chat-001", auth=auth)
        assert exc.value.status_code == 404


# ── delete_chat ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_chat_success(auth, mock_chat_repo, sample_session):
    mock_chat_repo.get_chat.return_value = sample_session
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        result = await delete_chat("chat-001", auth=auth)
    assert result["status"] == "deleted"
    mock_chat_repo.delete_chat.assert_called_once_with("chat-001")


@pytest.mark.asyncio
async def test_delete_chat_not_found(auth, mock_chat_repo):
    mock_chat_repo.get_chat.return_value = None
    with patch("orchid_api.routers.chats.app_ctx") as ctx:
        ctx.chat_repo = mock_chat_repo
        with pytest.raises(HTTPException) as exc:
            await delete_chat("nonexistent", auth=auth)
        assert exc.value.status_code == 404
