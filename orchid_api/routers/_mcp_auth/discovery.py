"""MCP-server discovery endpoints.

Two read/admin endpoints — list every OAuth-requiring server (with the
caller's authorization status) and force the RFC 9728/8414/7591 chain
to run for one server up-front.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchid_ai.core.mcp import (
    OrchidMCPClientRegistrationStore,
    OrchidMCPDiscoveryError,
    OrchidMCPTokenStore,
)
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.discovery import OrchidMCPAuthDiscovery
from orchid_ai.runtime import OrchidRuntime

from ...auth import get_auth_context
from ...context import (
    get_mcp_client_registration_store,
    get_mcp_client_registration_store_optional,
    get_mcp_token_store_optional,
    get_runtime,
)
from ...settings import Settings, get_settings
from ._helpers import callback_url

logger = logging.getLogger(__name__)
router = APIRouter()


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
        redirect_uri=callback_url(settings),
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
