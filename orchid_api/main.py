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
from .routers import chats, legacy, messages, mcp_auth, resume, sharing, streaming

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

# ── CORS (allow the Next.js frontend) ─────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # local dev
        "http://frontend:3000",  # Docker network
    ],
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
app.include_router(streaming.router)
app.include_router(legacy.router)


# ── Plugin router discovery ────────────────────────────────


def _load_router_plugins() -> None:
    """Discover and register custom FastAPI routers from entry-point group.

    Consumer packages can declare custom routers in their ``pyproject.toml``::

        [project.entry-points."orchid_api.routers"]
        my_admin = "my_package.api.admin:router"

    Each entry must resolve to a ``fastapi.APIRouter`` instance.
    Failed plugins log a warning but do not block startup.
    """
    from fastapi import APIRouter

    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        plugins = (
            eps.select(group="orchid_api.routers") if hasattr(eps, "select") else eps.get("orchid_api.routers", [])
        )

        for ep in plugins:
            try:
                router = ep.load()
                if isinstance(router, APIRouter):
                    app.include_router(router)
                    logger.info("[API] Loaded router plugin: %s", ep.name)
                else:
                    logger.warning("[API] Plugin '%s' is not an APIRouter — skipping", ep.name)
            except Exception as exc:
                logger.warning("[API] Failed to load router plugin '%s': %s", ep.name, exc)
    except Exception:
        pass  # importlib.metadata unavailable or no plugins — that's fine


_load_router_plugins()
