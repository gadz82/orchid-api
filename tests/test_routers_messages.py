"""Tests for orchid_api.routers.messages — send message and upload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import AuthContext

from orchid_api.routers.messages import send_chat_message, upload_documents
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return AuthContext(access_token="tok", tenant_key="t1", user_id="u1")


def _patch_ctx():
    """Patch app_ctx in both _helpers and messages."""
    return (
        patch("orchid_api.routers._helpers.app_ctx"),
        patch("orchid_api.routers.messages.app_ctx"),
    )


def _setup(h_ctx, m_ctx, *, graph=None, chat_repo=None, reader=None, session=None):
    g = graph or AsyncMock()
    cr = chat_repo or AsyncMock()
    r = reader or MagicMock()
    for ctx in (h_ctx, m_ctx):
        ctx.graph = g
        ctx.chat_repo = cr
        ctx.runtime.get_reader.return_value = r
        ctx.runtime.mcp_auth_registry = None
        ctx.mcp_token_store = None
    if session:
        cr.get_chat = AsyncMock(return_value=session)
    return g, cr


# ── send_chat_message ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_no_graph(auth):
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _setup(h, m)
        h.graph = None
        m.graph = None
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("c1", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_send_message_no_chat_repo(auth):
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _setup(h, m)
        h.chat_repo = None
        m.chat_repo = None
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("c1", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_send_message_chat_not_found(auth):
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _, cr = _setup(h, m)
        cr.get_chat = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("c1", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_send_message_wrong_user(auth, sample_session):
    sample_session.user_id = "other"
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _setup(h, m, session=sample_session)
        with pytest.raises(HTTPException) as exc:
            await send_chat_message("chat-001", message="Hi", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_send_message_success(auth, sample_session):
    mg = AsyncMock()
    mg.ainvoke.return_value = {"final_response": "Hello!", "active_agents": ["helper"]}
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _, cr = _setup(h, m, graph=mg, session=sample_session)
        cr.get_messages = AsyncMock(return_value=[])
        result = await send_chat_message("chat-001", message="Hi", files=[], auth=auth, settings=Settings())
    assert result.response == "Hello!"
    assert result.agents_used == ["helper"]


@pytest.mark.asyncio
async def test_send_message_persists(auth, sample_session):
    mg = AsyncMock()
    mg.ainvoke.return_value = {"final_response": "Reply", "active_agents": []}
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _, cr = _setup(h, m, graph=mg, session=sample_session)
        cr.get_messages = AsyncMock(return_value=[])
        await send_chat_message("chat-001", message="Hello", files=[], auth=auth, settings=Settings())
    calls = cr.add_message.call_args_list
    assert len(calls) == 2
    assert calls[0].args == ("chat-001", "user", "Hello")
    assert calls[1].args == ("chat-001", "assistant", "Reply")


@pytest.mark.asyncio
async def test_send_message_auto_title(auth, sample_session):
    mg = AsyncMock()
    mg.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _, cr = _setup(h, m, graph=mg, session=sample_session)
        cr.get_messages = AsyncMock(return_value=[])
        await send_chat_message("chat-001", message="Tell me about LeBron", files=[], auth=auth, settings=Settings())
    cr.update_title.assert_called_once()
    assert "LeBron" in cr.update_title.call_args.args[1]


@pytest.mark.asyncio
async def test_send_message_no_auto_title_with_history(auth, sample_session, sample_message):
    mg = AsyncMock()
    mg.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    p1, p2 = _patch_ctx()
    with p1 as h, p2 as m:
        _, cr = _setup(h, m, graph=mg, session=sample_session)
        cr.get_messages = AsyncMock(return_value=[sample_message])
        await send_chat_message("chat-001", message="Follow up", files=[], auth=auth, settings=Settings())
    cr.update_title.assert_not_called()


# ── upload_documents ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_no_writer(auth):
    with patch("orchid_api.routers.messages.app_ctx") as ctx:
        ctx.runtime.get_reader.return_value = object()
        ctx.chat_repo = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await upload_documents("c1", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_upload_no_chat_repo(auth):
    with (
        patch("orchid_api.routers.messages.app_ctx") as ctx,
        patch("orchid_api.routers.messages.isinstance", return_value=True),
    ):
        ctx.runtime.get_reader.return_value = MagicMock()
        ctx.chat_repo = None
        with pytest.raises(HTTPException) as exc:
            await upload_documents("c1", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_upload_chat_not_found(auth):
    with (
        patch("orchid_api.routers.messages.app_ctx") as ctx,
        patch("orchid_api.routers.messages.isinstance", return_value=True),
    ):
        ctx.runtime.get_reader.return_value = MagicMock()
        ctx.chat_repo = AsyncMock()
        ctx.chat_repo.get_chat = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await upload_documents("c1", files=[], auth=auth, settings=Settings())
        assert exc.value.status_code == 404
