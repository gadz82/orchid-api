"""Tests for the MCP OAuth authorization router (MCP 2025-03-26 spec).

Handlers now receive ``runtime``/``state_store``/``token_store``/
``registration_store`` via FastAPI ``Depends`` — tests pass mocks
directly through the function parameters.  The 503 behaviour for
"unconfigured store" is covered in ``tests/test_context.py`` on the
dependency helpers.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from orchid_ai.core.mcp import OrchidMCPClientRegistration, OrchidMCPTokenRecord
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
def mock_token_store():
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
                url="https://crm.example.com/mcp",
                agent_names=("sales", "support"),
            ),
        }
    )


@pytest.fixture
def registration():
    return OrchidMCPClientRegistration(
        server_name="ext-crm",
        authorization_endpoint="https://auth.crm.example.com/authorize",
        token_endpoint="https://auth.crm.example.com/token",
        registration_endpoint="https://auth.crm.example.com/register",
        issuer="https://auth.crm.example.com",
        scopes_supported="openid crm.read",
        token_endpoint_auth_methods_supported="client_secret_post",
        client_id="dyn-client-xyz",
        client_secret="s3kr3t",
    )


@pytest.fixture
def registration_store(registration):
    """In-memory registration store seeded with ``registration``."""
    store = AsyncMock()
    store.get = AsyncMock(return_value=registration)
    store.save = AsyncMock()
    store.delete = AsyncMock(return_value=True)
    return store


@pytest.fixture
def empty_registration_store():
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    return store


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
async def test_list_servers_unauthorized(auth, mock_token_store, registry, registration_store):
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(registry),
        token_store=mock_token_store,
        registration_store=registration_store,
    )
    assert len(result) == 1
    entry = result[0]
    assert entry["server_name"] == "ext-crm"
    assert entry["authorized"] is False
    assert entry["discovered"] is True
    assert entry["scopes"] == "openid crm.read"
    assert "sales" in entry["agent_names"]


@pytest.mark.asyncio
async def test_list_servers_authorized(auth, mock_token_store, registry, registration_store):
    mock_token_store.get_token.return_value = OrchidMCPTokenRecord(
        server_name="ext-crm",
        tenant_id="t1",
        user_id="u1",
        access_token="valid-token",
        expires_at=time.time() + 3600,
    )
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(registry),
        token_store=mock_token_store,
        registration_store=registration_store,
    )
    assert result[0]["authorized"] is True


@pytest.mark.asyncio
async def test_list_servers_not_yet_discovered(auth, mock_token_store, registry, empty_registration_store):
    """Registry knows the server but discovery hasn't run — ``discovered: False``."""
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(registry),
        token_store=mock_token_store,
        registration_store=empty_registration_store,
    )
    assert result[0]["discovered"] is False


@pytest.mark.asyncio
async def test_list_servers_empty_registry(auth):
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(OrchidMCPAuthRegistry()),
        token_store=None,
        registration_store=None,
    )
    assert result == []


@pytest.mark.asyncio
async def test_list_servers_no_registry(auth):
    result = await list_mcp_auth_servers(
        auth=auth,
        runtime=_runtime(None),
        token_store=None,
        registration_store=None,
    )
    assert result == []


# ── get_authorize_url ──────────────────────────────────────


@pytest.mark.asyncio
async def test_authorize_returns_url(auth, registry, registration_store, settings):
    result = await get_authorize_url(
        "ext-crm",
        auth=auth,
        settings=settings,
        runtime=_runtime(registry),
        state_store=OrchidInMemoryOAuthStateStore(),
        registration_store=registration_store,
    )
    assert "authorize_url" in result
    assert "state" in result
    assert "auth.crm.example.com/authorize" in result["authorize_url"]
    assert "code_challenge" in result["authorize_url"]
    assert "dyn-client-xyz" in result["authorize_url"]


