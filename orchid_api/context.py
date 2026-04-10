"""Application context — replaces module-level singletons (DIP)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from orchid.core.identity import IdentityResolver
from orchid.persistence.base import ChatStorage


@dataclass
class AppContext:
    """Holds runtime dependencies, created once at startup."""

    graph: Any = None
    reader: Any = None  # VectorReader (may also be VectorStoreRepository)
    http_client: httpx.AsyncClient | None = None
    identity_resolver: IdentityResolver | None = None
    chat_repo: ChatStorage | None = None


# Singleton instance — populated by lifespan()
app_ctx = AppContext()
