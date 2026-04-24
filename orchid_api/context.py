"""Application context — single owned :class:`Orchid` + adapter-level concerns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException

from orchid_ai import Orchid, OrchidRuntime
from orchid_ai.config.schema import OrchidAgentsConfig
from orchid_ai.core.auth_config import OrchidAuthConfigProvider
from orchid_ai.core.identity import OrchidIdentityResolver
from orchid_ai.core.mcp import OrchidMCPClientRegistrationStore, OrchidMCPTokenStore
from orchid_ai.mcp.oauth_state import OrchidOAuthStateStore
from orchid_ai.persistence.base import OrchidChatStorage


@dataclass
class AppContext:
    """Adapter-level runtime state for ``orchid-api``.

    Holds a **single** :class:`Orchid` instance that owns the framework
    runtime + graph + persistence + checkpointer, plus orchid-api's own
    HTTP-layer concerns (identity resolver, shared HTTP client, OAuth
    state store).  Legacy fields (``runtime``, ``chat_repo``, ``graph``,
    ``mcp_token_store``, ``agents_config``) are exposed as read-through
    properties so routers that depend on them keep working unchanged.
    """

    orchid: Orchid | None = None
    http_client: httpx.AsyncClient | None = None
    identity_resolver: OrchidIdentityResolver | None = None
    # Resolves non-secret upstream-OAuth discovery info (endpoints +
    # public client_id) for downstream consumers fetching
    # ``GET /auth-info``.  ``None`` means discovery is not configured —
    # consumers must fall back to their own env-var overrides.
    auth_config_provider: OrchidAuthConfigProvider | None = None
    oauth_state_store: OrchidOAuthStateStore | None = None

    # ── Read-through properties (convenience for existing routers) ──

    @property
    def runtime(self) -> OrchidRuntime:
        """Underlying :class:`OrchidRuntime`, or an empty default when not started."""
        return self.orchid.runtime if self.orchid is not None else OrchidRuntime()

    @property
    def graph(self) -> Any:
        return self.orchid.graph if self.orchid is not None else None

    @property
    def chat_repo(self) -> OrchidChatStorage | None:
        return self.orchid.chat_repo if self.orchid is not None else None

    @property
    def mcp_token_store(self) -> OrchidMCPTokenStore | None:
        return self.orchid.mcp_token_store if self.orchid is not None else None

    @property
    def mcp_client_registration_store(self) -> OrchidMCPClientRegistrationStore | None:
        """Discovered per-server OAuth metadata + DCR credentials store."""
        return self.orchid.runtime.mcp_client_registration_store if self.orchid is not None else None

    @property
    def agents_config(self) -> OrchidAgentsConfig | None:
        return self.orchid.config if self.orchid is not None else None

    async def release_resources(self) -> None:
        """Release every library-level resource this context holds.

        Safe to call twice — each step short-circuits when its resource
        is already ``None``.
        """
        if self.orchid is not None:
            await self.orchid.close()
            self.orchid = None

        if self.oauth_state_store is not None:
            await self.oauth_state_store.close()
            self.oauth_state_store = None


# Singleton instance — populated by lifespan()
app_ctx = AppContext()


# ── FastAPI dependency helpers ─────────────────────────────────
#
# Routers depend on these instead of reaching into ``app_ctx`` and
# repeating the "is the service initialised?" null check.  Each helper
# raises a clear 503 when the corresponding resource hasn't been wired
# up yet (e.g. someone calls an endpoint before ``setup_orchid``).


def get_chat_repo() -> OrchidChatStorage:
    """FastAPI dependency — returns the chat storage or raises 503."""
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")
    return app_ctx.chat_repo


def get_graph() -> Any:
    """FastAPI dependency — returns the compiled graph or raises 503."""
    if app_ctx.graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialised")
    return app_ctx.graph


def get_runtime() -> OrchidRuntime:
    """FastAPI dependency — returns the :class:`OrchidRuntime`.

    Always returns a non-null value: when :class:`Orchid` hasn't been
    started yet, ``AppContext.runtime`` falls back to a default
    :class:`OrchidRuntime()` via the property.
    """
    return app_ctx.runtime


def get_agents_config() -> OrchidAgentsConfig:
    """FastAPI dependency — returns the parsed agents config or raises 503."""
    if app_ctx.agents_config is None:
        raise HTTPException(status_code=503, detail="Agents config not loaded")
    return app_ctx.agents_config


def get_oauth_state_store() -> OrchidOAuthStateStore:
    """FastAPI dependency — returns the OAuth state store or raises 503."""
    if app_ctx.oauth_state_store is None:
        raise HTTPException(status_code=503, detail="OAuth state store not initialised")
    return app_ctx.oauth_state_store


def get_mcp_token_store() -> OrchidMCPTokenStore:
    """FastAPI dependency — returns the MCP OAuth token store or raises 503."""
    if app_ctx.mcp_token_store is None:
        raise HTTPException(status_code=503, detail="MCP token store not initialised")
    return app_ctx.mcp_token_store


# ── Optional variants ─────────────────────────────────────────
#
# Some endpoints degrade gracefully when their store / config isn't
# wired (e.g. ``/capabilities`` advertises safe defaults before startup;
# ``mcp_auth.list_servers`` reports every server as unauthorized).
# These helpers return ``None`` instead of raising, keeping the
# degradation explicit at the call site.


def get_mcp_token_store_optional() -> OrchidMCPTokenStore | None:
    """FastAPI dependency — return the MCP token store or ``None``."""
    return app_ctx.mcp_token_store


def get_mcp_client_registration_store() -> OrchidMCPClientRegistrationStore:
    """FastAPI dependency — discovered OAuth metadata store or raises 503."""
    store = app_ctx.mcp_client_registration_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="MCP client-registration store not initialised",
        )
    return store


def get_mcp_client_registration_store_optional() -> OrchidMCPClientRegistrationStore | None:
    """FastAPI dependency — return the registration store or ``None``."""
    return app_ctx.mcp_client_registration_store


def get_agents_config_optional() -> OrchidAgentsConfig | None:
    """FastAPI dependency — return the parsed agents config or ``None``.

    Used by endpoints that advertise static capabilities before the
    runtime is fully wired.
    """
    return app_ctx.agents_config
