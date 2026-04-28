"""MCP per-server OAuth authorization endpoints (MCP 2025-03-26 spec).

Provides the API surface that drives the browser-visible half of the
RFC 9728 → RFC 8414 → RFC 7591 discovery chain implemented in
:mod:`orchid_ai.mcp.discovery`:

  - ``GET  /mcp/auth/servers``                  — list OAuth servers + user status
  - ``POST /mcp/auth/servers/{name}/discover``  — explicit trigger for the
                                                   discovery chain when a
                                                   server's 401 details
                                                   are known to the API
  - ``GET  /mcp/auth/servers/{name}/authorize`` — generate authorization URL
  - ``GET  /mcp/auth/callback``                 — IdP redirect callback
  - ``DELETE /mcp/auth/servers/{name}/token``   — revoke a stored token

All endpoint metadata (``authorization_endpoint``, ``token_endpoint``,
``client_id``, ``client_secret``) comes from
:class:`~orchid_ai.core.mcp.OrchidMCPClientRegistrationStore` which is
populated by the discovery service — there is nothing to configure in
YAML beyond ``auth.mode: oauth``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from orchid_ai.core.mcp import (
    OrchidMCPClientRegistrationStore,
    OrchidMCPDiscoveryError,
    OrchidMCPTokenRecord,
    OrchidMCPTokenStore,
)
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.discovery import OrchidMCPAuthDiscovery, probe_mcp_server_for_resource_metadata
from orchid_ai.mcp.oauth_state import OrchidOAuthPendingState, OrchidOAuthStateStore
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import (
    app_ctx,
    get_mcp_client_registration_store,
    get_mcp_client_registration_store_optional,
    get_mcp_token_store,
    get_mcp_token_store_optional,
    get_oauth_state_store,
    get_runtime,
)
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp/auth", tags=["mcp-auth"])


# ── PKCE helpers ──────────────────────────────────────────────


def _generate_code_verifier(length: int = 64) -> str:
    return secrets.token_urlsafe(length)[:128]


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _callback_url(settings: Settings) -> str:
    """Single source of truth for the registered redirect URI."""
    return f"{settings.api_base_url.rstrip('/')}/mcp/auth/callback"


# ── Request / response models ─────────────────────────────────


class DiscoverRequest(BaseModel):
    """Input to the explicit discovery endpoint.

    The framework ordinarily triggers discovery automatically when the
    MCP transport surfaces a 401 with a ``WWW-Authenticate`` header.
    This endpoint exists as an escape hatch for operators who want to
    prime the registration store before the first user connects — or
    for integrators whose MCP client doesn't capture the
    ``resource_metadata`` URL itself.
    """

    resource_metadata_url: str


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/servers")
async def list_mcp_auth_servers(
    auth: OrchidAuthContext = Depends(get_auth_context),
    runtime: OrchidRuntime = Depends(get_runtime),
    token_store: OrchidMCPTokenStore | None = Depends(get_mcp_token_store_optional),
    registration_store: OrchidMCPClientRegistrationStore | None = Depends(get_mcp_client_registration_store_optional),
):
    """List all MCP servers that require OAuth and the current user's status.

    ``discovered`` indicates whether the RFC 9728 → RFC 8414 → RFC 7591
    chain has already run for the server.  When ``False`` the
    ``authorize`` endpoint will trigger it on first call; when ``True``
    the cached registration is reused.
    """
    registry = runtime.mcp_auth_registry
    if not registry or registry.empty:
        return []

    results = []
    for name, info in registry.oauth_servers.items():
        authorized = False
        token_expired = False
        if token_store:
            token = await token_store.get_token(auth.tenant_key, auth.user_id, name)
            if token:
                authorized = True
                token_expired = token.is_expired

        registration = None
        if registration_store:
            registration = await registration_store.get(name)

        results.append(
            {
                "server_name": name,
                "agent_names": list(info.agent_names),
                "authorized": authorized and not token_expired,
                "token_expired": token_expired,
                "discovered": registration is not None,
                "scopes": registration.scopes_supported if registration else "",
            }
        )
    return results


@router.post("/servers/{server_name}/discover")
async def trigger_discovery(
    server_name: str,
    body: DiscoverRequest,
    _auth: OrchidAuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    runtime: OrchidRuntime = Depends(get_runtime),
    registration_store: OrchidMCPClientRegistrationStore = Depends(get_mcp_client_registration_store),
):
    """Run the MCP 2025-03-26 discovery chain for a single server.

    Idempotent — returns the cached registration on subsequent calls.
    Authentication is required so only operators / authorised users can
    force (re-)discovery against a chosen ``resource_metadata_url``.
    """
    registry = runtime.mcp_auth_registry
    if not registry or not registry.requires_oauth(server_name):
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' is not registered as OAuth-requiring",
        )

    discovery = OrchidMCPAuthDiscovery(
        store=registration_store,
        redirect_uri=_callback_url(settings),
    )

    try:
        record = await discovery.ensure_registration(
            server_name=server_name,
            resource_metadata_url=body.resource_metadata_url,
        )
    except OrchidMCPDiscoveryError as exc:
        logger.warning("[MCP OAuth] Discovery failed for '%s': %s", server_name, exc.reason)
        raise HTTPException(status_code=502, detail=exc.reason) from exc

    return {
        "server_name": record.server_name,
        "discovered": True,
        "authorization_endpoint": record.authorization_endpoint,
        "token_endpoint": record.token_endpoint,
        "issuer": record.issuer,
        "scopes_supported": record.scopes_supported,
    }


@router.get("/servers/{server_name}/authorize")
async def get_authorize_url(
    server_name: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    runtime: OrchidRuntime = Depends(get_runtime),
    state_store: OrchidOAuthStateStore = Depends(get_oauth_state_store),
    registration_store: OrchidMCPClientRegistrationStore = Depends(get_mcp_client_registration_store),
):
    """Generate an OAuth authorization URL for a specific MCP server.

    Requires the discovery chain to have been run at least once for the
    server — clients POST to ``/discover`` first (or rely on the
    transport-level auto-trigger once that's wired).
    """
    registry = runtime.mcp_auth_registry
    if not registry:
        raise HTTPException(status_code=404, detail="No MCP auth registry configured")
    server_info = registry.get_server(server_name)
    if server_info is None:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' not found or does not require OAuth",
        )

    # Auto-discover on first Connect: probe the MCP server for the
    # RFC 9728 metadata pointer, run the three-RFC chain, and persist.
    # Subsequent calls hit the cached row instead.
    registration = await registration_store.get(server_name)
    if registration is None:
        try:
            metadata_url = await probe_mcp_server_for_resource_metadata(
                mcp_url=server_info.url,
                server_name=server_name,
            )
            discovery = OrchidMCPAuthDiscovery(
                store=registration_store,
                redirect_uri=_callback_url(settings),
            )
            registration = await discovery.ensure_registration(
                server_name=server_name,
                resource_metadata_url=metadata_url,
            )
        except Exception as exc:
            reason = exc.reason if hasattr(exc, "reason") else str(exc)
            logger.warning(
                "[MCP OAuth] Auto-discovery failed for '%s': %s",
                server_name,
                reason,
            )
            raise HTTPException(status_code=502, detail=reason) from exc

    if not registration.authorization_endpoint:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Stored registration for '{server_name}' lacks an "
                f"authorization_endpoint — delete the row and re-run discovery."
            ),
        )

    code_verifier = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)
    await state_store.put(
        state,
        OrchidOAuthPendingState(
            server_name=server_name,
            tenant_id=auth.tenant_key,
            user_id=auth.user_id,
            code_verifier=code_verifier,
            token_endpoint=registration.token_endpoint,
            created_at=time.time(),
        ),
    )

    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": registration.client_id,
        "redirect_uri": _callback_url(settings),
        "scope": registration.scopes_supported or "openid",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{registration.authorization_endpoint}?{urlencode(params)}"
    return {"authorize_url": authorize_url, "state": state}


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    settings: Settings = Depends(get_settings),
    state_store: OrchidOAuthStateStore = Depends(get_oauth_state_store),
    token_store: OrchidMCPTokenStore | None = Depends(get_mcp_token_store_optional),
    registration_store: OrchidMCPClientRegistrationStore = Depends(get_mcp_client_registration_store),
):
    """OAuth callback — exchanges the code for tokens and persists them.

    This endpoint does NOT require Bearer auth — it is called by the
    authorization server's redirect.  Authentication is via PKCE + CSRF
    state validation.
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
    registration = await registration_store.get(server_name)
    if registration is None:
        return HTMLResponse(
            content=f"<html><body><h2>Unknown server: {server_name}</h2><script>window.close();</script></body></html>",
            status_code=500,
        )

    token_endpoint = pending.token_endpoint or registration.token_endpoint
    if not token_endpoint:
        return HTMLResponse(
            content="<html><body><h2>No token endpoint available</h2><script>window.close();</script></body></html>",
            status_code=500,
        )

    redirect_uri = _callback_url(settings)

    try:
        import httpx

        # Send client credentials per the authorization server's
        # advertised ``token_endpoint_auth_methods_supported``:
        #   - ``client_secret_basic`` → HTTP Basic auth header
        #   - ``client_secret_post`` (default) → form body field
        #   - ``none`` (public PKCE-only client) → no secret sent
        request_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": registration.client_id,
            "code_verifier": pending.code_verifier,
        }
        basic_auth = None
        if registration.client_secret:
            if registration.uses_basic_auth:
                basic_auth = (registration.client_id, registration.client_secret)
            else:
                request_data["client_secret"] = registration.client_secret

        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(token_endpoint, data=request_data, auth=basic_auth)
            if resp.status_code >= 400:
                logger.error(
                    "[MCP OAuth] Token exchange rejected by '%s' (%d): %s",
                    token_endpoint,
                    resp.status_code,
                    resp.text[:1000],
                )
                safe_body = resp.text[:1000].replace("<", "&lt;").replace(">", "&gt;")
                return HTMLResponse(
                    content=(
                        f"<html><body><h2>Token exchange failed ({resp.status_code})</h2>"
                        f"<pre>{safe_body}</pre>"
                        "<script>window.close();</script></body></html>"
                    ),
                    status_code=resp.status_code,
                )
            data = resp.json()
    except Exception as exc:
        logger.error("[MCP OAuth] Token exchange failed for '%s': %s", server_name, exc)
        return HTMLResponse(
            content=f"<html><body><h2>Token exchange failed</h2><p>{exc}</p>"
            "<script>window.close();</script></body></html>",
            status_code=500,
        )

    now = time.time()
    record = OrchidMCPTokenRecord(
        server_name=server_name,
        tenant_id=pending.tenant_id,
        user_id=pending.user_id,
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=now + data.get("expires_in", 3600),
        scopes=registration.scopes_supported,
        created_at=now,
        updated_at=now,
    )
    if token_store is not None:
        await token_store.save_token(record)
        logger.info(
            "[MCP OAuth] Token stored for server '%s', user '%s'",
            server_name,
            pending.user_id,
        )

    # Warm the freshly-authorized server's capabilities so the very
    # next chat sees them cached.  We synthesise an OrchidAuthContext
    # carrying the user's tenant/user id and the just-issued
    # access_token — the warmer dispatches via the per-server token
    # store, not via this context, but a populated access_token avoids
    # tripping any defensive checks downstream.  Failures here are
    # advisory and never break the OAuth completion page.
    if app_ctx.orchid is not None:
        try:
            resolved_auth = OrchidAuthContext(
                access_token=record.access_token,
                tenant_key=pending.tenant_id,
                user_id=pending.user_id,
            )
            await app_ctx.orchid.session_warmer.warm_one_for_user(resolved_auth, server_name)
        except Exception as exc:
            logger.warning(
                "[MCP OAuth] Post-callback warm for '%s' failed: %s",
                server_name,
                exc,
            )

    # ``targetOrigin = "*"`` because the popup lives on the API's
    # origin (e.g. ``http://localhost:8080``) while the opener is the
    # frontend (``http://localhost:3000`` — or anywhere the frontend is
    # hosted).  The browser refuses to deliver ``postMessage`` when
    # ``targetOrigin`` doesn't match the opener's origin, so scoping to
    # ``window.location.origin`` (the API) silently drops the message.
    # The payload carries no secrets — just a completion signal with
    # the server name — so the wildcard is safe.  Receivers can still
    # (and should) validate ``event.data.type`` on their end.
    return HTMLResponse(
        content=(
            "<html><body><h2>Authorization successful</h2>"
            "<p>You can close this window.</p>"
            "<script>"
            f'window.opener?.postMessage({{type:"mcp-auth-complete",server:"{server_name}"}}, "*");'
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
    """Delete the stored OAuth token for the authenticated user + server."""
    deleted = await store.delete_token(auth.tenant_key, auth.user_id, server_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="No token found for this server")
    logger.info("[MCP OAuth] Token revoked for server '%s', user '%s'", server_name, auth.user_id)
