"""``/mcp-gateway/config`` endpoint.

Exposes the resolved :class:`OrchidMCPGatewayConfig` — base config from
``agents.yaml`` / programmatic, plus env-var + external-file overrides
applied by :mod:`orchid_api.mcp_gateway`.

Auth: standard ``get_auth_context`` dependency.  When
``DEV_AUTH_BYPASS=true`` the endpoint is open (matching the rest of
orchid-api); otherwise it requires a valid Bearer token resolved by
the configured :class:`OrchidIdentityResolver`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.config import OrchidAgentsConfig, OrchidMCPGatewayConfig
from orchid_ai.core.state import OrchidAuthContext

from ..auth import get_auth_context
from ..context import get_agents_config
from ..mcp_gateway import OrchidMCPGatewayConfigError, resolve_mcp_gateway_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-gateway", tags=["mcp-gateway"])


@router.get("/config", response_model=OrchidMCPGatewayConfig)
async def get_mcp_gateway_config(
    _auth: OrchidAuthContext = Depends(get_auth_context),
    agents_config: OrchidAgentsConfig = Depends(get_agents_config),
) -> OrchidMCPGatewayConfig:
    """Return the effective MCP-gateway exposure config."""
    try:
        return resolve_mcp_gateway_config(agents_config.mcp_gateway)
    except OrchidMCPGatewayConfigError as exc:
        logger.error("[mcp-gateway] config resolution failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
