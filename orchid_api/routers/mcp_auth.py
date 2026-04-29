"""MCP per-server OAuth authorization router (MCP 2025-03-26 spec).

The 457-line monolith was decomposed into the :mod:`_mcp_auth` package:
discovery, authorize, callback, and revoke each own one file, with
PKCE / page rendering / token-exchange helpers in
:mod:`_mcp_auth._helpers`. This module now just composes those
sub-routers under the ``/mcp/auth`` prefix and re-exports the endpoint
functions for tests that import them directly.

Endpoints:
  - ``GET    /mcp/auth/servers``                  — list servers + status
  - ``POST   /mcp/auth/servers/{name}/discover``  — force discovery
  - ``GET    /mcp/auth/servers/{name}/authorize`` — generate authorize URL
  - ``GET    /mcp/auth/callback``                 — IdP redirect callback
  - ``DELETE /mcp/auth/servers/{name}/token``     — revoke a stored token
"""

from __future__ import annotations

from fastapi import APIRouter

from ._mcp_auth import authorize as _authorize
from ._mcp_auth import callback as _callback
from ._mcp_auth import discovery as _discovery
from ._mcp_auth import revoke as _revoke
from ._mcp_auth.authorize import get_authorize_url
from ._mcp_auth.callback import oauth_callback
from ._mcp_auth.discovery import DiscoverRequest, list_mcp_auth_servers, trigger_discovery
from ._mcp_auth.revoke import revoke_token

router = APIRouter(prefix="/mcp/auth", tags=["mcp-auth"])
router.include_router(_discovery.router)
router.include_router(_authorize.router)
router.include_router(_callback.router)
router.include_router(_revoke.router)

__all__ = [
    "DiscoverRequest",
    "get_authorize_url",
    "list_mcp_auth_servers",
    "oauth_callback",
    "revoke_token",
    "router",
    "trigger_discovery",
]
