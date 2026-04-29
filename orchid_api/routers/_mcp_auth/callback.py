"""OAuth callback handler.

This endpoint does NOT require Bearer auth — it is hit by the IdP's
redirect, with PKCE + state validation as the authentication mechanism.
On success it stores the access token and warms the freshly authorized
server's capability cache.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from orchid_ai.core.mcp import (
    OrchidMCPClientRegistrationStore,
    OrchidMCPTokenRecord,
    OrchidMCPTokenStore,
)
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.oauth_state import OrchidOAuthStateStore

from ...context import (
    app_ctx,
    get_mcp_client_registration_store,
    get_mcp_token_store_optional,
    get_oauth_state_store,
)
from ...settings import Settings, get_settings
from ._helpers import (
    callback_url,
    exchange_authorization_code,
    render_callback_success_page,
    render_simple_message_page,
)

logger = logging.getLogger(__name__)
router = APIRouter()


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
    """Exchange the authorization code for tokens and persist them."""
    if error:
        body, status = render_simple_message_page("Authorization failed", detail=error, status=400)
        return HTMLResponse(content=body, status_code=status)

    if not code or not state:
        body, status = render_simple_message_page("Missing code or state", status=400)
        return HTMLResponse(content=body, status_code=status)

    pending = await state_store.pop(state)
    if not pending:
        body, status = render_simple_message_page("Invalid or expired state", status=400)
        return HTMLResponse(content=body, status_code=status)

    server_name = pending.server_name
    registration = await registration_store.get(server_name)
    if registration is None:
        body, status = render_simple_message_page(
            f"Unknown server: {server_name}",
            status=500,
        )
        return HTMLResponse(content=body, status_code=status)

    token_endpoint = pending.token_endpoint or registration.token_endpoint
    if not token_endpoint:
        body, status = render_simple_message_page("No token endpoint available", status=500)
        return HTMLResponse(content=body, status_code=status)

    outcome = await exchange_authorization_code(
        token_endpoint=token_endpoint,
        redirect_uri=callback_url(settings),
        code=code,
        code_verifier=pending.code_verifier,
        registration=registration,
        server_name=server_name,
    )
    if outcome.html_body is not None:
        return HTMLResponse(content=outcome.html_body, status_code=outcome.status)

    assert outcome.data is not None  # narrowed by the branch above
    data = outcome.data

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

    return HTMLResponse(content=render_callback_success_page(server_name))
