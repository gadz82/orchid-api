"""Tests for ``orchid_api.routers.mcp_gateway_state`` — the shared
OAuth-state store for the inbound MCP gateway.

Coverage breaks into three slices:

- :func:`require_service_token` — the shared-secret gate (503 / 401 /
  pass-through).
- :func:`_require_store` — the store-readiness gate (503 when the
  runtime hasn't wired up the gateway-state store, typed return
  otherwise).
- Per-endpoint happy-path + not-found round-trips — exercising each
  handler against an in-memory SQLite store so we cover both the
  Pydantic ↔ dataclass conversions and the wiring into
  ``app_ctx.runtime.mcp_gateway_client_store``.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from orchid_ai.persistence.mcp_gateway_state_sqlite import (
    OrchidSQLiteMCPGatewayStateStore,
)

from orchid_api.context import app_ctx
from orchid_api.routers.mcp_gateway_state import (
    GatewayAuthCodeDTO,
    GatewayAuthCodePatch,
    GatewayClientDTO,
    GatewayTokenDTO,
    TokenLookup,
    UpstreamStateLookup,
    _require_store,
    consume_auth_code,
    get_client,
    introspect_token,
    lookup_auth_code_by_upstream_state,
    patch_auth_code,
    put_auth_code,
    register_client,
    require_service_token,
    revoke_token,
)
from orchid_api.settings import Settings


# ── Fixtures ──────────────────────────────────────────────────


@asynccontextmanager
async def _wired_store():
    """Yield a ready-to-use SQLite gateway-state store wired into
    ``app_ctx.orchid`` for the duration of the context.

    Uses :class:`SimpleNamespace` instead of the real :class:`Orchid`
    because the router only looks at
    ``app_ctx.runtime.mcp_gateway_client_store`` — instantiating the
    full facade would pull in the graph / reader / LLM factory just
    to exercise three URL paths.
    """
    store = OrchidSQLiteMCPGatewayStateStore(dsn=":memory:")
    await store.init_db()

    previous = app_ctx.orchid
    app_ctx.orchid = SimpleNamespace(
        runtime=SimpleNamespace(mcp_gateway_client_store=store),
    )  # type: ignore[assignment]
    try:
        yield store
    finally:
        app_ctx.orchid = previous
        await store.close()


@pytest.fixture
def reset_app_ctx_orchid():
    """Snapshot + restore ``app_ctx.orchid`` so tests never leak state."""
    previous = app_ctx.orchid
    try:
        yield
    finally:
        app_ctx.orchid = previous


def _client_dto(client_id: str = "cli-abc") -> GatewayClientDTO:
    return GatewayClientDTO(
        client_id=client_id,
        redirect_uris=["http://localhost:8765/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="MCP Inspector",
    )


def _auth_code_dto(
    code: str = "authcode-xyz",
    *,
    upstream_state: str = "ust-123",
) -> GatewayAuthCodeDTO:
    return GatewayAuthCodeDTO(
        code=code,
        client_id="cli-abc",
        redirect_uri="http://localhost:8765/callback",
        code_challenge="challenge-abc",
        code_challenge_method="S256",
        upstream_state=upstream_state,
        upstream_code_verifier="verifier-def",
        scopes=["mcp.read"],
        client_state="client-echo",
    )


def _token_dto(
    access_token: str = "at-1",
    refresh_token: str = "rt-1",
    *,
    expires_in: float = 3600.0,
) -> GatewayTokenDTO:
    return GatewayTokenDTO(
        access_token=access_token,
        refresh_token=refresh_token,
        client_id="cli-abc",
        subject="u-42",
        identity={"sub": "u-42", "email": "a@b.c"},
        scopes=["mcp.read"],
        expires_at=time.time() + expires_in,
    )


# ── Service-token guard ──────────────────────────────────────


class TestRequireServiceToken:
    @pytest.mark.asyncio
    async def test_503_when_token_setting_is_empty(self):
        """Empty setting → endpoint group disabled (503)."""
        with pytest.raises(HTTPException) as exc:
            await require_service_token(
                authorization="Bearer anything",
                settings=Settings(mcp_gateway_state_service_token=""),
            )
        assert exc.value.status_code == 503
        assert "disabled" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_401_when_header_missing(self):
        with pytest.raises(HTTPException) as exc:
            await require_service_token(
                authorization=None,
                settings=Settings(mcp_gateway_state_service_token="sek"),
            )
        assert exc.value.status_code == 401
        assert "bearer" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_401_when_scheme_is_not_bearer(self):
        with pytest.raises(HTTPException) as exc:
            await require_service_token(
                authorization="Basic abcdef",
                settings=Settings(mcp_gateway_state_service_token="sek"),
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_401_when_token_does_not_match(self):
        with pytest.raises(HTTPException) as exc:
            await require_service_token(
                authorization="Bearer wrong",
                settings=Settings(mcp_gateway_state_service_token="sek"),
            )
        assert exc.value.status_code == 401
        assert "invalid" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_passes_when_token_matches(self):
        # No exception == pass.
        await require_service_token(
            authorization="Bearer sek",
            settings=Settings(mcp_gateway_state_service_token="sek"),
        )

    @pytest.mark.asyncio
    async def test_bearer_scheme_is_case_insensitive(self):
        """``Authorization: bearer ...`` is valid per RFC 6750 — the
        scheme token is case-insensitive.
        """
        await require_service_token(
            authorization="bearer sek",
            settings=Settings(mcp_gateway_state_service_token="sek"),
        )


# ── Store guard ───────────────────────────────────────────────


class TestRequireStore:
    def test_503_when_orchid_is_none(self, reset_app_ctx_orchid):
        app_ctx.orchid = None
        with pytest.raises(HTTPException) as exc:
            _require_store()
        assert exc.value.status_code == 503
        assert "not initialised" in str(exc.value.detail).lower()

    def test_503_when_store_is_none(self, reset_app_ctx_orchid):
        app_ctx.orchid = SimpleNamespace(
            runtime=SimpleNamespace(mcp_gateway_client_store=None),
        )  # type: ignore[assignment]
        with pytest.raises(HTTPException) as exc:
            _require_store()
        assert exc.value.status_code == 503

    def test_returns_store_when_wired(self, reset_app_ctx_orchid):
        sentinel = object()
        app_ctx.orchid = SimpleNamespace(
            runtime=SimpleNamespace(mcp_gateway_client_store=sentinel),
        )  # type: ignore[assignment]
        assert _require_store() is sentinel


# ── Clients endpoints ────────────────────────────────────────


class TestClientEndpoints:
    @pytest.mark.asyncio
    async def test_register_and_get_round_trip(self):
        async with _wired_store():
            await register_client(_client_dto())
            loaded = await get_client("cli-abc")
            assert isinstance(loaded, GatewayClientDTO)
            assert loaded.client_id == "cli-abc"
            assert loaded.client_name == "MCP Inspector"
            assert loaded.redirect_uris == ["http://localhost:8765/callback"]

    @pytest.mark.asyncio
    async def test_get_404_when_missing(self):
        async with _wired_store():
            with pytest.raises(HTTPException) as exc:
                await get_client("never-registered")
            assert exc.value.status_code == 404


# ── Auth-code endpoints ──────────────────────────────────────


class TestAuthCodeEndpoints:
    @pytest.mark.asyncio
    async def test_put_and_lookup_by_upstream_state(self):
        async with _wired_store():
            await put_auth_code(_auth_code_dto())
            loaded = await lookup_auth_code_by_upstream_state(
                UpstreamStateLookup(upstream_state="ust-123"),
            )
            assert isinstance(loaded, GatewayAuthCodeDTO)
            assert loaded.code == "authcode-xyz"
            assert loaded.scopes == ["mcp.read"]

    @pytest.mark.asyncio
    async def test_lookup_404_when_upstream_state_unknown(self):
        async with _wired_store():
            with pytest.raises(HTTPException) as exc:
                await lookup_auth_code_by_upstream_state(
                    UpstreamStateLookup(upstream_state="unknown"),
                )
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_is_partial(self):
        """PATCH only touches fields the caller sends; omitted fields
        stay at their previous values.
        """
        async with _wired_store():
            await put_auth_code(_auth_code_dto())
            # First patch: fill in IdP tokens only.
            await patch_auth_code(
                "authcode-xyz",
                GatewayAuthCodePatch(
                    idp_access_token="at-v1",
                    idp_refresh_token="rt-v1",
                    idp_expires_at=time.time() + 600,
                ),
            )
            # Second patch: set identity only — IdP tokens must survive.
            await patch_auth_code(
                "authcode-xyz",
                GatewayAuthCodePatch(identity={"sub": "u-42"}),
            )

            loaded = await lookup_auth_code_by_upstream_state(
                UpstreamStateLookup(upstream_state="ust-123"),
            )
            assert loaded.identity == {"sub": "u-42"}
            assert loaded.idp_access_token == "at-v1"
            assert loaded.idp_refresh_token == "rt-v1"
            assert loaded.idp_expires_at > 0.0

    @pytest.mark.asyncio
    async def test_consume_is_one_shot(self):
        async with _wired_store():
            await put_auth_code(_auth_code_dto())
            first = await consume_auth_code("authcode-xyz")
            assert isinstance(first, GatewayAuthCodeDTO)
            assert first.code == "authcode-xyz"

            # Second consume → 404.
            with pytest.raises(HTTPException) as exc:
                await consume_auth_code("authcode-xyz")
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_consume_404_when_unknown(self):
        async with _wired_store():
            with pytest.raises(HTTPException) as exc:
                await consume_auth_code("never-put")
            assert exc.value.status_code == 404


# ── Token endpoints ──────────────────────────────────────────


class TestTokenEndpoints:
    @pytest.mark.asyncio
    async def test_issue_and_introspect_by_access_token(self):
        async with _wired_store():
            # Directly exercise the ``issue`` handler.
            from orchid_api.routers.mcp_gateway_state import issue_token

            await issue_token(_token_dto())
            loaded = await introspect_token(TokenLookup(access_token="at-1"))
            assert isinstance(loaded, GatewayTokenDTO)
            assert loaded.subject == "u-42"
            assert loaded.identity == {"sub": "u-42", "email": "a@b.c"}

    @pytest.mark.asyncio
    async def test_introspect_by_refresh_token(self):
        async with _wired_store():
            from orchid_api.routers.mcp_gateway_state import issue_token

            await issue_token(_token_dto())
            loaded = await introspect_token(TokenLookup(refresh_token="rt-1"))
            assert loaded.access_token == "at-1"

    @pytest.mark.asyncio
    async def test_introspect_400_when_both_fields_given(self):
        """The lookup semantics are XOR — both fields is user error,
        return 400 so the caller fixes its request.
        """
        async with _wired_store():
            with pytest.raises(HTTPException) as exc:
                await introspect_token(
                    TokenLookup(access_token="at-1", refresh_token="rt-1"),
                )
            assert exc.value.status_code == 400
            assert "exactly one" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_introspect_400_when_neither_field_given(self):
        async with _wired_store():
            with pytest.raises(HTTPException) as exc:
                await introspect_token(TokenLookup())
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_introspect_404_when_token_unknown(self):
        async with _wired_store():
            with pytest.raises(HTTPException) as exc:
                await introspect_token(TokenLookup(access_token="never-issued"))
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_introspect_404_when_token_expired(self):
        """Expired tokens must look up as 404 — the gateway only sees
        "token not found", same response as for never-issued tokens.
        """
        async with _wired_store():
            from orchid_api.routers.mcp_gateway_state import issue_token

            await issue_token(_token_dto(expires_in=-10))  # already expired
            with pytest.raises(HTTPException) as exc:
                await introspect_token(TokenLookup(access_token="at-1"))
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_removes_token(self):
        async with _wired_store():
            from orchid_api.routers.mcp_gateway_state import issue_token

            await issue_token(_token_dto())
            await revoke_token("at-1")
            with pytest.raises(HTTPException) as exc:
                await introspect_token(TokenLookup(access_token="at-1"))
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_is_idempotent(self):
        """Deleting an already-deleted token must not raise — the HTTP
        contract is "state reaches ``gone``", not "we just removed a row".
        """
        async with _wired_store():
            # Never issued — revoke returns silently.
            await revoke_token("never-issued")


# ── DTO round-trips ──────────────────────────────────────────


class TestDTORoundTrips:
    """The DTOs convert Pydantic ↔ dataclass both ways.  Regressions
    here would silently corrupt the wire format — worth a smoke test
    independent of the handlers.
    """

    def test_client_dto_round_trip(self):
        dto = _client_dto()
        record = dto.to_record()
        back = GatewayClientDTO.from_record(record)
        assert back.model_dump() == dto.model_dump()

    def test_auth_code_dto_round_trip(self):
        dto = _auth_code_dto()
        record = dto.to_record()
        back = GatewayAuthCodeDTO.from_record(record)
        assert back.model_dump() == dto.model_dump()

    def test_token_dto_round_trip(self):
        dto = _token_dto()
        record = dto.to_record()
        back = GatewayTokenDTO.from_record(record)
        assert back.model_dump() == dto.model_dump()
