"""``/mcp-gateway/state/*`` — shared OAuth-state store for the
inbound MCP gateway.

Without this router an MCP gateway (``orchid-mcp``) keeps its DCR
client registrations, pending authorization codes, and issued
access + refresh tokens in memory or in a local JSON file.  Both
strategies are single-replica by construction: two gateway replicas
fronting the same orchid-api can't mint or validate each other's
tokens.

This router exposes the same three ABCs over HTTP so multiple
gateway replicas share a single state store (Postgres in production,
SQLite in local dev) — the same database orchid-api already uses for
chat storage and the outbound MCP token stores.

**Authentication.**  All endpoints require ``Authorization: Bearer
<token>`` matching
:attr:`orchid_api.settings.Settings.mcp_gateway_state_service_token`.
Leaving the setting blank disables the endpoints entirely (503 at
every path) — the safe posture for environments where no downstream
gateway is expected.

Unlike the sibling ``/auth/exchange-code`` (which relies on PKCE +
upstream code binding for its natural guard), this router's payloads
include **live access tokens** and PKCE verifiers; a shared service
secret is the floor of acceptable protection.  Operators wanting
defence-in-depth layer mTLS or an allow-listed source network on top
at the reverse-proxy tier.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from orchid_ai.core.mcp_gateway_state import (
    OrchidMCPGatewayAuthCode,
    OrchidMCPGatewayClient,
    OrchidMCPGatewayToken,
)

from ..context import app_ctx
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-gateway/state", tags=["mcp-gateway-state"])


# ── Service-token dependency ──────────────────────────────────────


async def require_service_token(
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    """Gate every endpoint behind the shared service token.

    When the operator has not configured a token (empty string in
    settings), the endpoint group is **disabled** — return 503 rather
    than silently accepting any caller.  A downstream gateway that
    tries to use the endpoints in that configuration gets a clear
    error at the first request.
    """
    expected = settings.mcp_gateway_state_service_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "MCP-gateway-state endpoints are disabled.  Set "
                "``MCP_GATEWAY_STATE_SERVICE_TOKEN`` on orchid-api to enable."
            ),
        )
    if authorization is None or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid service token")


# ── Pydantic DTOs ─────────────────────────────────────────────────


class GatewayClientDTO(BaseModel):
    """Wire shape for :class:`OrchidMCPGatewayClient`.  Field names
    match OAuth spec verbatim so the gateway-side TypeScript can
    pass-through verbatim.
    """

    client_id: str
    redirect_uris: list[str]
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str = "none"
    client_name: str = ""
    created_at: float = Field(default_factory=time.time)

    def to_record(self) -> OrchidMCPGatewayClient:
        return OrchidMCPGatewayClient(
            client_id=self.client_id,
            redirect_uris=list(self.redirect_uris),
            grant_types=list(self.grant_types),
            response_types=list(self.response_types),
            token_endpoint_auth_method=self.token_endpoint_auth_method,
            client_name=self.client_name,
            created_at=self.created_at,
        )

    @classmethod
    def from_record(cls, record: OrchidMCPGatewayClient) -> "GatewayClientDTO":
        return cls(
            client_id=record.client_id,
            redirect_uris=list(record.redirect_uris),
            grant_types=list(record.grant_types),
            response_types=list(record.response_types),
            token_endpoint_auth_method=record.token_endpoint_auth_method,
            client_name=record.client_name,
            created_at=record.created_at,
        )


class GatewayAuthCodeDTO(BaseModel):
    """Wire shape for :class:`OrchidMCPGatewayAuthCode`."""

    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    upstream_state: str
    upstream_code_verifier: str
    scopes: list[str]
    client_state: str = ""
    identity: dict[str, Any] | None = None
    idp_access_token: str = ""
    idp_refresh_token: str = ""
    idp_expires_at: float = 0.0
    created_at: float = Field(default_factory=time.time)

    def to_record(self) -> OrchidMCPGatewayAuthCode:
        return OrchidMCPGatewayAuthCode(
            code=self.code,
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
            code_challenge=self.code_challenge,
            code_challenge_method=self.code_challenge_method,
            upstream_state=self.upstream_state,
            upstream_code_verifier=self.upstream_code_verifier,
            scopes=list(self.scopes),
            client_state=self.client_state,
            identity=self.identity,
            idp_access_token=self.idp_access_token,
            idp_refresh_token=self.idp_refresh_token,
            idp_expires_at=self.idp_expires_at,
            created_at=self.created_at,
        )

    @classmethod
    def from_record(cls, record: OrchidMCPGatewayAuthCode) -> "GatewayAuthCodeDTO":
        return cls(
            code=record.code,
            client_id=record.client_id,
            redirect_uri=record.redirect_uri,
            code_challenge=record.code_challenge,
            code_challenge_method=record.code_challenge_method,
            upstream_state=record.upstream_state,
            upstream_code_verifier=record.upstream_code_verifier,
            scopes=list(record.scopes),
            client_state=record.client_state,
            identity=record.identity,
            idp_access_token=record.idp_access_token,
            idp_refresh_token=record.idp_refresh_token,
            idp_expires_at=record.idp_expires_at,
            created_at=record.created_at,
        )


class GatewayAuthCodePatch(BaseModel):
    """Partial-update payload for :meth:`AuthCodeStore.update`.  Any
    ``None`` field is left untouched — the caller only sends the
    columns they want to overwrite.
    """

    identity: dict[str, Any] | None = None
    idp_access_token: str | None = None
    idp_refresh_token: str | None = None
    idp_expires_at: float | None = None


class GatewayTokenDTO(BaseModel):
    """Wire shape for :class:`OrchidMCPGatewayToken`."""

    access_token: str
    refresh_token: str
    client_id: str
    subject: str
    identity: dict[str, Any]
    scopes: list[str]
    expires_at: float
    # Upstream-token columns — see core dataclass.  Defaults keep
    # the DTO backwards-compatible with downstream gateways that
    # haven't rolled their zod schema forward yet.
    idp_access_token: str = ""
    idp_refresh_token: str = ""
    idp_expires_at: float = 0.0

    def to_record(self) -> OrchidMCPGatewayToken:
        return OrchidMCPGatewayToken(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            client_id=self.client_id,
            subject=self.subject,
            identity=dict(self.identity),
            scopes=list(self.scopes),
            expires_at=self.expires_at,
            idp_access_token=self.idp_access_token,
            idp_refresh_token=self.idp_refresh_token,
            idp_expires_at=self.idp_expires_at,
        )

    @classmethod
    def from_record(cls, record: OrchidMCPGatewayToken) -> "GatewayTokenDTO":
        return cls(
            access_token=record.access_token,
            refresh_token=record.refresh_token,
            client_id=record.client_id,
            subject=record.subject,
            identity=dict(record.identity),
            scopes=list(record.scopes),
            expires_at=record.expires_at,
            idp_access_token=record.idp_access_token,
            idp_refresh_token=record.idp_refresh_token,
            idp_expires_at=record.idp_expires_at,
        )


class UpstreamStateLookup(BaseModel):
    upstream_state: str


class TokenLookup(BaseModel):
    """Lookup body for :meth:`TokenStore.get_by_access_token` /
    :meth:`TokenStore.get_by_refresh_token`.  Sending the token in a
    body (not a query string) keeps it out of access logs.
    """

    access_token: str | None = None
    refresh_token: str | None = None


# ── Endpoint helpers ──────────────────────────────────────────────


def _require_store() -> Any:
    """Return the wired gateway-state store or raise 503.

    One concrete instance implements all three ABCs, so we return it
    typed loosely (``Any``) and let the per-endpoint logic pick the
    methods it needs.
    """
    store = app_ctx.runtime.mcp_gateway_client_store if app_ctx.orchid is not None else None
    if store is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "MCP-gateway-state store is not initialised.  Check "
                "``MCP_GATEWAY_STATE_STORE_CLASS`` and ensure orchid-api "
                "completed startup."
            ),
        )
    return store


# ── Clients ───────────────────────────────────────────────────────


@router.post(
    "/clients",
    status_code=204,
    dependencies=[Depends(require_service_token)],
)
async def register_client(payload: GatewayClientDTO) -> None:
    store = _require_store()
    await store.register(payload.to_record())


@router.get(
    "/clients/{client_id}",
    response_model=GatewayClientDTO,
    dependencies=[Depends(require_service_token)],
)
async def get_client(client_id: str) -> GatewayClientDTO:
    store = _require_store()
    record = await store.get(client_id)
    if record is None:
        raise HTTPException(status_code=404, detail="client not found")
    return GatewayClientDTO.from_record(record)


# ── Auth codes ────────────────────────────────────────────────────


@router.post(
    "/auth-codes",
    status_code=204,
    dependencies=[Depends(require_service_token)],
)
async def put_auth_code(payload: GatewayAuthCodeDTO) -> None:
    store = _require_store()
    await store.put(payload.to_record())


@router.post(
    "/auth-codes/lookup-by-upstream-state",
    response_model=GatewayAuthCodeDTO,
    dependencies=[Depends(require_service_token)],
)
async def lookup_auth_code_by_upstream_state(
    body: UpstreamStateLookup,
) -> GatewayAuthCodeDTO:
    store = _require_store()
    record = await store.get_by_upstream_state(body.upstream_state)
    if record is None:
        raise HTTPException(status_code=404, detail="auth code not found")
    return GatewayAuthCodeDTO.from_record(record)


@router.patch(
    "/auth-codes/{code}",
    status_code=204,
    dependencies=[Depends(require_service_token)],
)
async def patch_auth_code(code: str, patch: GatewayAuthCodePatch) -> None:
    store = _require_store()
    await store.update(
        code,
        identity=patch.identity,
        idp_access_token=patch.idp_access_token,
        idp_refresh_token=patch.idp_refresh_token,
        idp_expires_at=patch.idp_expires_at,
    )


@router.post(
    "/auth-codes/{code}/consume",
    response_model=GatewayAuthCodeDTO,
    dependencies=[Depends(require_service_token)],
)
async def consume_auth_code(code: str) -> GatewayAuthCodeDTO:
    store = _require_store()
    record = await store.consume(code)
    if record is None:
        raise HTTPException(status_code=404, detail="auth code not found or already consumed")
    return GatewayAuthCodeDTO.from_record(record)


# ── Tokens ────────────────────────────────────────────────────────


@router.post(
    "/tokens",
    status_code=204,
    dependencies=[Depends(require_service_token)],
)
async def issue_token(payload: GatewayTokenDTO) -> None:
    store = _require_store()
    await store.issue(payload.to_record())


@router.post(
    "/tokens/introspect",
    response_model=GatewayTokenDTO,
    dependencies=[Depends(require_service_token)],
)
async def introspect_token(body: TokenLookup) -> GatewayTokenDTO:
    """Look up an issued token by either ``access_token`` or
    ``refresh_token``.  Returns 400 when neither field is supplied
    (the caller must pick one); returns 404 when the token is
    unknown or has expired (consistent with the memory / file stores).
    """
    if bool(body.access_token) == bool(body.refresh_token):
        raise HTTPException(
            status_code=400,
            detail="Specify exactly one of `access_token` or `refresh_token`",
        )
    store = _require_store()
    if body.access_token:
        record = await store.get_by_access_token(body.access_token)
    else:
        assert body.refresh_token is not None
        record = await store.get_by_refresh_token(body.refresh_token)
    if record is None:
        raise HTTPException(status_code=404, detail="token not found or expired")
    return GatewayTokenDTO.from_record(record)


@router.delete(
    "/tokens/{access_token:path}",
    status_code=204,
    dependencies=[Depends(require_service_token)],
)
async def revoke_token(access_token: str) -> None:
    store = _require_store()
    await store.revoke(access_token)
