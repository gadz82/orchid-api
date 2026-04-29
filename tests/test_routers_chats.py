"""Tests for orchid_api.routers.chats — CRUD endpoints.

Handlers now accept ``chat_repo`` as an injected FastAPI dependency
(``Depends(get_chat_repo)``) — tests pass the mock directly through
that parameter instead of patching ``app_ctx``.  The 503 null-check is
covered separately by exercising ``get_chat_repo`` itself.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import OrchidAuthContext

from orchid_api.context import app_ctx, get_chat_repo
from orchid_api.models import CreateChatRequest
from orchid_api.routers.chats import create_chat, delete_chat, get_messages, list_chats


@pytest.fixture
def auth():
    return OrchidAuthContext(access_token="tok", tenant_key="t1", user_id="u1")


# ── get_chat_repo dependency ───────────────────────────────


class TestGetChatRepoDep:
    def test_raises_503_when_unset(self):
        # ``chat_repo`` is now a read-through property of ``app_ctx.orchid``;
        # swap the underlying handle to ``None`` to simulate pre-startup.
        original = app_ctx.orchid
        app_ctx.orchid = None
        try:
            with pytest.raises(HTTPException) as exc:
                get_chat_repo()
            assert exc.value.status_code == 503
        finally:
            app_ctx.orchid = original


# ── create_chat ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_chat_success(auth, mock_chat_repo, sample_session):
    mock_chat_repo.create_chat.return_value = sample_session
    result = await create_chat(
        CreateChatRequest(title="Test"),
        auth=auth,
        chat_repo=mock_chat_repo,
    )
    assert result.id == "chat-001"
    assert result.title == "Test Chat"
    mock_chat_repo.create_chat.assert_called_once_with(tenant_id="t1", user_id="u1", title="Test")


@pytest.mark.asyncio
async def test_create_chat_default_title(auth, mock_chat_repo, sample_session):
    mock_chat_repo.create_chat.return_value = sample_session
    await create_chat(CreateChatRequest(), auth=auth, chat_repo=mock_chat_repo)
    mock_chat_repo.create_chat.assert_called_once_with(tenant_id="t1", user_id="u1", title="New chat")


# ── list_chats ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_chats_empty(auth, mock_chat_repo):
    mock_chat_repo.list_chats.return_value = []
    result = await list_chats(auth=auth, chat_repo=mock_chat_repo)
    assert result == []


@pytest.mark.asyncio
async def test_list_chats_returns_sessions(auth, mock_chat_repo, sample_session):
    mock_chat_repo.list_chats.return_value = [sample_session]
    result = await list_chats(auth=auth, chat_repo=mock_chat_repo)
    assert len(result) == 1
    assert result[0].id == "chat-001"


# ── get_messages ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_messages_success(auth, mock_chat_repo, sample_session, sample_message):
    mock_chat_repo.get_chat.return_value = sample_session
    mock_chat_repo.get_messages.return_value = [sample_message]
    result = await get_messages("chat-001", auth=auth, chat_repo=mock_chat_repo)
    assert len(result) == 1
    assert result[0].content == "Hello"


@pytest.mark.asyncio
async def test_get_messages_chat_not_found(auth, mock_chat_repo):
    mock_chat_repo.get_chat.return_value = None
    with pytest.raises(HTTPException) as exc:
        await get_messages("nonexistent", auth=auth, chat_repo=mock_chat_repo)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_messages_wrong_user(auth, mock_chat_repo, sample_session):
    sample_session.user_id = "other-user"
    mock_chat_repo.get_chat.return_value = sample_session
    with pytest.raises(HTTPException) as exc:
        await get_messages("chat-001", auth=auth, chat_repo=mock_chat_repo)
    assert exc.value.status_code == 404


# ── delete_chat ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_chat_success(auth, mock_chat_repo, sample_session):
    mock_chat_repo.get_chat.return_value = sample_session
    result = await delete_chat("chat-001", auth=auth, chat_repo=mock_chat_repo)
    assert result["status"] == "deleted"
    mock_chat_repo.delete_chat.assert_called_once_with("chat-001")


@pytest.mark.asyncio
async def test_delete_chat_not_found(auth, mock_chat_repo):
    mock_chat_repo.get_chat.return_value = None
    with pytest.raises(HTTPException) as exc:
        await delete_chat("nonexistent", auth=auth, chat_repo=mock_chat_repo)
    assert exc.value.status_code == 404


# ── pagination cap ─────────────────────────────────────────


def test_get_messages_pagination_is_bounded():
    """``?limit=`` is constrained to [1, 500] and ``?offset=`` to [0, ∞).

    The cap is enforced declaratively via ``Query(..., ge=, le=)`` so a
    runaway client can't ask for an unbounded slice. Validated through
    a real ``FastAPI`` instance so the behaviour mirrors production.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orchid_api.auth import get_auth_context
    from orchid_api.context import get_chat_repo
    from orchid_api.routers.chats import router
    from unittest.mock import AsyncMock

    app = FastAPI()
    app.include_router(router)

    repo = AsyncMock()
    repo.get_chat = AsyncMock(return_value=None)
    repo.get_messages = AsyncMock(return_value=[])

    app.dependency_overrides[get_chat_repo] = lambda: repo
    app.dependency_overrides[get_auth_context] = lambda: OrchidAuthContext(
        access_token="t", tenant_key="t1", user_id="u1"
    )

    client = TestClient(app)

    # Limit > ceiling — 422 from FastAPI's query-parameter validator.
    resp = client.get("/chats/c1/messages?limit=999999")
    assert resp.status_code == 422

    # Limit = 0 — disallowed (``ge=1``).
    resp = client.get("/chats/c1/messages?limit=0")
    assert resp.status_code == 422

    # Negative offset — disallowed (``ge=0``).
    resp = client.get("/chats/c1/messages?offset=-1")
    assert resp.status_code == 422

    # Limit at the ceiling — accepted (chat-not-found 404 from the handler).
    resp = client.get("/chats/c1/messages?limit=500")
    assert resp.status_code == 404
