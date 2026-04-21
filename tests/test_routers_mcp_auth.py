"""Tests for the MCP OAuth authorization router.

Handlers now receive ``runtime``/``state_store``/``token_store`` via
FastAPI ``Depends`` — tests pass mocks directly through the function
parameters.  The 503 behaviour for "unconfigured store" is covered in
``tests/test_context.py`` on the dependency helpers.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from orchid_ai.core.mcp import OrchidMCPTokenRecord
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.auth_registry import OrchidMCPAuthRegistry, OrchidMCPOAuthServerInfo
from orchid_ai.mcp.oauth_state import OrchidInMemoryOAuthStateStore

from orchid_api.routers.mcp_auth import (
    get_authorize_url,
    list_mcp_auth_servers,
    oauth_callback,
    revoke_token,
)


@pytest.fixture
def auth():
    return OrchidAuthContext(access_token="tok", tenant_key="t1", user_id="u1")


@pytest.fixture
def mock_store():
    store = AsyncMock()
    store.get_token = AsyncMock(return_value=None)
    store.save_token = AsyncMock()
    store.delete_token = AsyncMock(return_value=True)
    return store


@pytest.fixture
def registry():
    return OrchidMCPAuthRegistry(
        _servers={
            "ext-crm": OrchidMCPOAuthServerInfo(
                server_name="ext-crm",
                client_id="orchid-crm",
                authorization_endpoint="https://auth.crm.example.com/authorize",
                token_endpoint="https://auth.crm.example.com/token",
                scopes="openid crm.read",
                issuer="",
                agent_names=("sales", "support"),
            ),
        }
    )


@pytest.fixture
def settings():
    s = MagicMock()
    s.api_base_url = "http://localhost:8000"
    return s


def _runtime(registry: OrchidMCPAuthRegistry | None) -> MagicMock:
    rt = MagicMock()
    rt.mcp_auth_registry = registry
    return rt


# ── list_mcp_auth_servers ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_servers_unauthorized(auth, mock_store, registry):
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(registry),
        store=mock_store,
    )
    assert len(result) == 1
    assert result[0]["server_name"] == "ext-crm"
    assert result[0]["authorized"] is False
    assert result[0]["client_id"] == "orchid-crm"
    assert "sales" in result[0]["agent_names"]


@pytest.mark.asyncio
async def test_list_servers_authorized(auth, mock_store, registry):
    mock_store.get_token.return_value = OrchidMCPTokenRecord(
        server_name="ext-crm",
        tenant_id="t1",
        user_id="u1",
        access_token="valid-token",
        expires_at=time.time() + 3600,
    )
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(registry),
        store=mock_store,
    )
    assert result[0]["authorized"] is True


@pytest.mark.asyncio
async def test_list_servers_empty_registry(auth):
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(OrchidMCPAuthRegistry()),
        store=None,
    )
    assert result == []


@pytest.mark.asyncio
async def test_list_servers_no_registry(auth):
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(None),
        store=None,
    )
    assert result == []


# ── get_authorize_url ──────────────────────────────────────


@pytest.mark.asyncio
async def test_authorize_returns_url(auth, registry, settings):
    result = await get_authorize_url(
        "ext-crm",
        auth=auth,
        settings=settings,
        runtime=_runtime(registry),
        state_store=OrchidInMemoryOAuthStateStore(),
    )
    assert "authorize_url" in result
    assert "state" in result
    assert "auth.crm.example.com/authorize" in result["authorize_url"]
    assert "code_challenge" in result["authorize_url"]
    assert "orchid-crm" in result["authorize_url"]


@pytest.mark.asyncio
async def test_authorize_unknown_server(auth, registry, settings):
    with pytest.raises(HTTPException) as exc_info:
        await get_authorize_url(
            "nonexistent",
            auth=auth,
            settings=settings,
            runtime=_runtime(registry),
            state_store=OrchidInMemoryOAuthStateStore(),
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_authorize_no_registry(auth, settings):
    with pytest.raises(HTTPException) as exc_info:
        await get_authorize_url(
            "ext-crm",
            auth=auth,
            settings=settings,
            runtime=_runtime(None),
            state_store=OrchidInMemoryOAuthStateStore(),
        )
    assert exc_info.value.status_code == 404


# ── oauth_callback ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_invalid_state(settings):
    result = await oauth_callback(
        code="abc",
        state="invalid-state",
        settings=settings,
        runtime=_runtime(None),
        state_store=OrchidInMemoryOAuthStateStore(),
        token_store=None,
    )
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_callback_missing_code(settings):
    result = await oauth_callback(
        code="",
        state="something",
        settings=settings,
        runtime=_runtime(None),
        state_store=OrchidInMemoryOAuthStateStore(),
        token_store=None,
    )
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_callback_error_param(settings):
    result = await oauth_callback(
        error="access_denied",
        settings=settings,
        runtime=_runtime(None),
        state_store=OrchidInMemoryOAuthStateStore(),
        token_store=None,
    )
    assert result.status_code == 400


# ── revoke_token ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_existing_token(auth, mock_store):
    mock_store.delete_token.return_value = True
    await revoke_token("ext-crm", auth=auth, store=mock_store)
    mock_store.delete_token.assert_called_once_with("t1", "u1", "ext-crm")


@pytest.mark.asyncio
async def test_revoke_nonexistent_raises_404(auth, mock_store):
    mock_store.delete_token.return_value = False
    with pytest.raises(HTTPException) as exc_info:
        await revoke_token("ext-crm", auth=auth, store=mock_store)
    assert exc_info.value.status_code == 404
