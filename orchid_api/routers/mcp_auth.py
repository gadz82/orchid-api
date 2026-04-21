"""MCP per-server OAuth authorization endpoints.

Provides the API surface for third-party OAuth flows:
  - ``GET  /mcp/auth/servers``                  — list OAuth servers + user status
  - ``GET  /mcp/auth/servers/{name}/authorize`` — generate authorization URL
  - ``GET  /mcp/auth/callback``                 — IdP redirect callback
  - ``DELETE /mcp/auth/servers/{name}/token``   — revoke a stored token
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from orchid_ai.core.mcp import OrchidMCPTokenRecord, OrchidMCPTokenStore
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.oauth_state import OrchidOAuthPendingState, OrchidOAuthStateStore
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import (
    get_mcp_token_store,
    get_mcp_token_store_optional,
    get_oauth_state_store,
    get_runtime,
)
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp/auth", tags=["mcp-auth"])

# Optional-vs-strict asymmetry: ``list_mcp_auth_servers`` / ``oauth_callback``
# tolerate a missing token store (read-only / best-effort paths), while
# ``revoke_token`` uses the strict :func:`get_mcp_token_store` dep — you
# cannot revoke a token that was never saved, so 503 is the right signal.


# ── PKCE helpers ──────────────────────────────────────────────


def _generate_code_verifier(length: int = 64) -> str:
    return secrets.token_urlsafe(length)[:128]


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/servers")
async def list_mcp_auth_servers(
    auth: OrchidAuthContext = Depends(get_auth_context),
    runtime: OrchidRuntime = Depends(get_runtime),
    store: OrchidMCPTokenStore | None = Depends(get_mcp_token_store_optional),
):
    """List all MCP servers that require OAuth and the current user's authorization status."""
    registry = runtime.mcp_auth_registry
    if not registry or registry.empty:
        return []

    results = []
    for name, info in registry.oauth_servers.items():
        authorized = False
        token_expired = False
        if store:
            token = await store.get_token(auth.tenant_key, auth.user_id, name)
            if token:
                authorized = True
                token_expired = token.is_expired
        results.append(
            {
                "server_name": name,
                "client_id": info.client_id,
                "scopes": info.scopes,
                "authorized": authorized and not token_expired,
                "token_expired": token_expired,
                "agent_names": list(info.agent_names),
            }
        )
    return results


