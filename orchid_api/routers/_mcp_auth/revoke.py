"""Token revocation endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.core.mcp import OrchidMCPTokenStore
from orchid_ai.core.state import OrchidAuthContext

from ...auth import get_auth_context
from ...context import get_mcp_token_store

logger = logging.getLogger(__name__)
router = APIRouter()


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
