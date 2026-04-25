"""
FastAPI entry point — the ONLY place where identity resolution happens (ADR-010).

Endpoints:
  POST /chats                     — create a new chat session
  GET  /chats                     — list user's chat sessions
  GET  /chats/{id}/messages       — load chat history
  DELETE /chats/{id}              — delete a chat
  POST /chats/{id}/messages       — send a message (invokes agent graph)
  POST /chats/{id}/share          — promote chat RAG data to user-common
  POST /chats/{id}/upload         — upload documents for chat-scoped RAG
  POST /chat                      — legacy: single-shot chat (no persistence)
  POST /index                     — manually index test data (PoC)
  GET  /health                    — readiness check
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .lifecycle import setup_orchid, teardown_orchid
from .routers import (
    auth_exchange,
    auth_identity,
    auth_info,
    chats,
    legacy,
    mcp_auth,
    mcp_gateway,
    mcp_gateway_state,
    messages,
    resume,
    sharing,
    streaming,
)
from .settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


# ── Lifespan (delegates to lifecycle helpers) ──────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build graph and HTTP client once at startup, clean up on shutdown.

    The heavy lifting lives in ``lifecycle.setup_orchid`` / ``teardown_orchid``
    so integrators can reuse them in their own FastAPI apps (see README).
    """
    await setup_orchid()
    yield
    await teardown_orchid()


# ── App factory ─────────────────────────────────────────────

app = FastAPI(
    title="Orchid API",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS (origins configurable via Settings.cors_allowed_origins) ──
_cors_origins = [o.strip() for o in get_settings().cors_allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Built-in Routers ───────────────────────────────────────
app.include_router(chats.router)
app.include_router(messages.router)
app.include_router(resume.router)
app.include_router(sharing.router)
app.include_router(mcp_auth.router)
app.include_router(mcp_gateway.router)
app.include_router(mcp_gateway_state.router)
app.include_router(auth_info.router)
app.include_router(auth_exchange.router)
app.include_router(auth_identity.router)
app.include_router(streaming.router)
app.include_router(legacy.router)


# ── Plugin router discovery ────────────────────────────────


def _load_router_plugins() -> None:
    """Discover and register custom FastAPI routers.

    Consumer packages declare custom routers in their ``pyproject.toml``::

        [project.entry-points."orchid_api.routers"]
        my_admin = "my_package.api.admin:router"

    Each entry must resolve to a ``fastapi.APIRouter``.  Individual
    failures log a warning but never block startup — see
    :func:`orchid_ai.plugins.iter_entry_point_plugins`.
    """
    from fastapi import APIRouter

    from orchid_ai.plugins import iter_entry_point_plugins

    for name, router in iter_entry_point_plugins("orchid_api.routers", logger=logger):
        if isinstance(router, APIRouter):
            app.include_router(router)
            logger.info("[API] Loaded router plugin: %s", name)
        else:
            logger.warning("[API] Plugin '%s' is not an APIRouter — skipping", name)


_load_router_plugins()