@router.get("/servers/{server_name}/authorize")
async def get_authorize_url(
    server_name: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    runtime: OrchidRuntime = Depends(get_runtime),
    state_store: OrchidOAuthStateStore = Depends(get_oauth_state_store),
):
    """Generate an OAuth authorization URL for a specific MCP server."""
    registry = runtime.mcp_auth_registry
    if not registry:
        raise HTTPException(status_code=404, detail="No MCP auth registry configured")

    server_info = registry.get_server(server_name)
    if not server_info:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_name}' not found or does not require OAuth")

    # Resolve authorization endpoint (OIDC discovery if needed)
    auth_endpoint = server_info.authorization_endpoint
    token_endpoint = server_info.token_endpoint
    if not auth_endpoint and server_info.issuer:
        endpoints = await _discover_oidc_endpoints(server_info.issuer)
        auth_endpoint = endpoints.get("authorization_endpoint", "")
        token_endpoint = endpoints.get("token_endpoint", token_endpoint)

    if not auth_endpoint:
        raise HTTPException(status_code=500, detail=f"Cannot resolve authorization endpoint for '{server_name}'")

    # Generate PKCE pair + state
    code_verifier = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)

    # Store pending state
    await state_store.put(
        state,
        OrchidOAuthPendingState(
            server_name=server_name,
            tenant_id=auth.tenant_key,
            user_id=auth.user_id,
            code_verifier=code_verifier,
            token_endpoint=token_endpoint or "",
            created_at=time.time(),
        ),
    )

    redirect_uri = f"{settings.api_base_url}/mcp/auth/callback"

    # Build authorization URL
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": server_info.client_id,
        "redirect_uri": redirect_uri,
        "scope": server_info.scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{auth_endpoint}?{urlencode(params)}"

    return {"authorize_url": authorize_url, "state": state}


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    settings: Settings = Depends(get_settings),
    runtime: OrchidRuntime = Depends(get_runtime),
    state_store: OrchidOAuthStateStore = Depends(get_oauth_state_store),
    token_store: OrchidMCPTokenStore | None = Depends(get_mcp_token_store_optional),
):
    """OAuth callback — exchanges code for tokens and stores them.

    This endpoint does NOT require Bearer auth — it is called by the
    IdP redirect.  Authentication is via PKCE + CSRF state validation.
    """
    if error:
        return HTMLResponse(
            content=f"<html><body><h2>Authorization failed</h2><p>{error}</p>"
            "<script>window.close();</script></body></html>",
            status_code=400,
        )

    if not code or not state:
        return HTMLResponse(
            content="<html><body><h2>Missing code or state</h2><script>window.close();</script></body></html>",
            status_code=400,
        )

    pending = await state_store.pop(state)
    if not pending:
        return HTMLResponse(
            content="<html><body><h2>Invalid or expired state</h2><script>window.close();</script></body></html>",
            status_code=400,
        )

    server_name = pending.server_name
    registry = runtime.mcp_auth_registry
    if not registry:
        return HTMLResponse(
            content="<html><body><h2>Server configuration error</h2><script>window.close();</script></body></html>",
            status_code=500,
        )

    server_info = registry.get_server(server_name)
    if not server_info:
        return HTMLResponse(
            content=f"<html><body><h2>Unknown server: {server_name}</h2><script>window.close();</script></body></html>",
            status_code=500,
        )

    # Exchange code for tokens
    token_endpoint = pending.token_endpoint or server_info.token_endpoint
    if not token_endpoint and server_info.issuer:
        endpoints = await _discover_oidc_endpoints(server_info.issuer)
        token_endpoint = endpoints.get("token_endpoint", "")

    if not token_endpoint:
        return HTMLResponse(
            content="<html><body><h2>No token endpoint configured</h2><script>window.close();</script></body></html>",
            status_code=500,
        )

    redirect_uri = f"{settings.api_base_url}/mcp/auth/callback"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": server_info.client_id,
                    "code_verifier": pending.code_verifier,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("[MCP OAuth] Token exchange failed for '%s': %s", server_name, exc)
        return HTMLResponse(
            content=f"<html><body><h2>Token exchange failed</h2><p>{exc}</p>"
            "<script>window.close();</script></body></html>",
            status_code=500,
        )

    # Store the token
    now = time.time()
    record = OrchidMCPTokenRecord(
        server_name=server_name,
        tenant_id=pending.tenant_id,
        user_id=pending.user_id,
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=now + data.get("expires_in", 3600),
        scopes=server_info.scopes,
        created_at=now,
        updated_at=now,
    )

    if token_store is not None:
        await token_store.save_token(record)
        logger.info("[MCP OAuth] Token stored for server '%s', user '%s'", server_name, pending.user_id)

    # Return HTML that notifies the opener window and closes the popup
    return HTMLResponse(
        content=(
            "<html><body><h2>Authorization successful</h2>"
            "<p>You can close this window.</p>"
            "<script>"
            f'window.opener?.postMessage({{type:"mcp-auth-complete",server:"{server_name}"}}, window.location.origin);'
            "setTimeout(function() { window.close(); }, 1000);"
            "</script></body></html>"
        )
    )


@router.delete("/servers/{server_name}/token", status_code=204)
async def revoke_token(
    server_name: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    store: OrchidMCPTokenStore = Depends(get_mcp_token_store),
):
    """Delete the stored OAuth token for the authenticated user and specified server."""
    deleted = await store.delete_token(auth.tenant_key, auth.user_id, server_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="No token found for this server")
    logger.info("[MCP OAuth] Token revoked for server '%s', user '%s'", server_name, auth.user_id)


# ── OIDC discovery (cached) ──────────────────────────────────

_oidc_cache: dict[str, dict[str, str]] = {}


async def _discover_oidc_endpoints(issuer: str) -> dict[str, str]:
    """Fetch OIDC discovery document and return key endpoints."""
    if issuer in _oidc_cache:
        return _oidc_cache[issuer]

    well_known = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(well_known)
            resp.raise_for_status()
            data = resp.json()

        result = {
            "authorization_endpoint": data.get("authorization_endpoint", ""),
            "token_endpoint": data.get("token_endpoint", ""),
            "userinfo_endpoint": data.get("userinfo_endpoint", ""),
        }
        _oidc_cache[issuer] = result
        logger.info("[MCP OAuth] OIDC discovery for '%s': %s", issuer, list(result.keys()))
        return result
    except Exception as exc:
        logger.warning("[MCP OAuth] OIDC discovery failed for '%s': %s", issuer, exc)
        return {}
