"""``GET /mcp/auth/callback`` post-auth warm hook.

After a successful per-server OAuth code exchange, the callback hands
the freshly-issued token to the warmer so the user's next chat sees
the new server's tools cached up front.  Failures in the warm step
must NEVER replace the success page with an error.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchid_ai.core.mcp import OrchidMCPClientRegistration
from orchid_ai.mcp.oauth_state import OrchidInMemoryOAuthStateStore, OrchidOAuthPendingState

from orchid_api.routers.mcp_auth import oauth_callback


def _registration() -> OrchidMCPClientRegistration:
    return OrchidMCPClientRegistration(
        server_name="ext-crm",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
        registration_endpoint="https://auth.example.com/register",
        issuer="https://auth.example.com",
        scopes_supported="openid crm.read",
        token_endpoint_auth_methods_supported="client_secret_post",
        client_id="dyn-client",
        client_secret="s3kr3t",
    )


def _settings() -> MagicMock:
    s = MagicMock()
    s.api_base_url = "http://localhost:8000"
    return s


async def _seed_state_store() -> tuple[OrchidInMemoryOAuthStateStore, str]:
    store = OrchidInMemoryOAuthStateStore()
    state = "csrf-xyz"
    await store.put(
        state,
        OrchidOAuthPendingState(
            server_name="ext-crm",
            tenant_id="t1",
            user_id="u1",
            code_verifier="verifier",
            token_endpoint="https://auth.example.com/token",
            created_at=time.time(),
        ),
    )
    return store, state


def _httpx_post_returning_token(*_args, **_kwargs):
    """Stub for httpx.AsyncClient.post — returns a token JSON."""
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(
        return_value={
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 3600,
        }
    )
    return response


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient — only ``post`` is used."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return _httpx_post_returning_token()


@pytest.mark.asyncio
async def test_callback_invokes_warm_one_for_user_after_token_save():
    state_store, state = await _seed_state_store()
    registration_store = AsyncMock()
    registration_store.get = AsyncMock(return_value=_registration())
    token_store = AsyncMock()
    token_store.save_token = AsyncMock()

    fake_orchid = MagicMock()
    fake_orchid.session_warmer = MagicMock()
    fake_orchid.session_warmer.warm_one_for_user = AsyncMock()

    with (
        patch("orchid_api.routers.mcp_auth.app_ctx") as mock_ctx,
        patch("httpx.AsyncClient", _FakeAsyncClient),
    ):
        mock_ctx.orchid = fake_orchid
        result = await oauth_callback(
            code="auth-code",
            state=state,
            error="",
            settings=_settings(),
            state_store=state_store,
            token_store=token_store,
            registration_store=registration_store,
        )

    # Token persistence happened before the warm hook.
    token_store.save_token.assert_awaited_once()
    fake_orchid.session_warmer.warm_one_for_user.assert_awaited_once()
    args, _kwargs = fake_orchid.session_warmer.warm_one_for_user.call_args
    # Second positional argument is the server name — sanity check.
    assert args[1] == "ext-crm"
    # And the response is still the success HTML.
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_callback_swallows_warm_failure():
    state_store, state = await _seed_state_store()
    registration_store = AsyncMock()
    registration_store.get = AsyncMock(return_value=_registration())
    token_store = AsyncMock()
    token_store.save_token = AsyncMock()

    fake_orchid = MagicMock()
    fake_orchid.session_warmer = MagicMock()
    fake_orchid.session_warmer.warm_one_for_user = AsyncMock(side_effect=RuntimeError("warmer broken"))

    with (
        patch("orchid_api.routers.mcp_auth.app_ctx") as mock_ctx,
        patch("httpx.AsyncClient", _FakeAsyncClient),
    ):
        mock_ctx.orchid = fake_orchid
        result = await oauth_callback(
            code="auth-code",
            state=state,
            error="",
            settings=_settings(),
            state_store=state_store,
            token_store=token_store,
            registration_store=registration_store,
        )

    # Even though the warm step raised, the user-facing HTML is still 200.
    assert result.status_code == 200
    fake_orchid.session_warmer.warm_one_for_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_skips_warm_when_runtime_missing():
    state_store, state = await _seed_state_store()
    registration_store = AsyncMock()
    registration_store.get = AsyncMock(return_value=_registration())
    token_store = AsyncMock()
    token_store.save_token = AsyncMock()

    with (
        patch("orchid_api.routers.mcp_auth.app_ctx") as mock_ctx,
        patch("httpx.AsyncClient", _FakeAsyncClient),
    ):
        mock_ctx.orchid = None
        result = await oauth_callback(
            code="auth-code",
            state=state,
            error="",
            settings=_settings(),
            state_store=state_store,
            token_store=token_store,
            registration_store=registration_store,
        )

    assert result.status_code == 200
    token_store.save_token.assert_awaited_once()
