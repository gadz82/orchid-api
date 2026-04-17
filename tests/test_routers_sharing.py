"""Tests for orchid_api.routers.sharing — chat sharing endpoint.

Handler receives ``chat_repo``/``runtime``/``agents_config`` via
FastAPI ``Depends`` — tests pass mocks directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import AuthContext

from orchid_api.routers.sharing import share_chat
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return AuthContext(access_token="tok", tenant_key="t1", user_id="u1")


def _runtime(reader: MagicMock | None = None) -> MagicMock:
    rt = MagicMock()
    rt.get_reader.return_value = reader if reader is not None else MagicMock()
    return rt


def _agents_config(namespaces: list[str] | None = None) -> MagicMock:
    cfg = MagicMock()
    agents: dict[str, MagicMock] = {}
    for ns in namespaces or []:
        agent = MagicMock()
        agent.rag.enabled = True
        agent.rag.namespace = ns
        agents[ns] = agent
    cfg.agents = agents
    return cfg


@pytest.mark.asyncio
async def test_share_unsupported_backend(auth):
    # A plain MagicMock is NOT a VectorStoreRepository instance.
    with pytest.raises(HTTPException) as exc:
        await share_chat(
            "chat-1",
            auth=auth,
            settings=Settings(),
            chat_repo=AsyncMock(),
            runtime=_runtime(reader=MagicMock()),
            agents_config=_agents_config(),
        )
    assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_share_chat_not_found(auth):
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=None)
    reader = MagicMock()
    with patch("orchid_api.routers.sharing.isinstance", return_value=True):
        reader.supports_scope_promotion = True
        with pytest.raises(HTTPException) as exc:
            await share_chat(
                "chat-1",
                auth=auth,
                settings=Settings(),
                chat_repo=chat_repo,
                runtime=_runtime(reader=reader),
                agents_config=_agents_config(),
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_share_wrong_user(auth, sample_session):
    sample_session.user_id = "other-user"
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    reader = MagicMock()
    reader.supports_scope_promotion = True
    with patch("orchid_api.routers.sharing.isinstance", return_value=True):
        with pytest.raises(HTTPException) as exc:
            await share_chat(
                "chat-001",
                auth=auth,
                settings=Settings(),
                chat_repo=chat_repo,
                runtime=_runtime(reader=reader),
                agents_config=_agents_config(),
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_share_success(auth, sample_session):
    reader = MagicMock()
    reader.supports_scope_promotion = True
    reader.promote_scope = AsyncMock(return_value=5)

    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)

    with patch("orchid_api.routers.sharing.isinstance", return_value=True):
        result = await share_chat(
            "chat-001",
            auth=auth,
            settings=Settings(),
            chat_repo=chat_repo,
            runtime=_runtime(reader=reader),
            agents_config=_agents_config(["knowledge"]),
        )

    assert result["status"] == "shared"
    assert result["chat_id"] == "chat-001"
    assert result["points_promoted"] > 0
    chat_repo.mark_shared.assert_called_once_with("chat-001")
