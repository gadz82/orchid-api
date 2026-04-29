"""Authorization-URL endpoint.

Generates the URL the user is redirected to so they can grant the MCP
server access. Persists a PKCE-protected pending state keyed by an
opaque ``state`` that the OAuth callback later consumes.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.core.mcp import OrchidMCPClientRegistrationStore
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.discovery import (
    OrchidMCPAuthDiscovery,
    probe_mcp_server_for_resource_metadata,
)
from orchid_ai.mcp.oauth_state import OrchidOAuthPendingState, OrchidOAuthStateStore
from orchid_ai.runtime import OrchidRuntime

from ...auth import get_auth_context
from ...context import (
    get_mcp_client_registration_store,
    get_oauth_state_store,
    get_runtime,
)
from ...settings import Settings, get_settings
from ._helpers import callback_url, generate_code_challenge, generate_code_verifier
import secrets

logger = logging.getLogger(__name__)
router = APIRouter()


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

    Auto-runs the MCP 2025-03-26 discovery chain on first call; subsequent
    calls reuse the cached registration row.
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
                redirect_uri=callback_url(settings),
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

    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
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

    params = {
        "response_type": "code",
        "client_id": registration.client_id,
        "redirect_uri": callback_url(settings),
        "scope": registration.scopes_supported or "openid",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{registration.authorization_endpoint}?{urlencode(params)}"
    return {"authorize_url": authorize_url, "state": state}
