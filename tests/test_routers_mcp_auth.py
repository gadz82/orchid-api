"""Tests for the MCP OAuth authorization router.

Follows the same pattern as test_routers_chats.py — directly calls
route functions with a patched app_ctx, avoiding lifespan startup.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid_ai.core.mcp import MCPTokenRecord
from orchid_ai.core.state import AuthContext
from orchid_ai.mcp.auth_registry import MCPAuthRegistry, MCPOAuthServerInfo

from orchid_api.routers.mcp_auth import (
    get_authorize_url,
    list_mcp_auth_servers,
    oauth_callback,
    revoke_token,
)


@pytest.fixture
def auth():
    return AuthContext(access_token="tok", tenant_key="t1", user_id="u1")


@pytest.fixture
def mock_store():
    store = AsyncMock()
    store.get_token = AsyncMock(return_value=None)
    store.save_token = AsyncMock()
    store.delete_token = AsyncMock(return_value=True)
    return store


@pytest.fixture
def registry():
    return MCPAuthRegistry(
        _servers={
            "ext-crm": MCPOAuthServerInfo(
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


# ── list_mcp_auth_servers ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_servers_unauthorized(auth, mock_store, registry):
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.runtime.mcp_auth_registry = registry
        ctx.mcp_token_store = mock_store
        result = await list_mcp_auth_servers(auth=auth)
    assert len(result) == 1
    assert result[0]["server_name"] == "ext-crm"
    assert result[0]["authorized"] is False
    assert result[0]["client_id"] == "orchid-crm"
    assert "sales" in result[0]["agent_names"]


@pytest.mark.asyncio
async def test_list_servers_authorized(auth, mock_store, registry):
    mock_store.get_token.return_value = MCPTokenRecord(
        server_name="ext-crm",
        tenant_id="t1",
        user_id="u1",
        access_token="valid-token",
        expires_at=time.time() + 3600,
    )
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.runtime.mcp_auth_registry = registry
        ctx.mcp_token_store = mock_store
        result = await list_mcp_auth_servers(auth=auth)
    assert result[0]["authorized"] is True


@pytest.mark.asyncio
async def test_list_servers_empty_registry(auth):
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.runtime.mcp_auth_registry = MCPAuthRegistry()
        result = await list_mcp_auth_servers(auth=auth)
    assert result == []


@pytest.mark.asyncio
async def test_list_servers_no_registry(auth):
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.runtime.mcp_auth_registry = None
        result = await list_mcp_auth_servers(auth=auth)
    assert result == []


# ── get_authorize_url ──────────────────────────────────────


@pytest.mark.asyncio
async def test_authorize_returns_url(auth, registry, settings):
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.runtime.mcp_auth_registry = registry
        result = await get_authorize_url("ext-crm", auth=auth, settings=settings)
    assert "authorize_url" in result
    assert "state" in result
    assert "auth.crm.example.com/authorize" in result["authorize_url"]
    assert "code_challenge" in result["authorize_url"]
    assert "orchid-crm" in result["authorize_url"]


@pytest.mark.asyncio
async def test_authorize_unknown_server(auth, registry, settings):
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.runtime.mcp_auth_registry = registry
        with pytest.raises(HTTPException) as exc_info:
            await get_authorize_url("nonexistent", auth=auth, settings=settings)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_authorize_no_registry(auth, settings):
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.runtime.mcp_auth_registry = None
        with pytest.raises(HTTPException) as exc_info:
            await get_authorize_url("ext-crm", auth=auth, settings=settings)
    assert exc_info.value.status_code == 404


# ── oauth_callback ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_invalid_state(settings):
    result = await oauth_callback(code="abc", state="invalid-state", settings=settings)
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_callback_missing_code(settings):
    result = await oauth_callback(code="", state="something", settings=settings)
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_callback_error_param(settings):
    result = await oauth_callback(error="access_denied", settings=settings)
    assert result.status_code == 400


# ── revoke_token ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_existing_token(auth, mock_store):
    mock_store.delete_token.return_value = True
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.mcp_token_store = mock_store
        await revoke_token("ext-crm", auth=auth)
    mock_store.delete_token.assert_called_once_with("t1", "u1", "ext-crm")


@pytest.mark.asyncio
async def test_revoke_nonexistent_raises_404(auth, mock_store):
    mock_store.delete_token.return_value = False
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.mcp_token_store = mock_store
        with pytest.raises(HTTPException) as exc_info:
            await revoke_token("ext-crm", auth=auth)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_revoke_no_store_raises_503(auth):
    with patch("orchid_api.routers.mcp_auth.app_ctx") as ctx:
        ctx.mcp_token_store = None
        with pytest.raises(HTTPException) as exc_info:
            await revoke_token("ext-crm", auth=auth)
    assert exc_info.value.status_code == 503
