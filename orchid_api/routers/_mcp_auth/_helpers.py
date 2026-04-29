"""Shared helpers for the MCP-auth router family.

Concentrates four cross-cutting concerns the per-endpoint modules each
need: PKCE generation, the registered redirect URI, XSS-safe HTML
page rendering, and the upstream token exchange. Splitting them out
keeps the route files focused on request shape and orchestration.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets
from dataclasses import dataclass

import httpx

from orchid_ai.core.mcp import OrchidMCPClientRegistration

from ...settings import Settings

logger = logging.getLogger(__name__)


# ── PKCE + redirect URI ─────────────────────────────────────


def generate_code_verifier(length: int = 64) -> str:
    return secrets.token_urlsafe(length)[:128]


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def callback_url(settings: Settings) -> str:
    """Single source of truth for the registered redirect URI."""
    return f"{settings.api_base_url.rstrip('/')}/mcp/auth/callback"


# ── HTML page rendering (XSS-safe) ──────────────────────────


def render_error_page(message: str, *, status: int) -> tuple[str, int]:
    """Build a self-closing error page with the message HTML-escaped."""
    body = f"<html><body><h2>{html.escape(message)}</h2><script>window.close();</script></body></html>"
    return body, status


def render_simple_message_page(heading: str, *, status: int, detail: str = "") -> tuple[str, int]:
    """Build a static page (heading + optional detail) — both escaped."""
    detail_html = f"<p>{html.escape(detail)}</p>" if detail else ""
    body = f"<html><body><h2>{html.escape(heading)}</h2>{detail_html}<script>window.close();</script></body></html>"
    return body, status


def render_token_exchange_failure_page(
    *,
    body_text: str,
    status: int,
) -> tuple[str, int]:
    """Build the page rendered when the upstream token endpoint returned a 4xx."""
    body = (
        f"<html><body><h2>Token exchange failed ({status})</h2>"
        f"<pre>{html.escape(body_text[:1000])}</pre>"
        "<script>window.close();</script></body></html>"
    )
    return body, status


def render_callback_success_page(server_name: str) -> str:
    """Build the success page that posts a completion message back to the opener.

    The server name is JSON-encoded with ``</`` neutralised so a hostile
    name cannot break out of the surrounding <script> block.
    """
    payload = json.dumps({"type": "mcp-auth-complete", "server": server_name}).replace("</", "<\\/")
    return (
        "<html><body><h2>Authorization successful</h2>"
        "<p>You can close this window.</p>"
        "<script>"
        f'window.opener?.postMessage({payload}, "*");'
        "setTimeout(function() { window.close(); }, 1000);"
        "</script></body></html>"
    )


# ── Token exchange ─────────────────────────────────────────


@dataclass
class TokenExchangeOutcome:
    """Result of an upstream token exchange.

    Exactly one of ``data`` (success) or ``html_body`` (rendered failure
    page) is populated. The status code goes with the failure page so
    the caller can return it as the response status.
    """

    data: dict | None = None
    html_body: str | None = None
    status: int = 200


async def exchange_authorization_code(
    *,
    token_endpoint: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    registration: OrchidMCPClientRegistration,
    server_name: str,
) -> TokenExchangeOutcome:
    """POST the authorization code to the upstream token endpoint.

    Honours the registration's advertised authentication method:
      - ``client_secret_basic`` — HTTP Basic header
      - ``client_secret_post`` (default) — secret in the body
      - public client — no secret sent (PKCE only)
    """
    request_data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": registration.client_id,
        "code_verifier": code_verifier,
    }
    basic_auth: tuple[str, str] | None = None
    if registration.client_secret:
        if registration.uses_basic_auth:
            basic_auth = (registration.client_id, registration.client_secret)
        else:
            request_data["client_secret"] = registration.client_secret

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(token_endpoint, data=request_data, auth=basic_auth)
    except Exception as exc:  # transport / DNS / SSL failure
        logger.error("[MCP OAuth] Token exchange failed for '%s': %s", server_name, exc)
        body, status = render_simple_message_page(
            "Token exchange failed",
            detail=str(exc),
            status=500,
        )
        return TokenExchangeOutcome(html_body=body, status=status)

    if resp.status_code >= 400:
        logger.error(
            "[MCP OAuth] Token exchange rejected by '%s' (%d): %s",
            token_endpoint,
            resp.status_code,
            resp.text[:1000],
        )
        body, status = render_token_exchange_failure_page(
            body_text=resp.text,
            status=resp.status_code,
        )
        return TokenExchangeOutcome(html_body=body, status=status)

    return TokenExchangeOutcome(data=resp.json())
