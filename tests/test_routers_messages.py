"""Tests for orchid_api.routers.messages — send message and upload.

Handlers now accept ``chat_repo``/``runtime``/``graph``/``mcp_token_store``
as FastAPI deps; tests pass the mocks directly.  503 behaviour is
covered by ``tests/test_context.py`` (on the dependency helpers).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import OrchidAuthContext

from orchid_api.routers.messages import send_chat_message, upload_documents
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return OrchidAuthContext(access_token="tok", tenant_key="t1", user_id="u1")


def _runtime(reader=None) -> MagicMock:
    rt = MagicMock()
    rt.get_reader.return_value = reader if reader is not None else MagicMock()
    rt.mcp_auth_registry = None
    rt.checkpointer = None
    return rt


# ── send_chat_message ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_chat_not_found(auth):
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await send_chat_message(
            "c1",
            message="Hi",
            files=[],
            auth=auth,
            settings=Settings(),
            chat_repo=chat_repo,
            runtime=_runtime(),
            graph=AsyncMock(),
            mcp_token_store=None,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_send_message_wrong_user(auth, sample_session):
    sample_session.user_id = "other"
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    with pytest.raises(HTTPException) as exc:
        await send_chat_message(
            "chat-001",
            message="Hi",
            files=[],
            auth=auth,
            settings=Settings(),
            chat_repo=chat_repo,
            runtime=_runtime(),
            graph=AsyncMock(),
            mcp_token_store=None,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_send_message_success(auth, sample_session):
    graph = AsyncMock()
    graph.ainvoke.return_value = {"final_response": "Hello!", "active_agents": ["helper"]}
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    chat_repo.get_messages = AsyncMock(return_value=[])

    result = await send_chat_message(
        "chat-001",
        message="Hi",
        files=[],
        auth=auth,
        settings=Settings(),
        chat_repo=chat_repo,
        runtime=_runtime(),
        graph=graph,
        mcp_token_store=None,
    )
    assert result.response == "Hello!"
    assert result.agents_used == ["helper"]


@pytest.mark.asyncio
async def test_send_message_persists(auth, sample_session):
    graph = AsyncMock()
    graph.ainvoke.return_value = {"final_response": "Reply", "active_agents": []}
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    chat_repo.get_messages = AsyncMock(return_value=[])

    await send_chat_message(
        "chat-001",
        message="Hello",
        files=[],
        auth=auth,
        settings=Settings(),
        chat_repo=chat_repo,
        runtime=_runtime(),
        graph=graph,
        mcp_token_store=None,
    )
    calls = chat_repo.add_message.call_args_list
    assert len(calls) == 2
    assert calls[0].args == ("chat-001", "user", "Hello")
    assert calls[1].args == ("chat-001", "assistant", "Reply")


@pytest.mark.asyncio
async def test_send_message_auto_title(auth, sample_session):
    graph = AsyncMock()
    graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    chat_repo.get_messages = AsyncMock(return_value=[])

    await send_chat_message(
        "chat-001",
        message="Tell me about LeBron",
        files=[],
        auth=auth,
        settings=Settings(),
        chat_repo=chat_repo,
        runtime=_runtime(),
        graph=graph,
        mcp_token_store=None,
    )
    chat_repo.update_title.assert_called_once()
    assert "LeBron" in chat_repo.update_title.call_args.args[1]


@pytest.mark.asyncio
async def test_send_message_no_auto_title_with_history(auth, sample_session, sample_message):
    graph = AsyncMock()
    graph.ainvoke.return_value = {"final_response": "ok", "active_agents": []}
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    chat_repo.get_messages = AsyncMock(return_value=[sample_message])

    await send_chat_message(
        "chat-001",
        message="Follow up",
        files=[],
        auth=auth,
        settings=Settings(),
        chat_repo=chat_repo,
        runtime=_runtime(),
        graph=graph,
        mcp_token_store=None,
    )
    chat_repo.update_title.assert_not_called()


# ── upload_documents ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_no_writer(auth):
    with pytest.raises(HTTPException) as exc:
        await upload_documents(
            "c1",
            files=[],
            auth=auth,
            settings=Settings(),
            chat_repo=AsyncMock(),
            runtime=_runtime(reader=object()),  # not a OrchidVectorWriter
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_upload_chat_not_found(auth):
    # Reader implements OrchidVectorWriter (MagicMock() matches via isinstance patch)
    from unittest.mock import patch

    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=None)
    with patch("orchid_api.routers.messages.isinstance", return_value=True):
        with pytest.raises(HTTPException) as exc:
            await upload_documents(
                "c1",
                files=[],
                auth=auth,
                settings=Settings(),
                chat_repo=chat_repo,
                runtime=_runtime(),
            )
        assert exc.value.status_code == 404
