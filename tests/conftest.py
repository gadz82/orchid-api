"""Shared fixtures for orchid-api tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from orchid_ai.core.state import AuthContext
from orchid_ai.persistence.models import ChatMessage, ChatSession


@pytest.fixture
def auth_context():
    return AuthContext(access_token="test-token", tenant_key="t1", user_id="u1")


@pytest.fixture
def mock_chat_repo():
    repo = AsyncMock()
    repo.create_chat = AsyncMock()
    repo.list_chats = AsyncMock(return_value=[])
    repo.get_chat = AsyncMock(return_value=None)
    repo.delete_chat = AsyncMock()
    repo.get_messages = AsyncMock(return_value=[])
    repo.add_message = AsyncMock()
    repo.update_title = AsyncMock()
    repo.mark_shared = AsyncMock()
    repo.close = AsyncMock()
    return repo


@pytest.fixture
def sample_session():
    now = datetime.now(timezone.utc)
    return ChatSession(
        id="chat-001",
        tenant_id="t1",
        user_id="u1",
        title="Test Chat",
        created_at=now,
        updated_at=now,
        is_shared=False,
    )


@pytest.fixture
def sample_message():
    now = datetime.now(timezone.utc)
    return ChatMessage(
        id="msg-001",
        chat_id="chat-001",
        role="user",
        content="Hello",
        agents_used=[],
        created_at=now,
    )
