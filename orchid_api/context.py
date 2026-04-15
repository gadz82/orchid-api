"""Application context — replaces module-level singletons (DIP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from orchid_ai.core.identity import IdentityResolver
from orchid_ai.core.mcp import MCPTokenStore
from orchid_ai.persistence.base import ChatStorage
from orchid_ai.runtime import OrchidRuntime


@dataclass
class AppContext:
    """Holds runtime dependencies, created once at startup.

    The ``runtime`` field is the Orchid framework dependency bag — it owns the
    reader, LLM service, and MCP client factory.  API-layer concerns
    (http_client, identity_resolver, chat_repo, mcp_token_store, graph) stay here.
    """

    runtime: OrchidRuntime = field(default_factory=OrchidRuntime)
    graph: Any = None
    http_client: httpx.AsyncClient | None = None
    identity_resolver: IdentityResolver | None = None
    chat_repo: ChatStorage | None = None
    mcp_token_store: MCPTokenStore | None = None


# Singleton instance — populated by lifespan()
app_ctx = AppContext()
