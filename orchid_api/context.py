"""Application context — replaces module-level singletons (DIP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import HTTPException

from orchid_ai.bootstrap import BootstrapResult, teardown_runtime
from orchid_ai.config.schema import AgentsConfig
from orchid_ai.core.identity import IdentityResolver
from orchid_ai.core.mcp import MCPTokenStore
from orchid_ai.mcp.oauth_state import OAuthStateStore
from orchid_ai.persistence.base import ChatStorage
from orchid_ai.runtime import OrchidRuntime


@dataclass
class AppContext:
    """Holds runtime dependencies, created once at startup.

    The ``runtime`` field is the Orchid framework dependency bag — it owns the
    reader, LLM service, and MCP client factory.  API-layer concerns
    (http_client, identity_resolver, chat_repo, mcp_token_store, graph,
    oauth_state_store, agents_config) stay here.
    """

    runtime: OrchidRuntime = field(default_factory=OrchidRuntime)
    graph: Any = None
    http_client: httpx.AsyncClient | None = None
    identity_resolver: IdentityResolver | None = None
    chat_repo: ChatStorage | None = None
    mcp_token_store: MCPTokenStore | None = None
    oauth_state_store: OAuthStateStore | None = None
    agents_config: AgentsConfig | None = None
    # Private handle on the library-level ``BootstrapResult`` used by
    # :meth:`release_resources`.  Callers should not read this directly —
    # pair every ``setup_orchid`` with one ``release_resources`` invocation.
    _bootstrap: BootstrapResult | None = None

    async def release_resources(self) -> None:
        """Release every library-level resource this context holds.

        Tears down the ``BootstrapResult`` (checkpointer, MCP token
        store, chat storage), closes the OAuth state store, and clears
        all downstream field references.  Safe to call twice — every
        step short-circuits when its resource is already ``None``.

        Encapsulates the release behaviour so :func:`lifecycle.teardown_orchid`
        doesn't have to reach into the private ``_bootstrap`` field.
        """
        if self._bootstrap is not None:
            await teardown_runtime(self._bootstrap)
            self._bootstrap = None

        if self.oauth_state_store is not None:
            await self.oauth_state_store.close()
            self.oauth_state_store = None

        self.mcp_token_store = None
        self.chat_repo = None


# Singleton instance — populated by lifespan()
app_ctx = AppContext()


# ── FastAPI dependency helpers ─────────────────────────────────
#
# Routers depend on these instead of reaching into ``app_ctx`` and
# repeating the "is the service initialised?" null check.  Each helper
# raises a clear 503 when the corresponding resource hasn't been wired
# up yet (e.g. someone calls an endpoint before ``setup_orchid``).


def get_chat_repo() -> ChatStorage:
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
    """FastAPI dependency — returns the ``OrchidRuntime``.

    ``AppContext.runtime`` has a ``default_factory`` and is always
    populated at import time, so no null-check is necessary.
    """
    return app_ctx.runtime


def get_agents_config() -> AgentsConfig:
    """FastAPI dependency — returns the parsed agents config or raises 503."""
    if app_ctx.agents_config is None:
        raise HTTPException(status_code=503, detail="Agents config not loaded")
    return app_ctx.agents_config


def get_oauth_state_store() -> OAuthStateStore:
    """FastAPI dependency — returns the OAuth state store or raises 503."""
    if app_ctx.oauth_state_store is None:
        raise HTTPException(status_code=503, detail="OAuth state store not initialised")
    return app_ctx.oauth_state_store


def get_mcp_token_store() -> MCPTokenStore:
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


def get_mcp_token_store_optional() -> MCPTokenStore | None:
    """FastAPI dependency — return the MCP token store or ``None``."""
    return app_ctx.mcp_token_store


def get_agents_config_optional() -> AgentsConfig | None:
    """FastAPI dependency — return the parsed agents config or ``None``.

    Used by endpoints that advertise static capabilities before the
    runtime is fully wired.
    """
    return app_ctx.agents_config