@pytest.mark.asyncio
async def test_authorize_unknown_server(auth, registry, registration_store, settings):
    with pytest.raises(HTTPException) as exc_info:
        await get_authorize_url(
            "nonexistent",
            auth=auth,
            settings=settings,
            runtime=_runtime(registry),
            state_store=OrchidInMemoryOAuthStateStore(),
            registration_store=registration_store,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_authorize_no_registry(auth, registration_store, settings):
    with pytest.raises(HTTPException) as exc_info:
        await get_authorize_url(
            "ext-crm",
            auth=auth,
            settings=settings,
            runtime=_runtime(None),
            state_store=OrchidInMemoryOAuthStateStore(),
            registration_store=registration_store,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_authorize_auto_discovers_on_first_call(
    auth,
    registry,
    empty_registration_store,
    registration,
    settings,
):
    """No stored registration → endpoint probes the MCP server + runs discovery inline."""
    from unittest.mock import patch

    async def _fake_probe(**_kwargs):
        return "https://crm.example.com/.well-known/oauth-protected-resource"

    async def _fake_ensure(self, *, server_name, resource_metadata_url):
        # Pretend the full RFC chain ran and returned the fixture.
        await empty_registration_store.save(registration)
        empty_registration_store.get = AsyncMock(return_value=registration)
        return registration

    with (
        patch(
            "orchid_api.routers.mcp_auth.probe_mcp_server_for_resource_metadata",
            new=_fake_probe,
        ),
        patch(
            "orchid_api.routers.mcp_auth.OrchidMCPAuthDiscovery.ensure_registration",
            new=_fake_ensure,
        ),
    ):
        result = await get_authorize_url(
            "ext-crm",
            auth=auth,
            settings=settings,
            runtime=_runtime(registry),
            state_store=OrchidInMemoryOAuthStateStore(),
            registration_store=empty_registration_store,
        )
    assert "authorize_url" in result


@pytest.mark.asyncio
async def test_authorize_auto_discovery_failure_surfaces_502(
    auth,
    registry,
    empty_registration_store,
    settings,
):
    """Probe failure bubbles up as HTTP 502 with the discovery reason."""
    from unittest.mock import patch

    from orchid_ai.core.mcp import OrchidMCPDiscoveryError

    async def _probe_fails(**kwargs):
        raise OrchidMCPDiscoveryError(kwargs["server_name"], "server not 401")

    with patch(
        "orchid_api.routers.mcp_auth.probe_mcp_server_for_resource_metadata",
        new=_probe_fails,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_authorize_url(
                "ext-crm",
                auth=auth,
                settings=settings,
                runtime=_runtime(registry),
                state_store=OrchidInMemoryOAuthStateStore(),
                registration_store=empty_registration_store,
            )
    assert exc_info.value.status_code == 502
    assert "not 401" in exc_info.value.detail


# ── oauth_callback ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_invalid_state(settings, empty_registration_store):
    result = await oauth_callback(
        code="abc",
        state="invalid-state",
        settings=settings,
        state_store=OrchidInMemoryOAuthStateStore(),
        token_store=None,
        registration_store=empty_registration_store,
    )
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_callback_missing_code(settings, empty_registration_store):
    result = await oauth_callback(
        code="",
        state="something",
        settings=settings,
        state_store=OrchidInMemoryOAuthStateStore(),
        token_store=None,
        registration_store=empty_registration_store,
    )
    assert result.status_code == 400


@pytest.mark.asyncio
async def test_callback_error_param(settings, empty_registration_store):
    result = await oauth_callback(
        error="access_denied",
        settings=settings,
        state_store=OrchidInMemoryOAuthStateStore(),
        token_store=None,
        registration_store=empty_registration_store,
    )
    assert result.status_code == 400


# ── revoke_token ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_existing_token(auth, mock_token_store):
    mock_token_store.delete_token.return_value = True
    await revoke_token("ext-crm", auth=auth, store=mock_token_store)
    mock_token_store.delete_token.assert_called_once_with("t1", "u1", "ext-crm")


@pytest.mark.asyncio
async def test_revoke_nonexistent_raises_404(auth, mock_token_store):
    mock_token_store.delete_token.return_value = False
    with pytest.raises(HTTPException) as exc_info:
        await revoke_token("ext-crm", auth=auth, store=mock_token_store)
    assert exc_info.value.status_code == 404
