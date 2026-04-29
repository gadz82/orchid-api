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
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orchid_ai.observability import configure_perf_logger

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
    session,
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

# ── Performance logger — opt-in via ORCHID_ENABLE_PERF_LOGS env var ──
# When unset (default), the ``orchid.perf`` logger is at WARNING so the
# scattered ``[PERF] …`` info() calls stay silent.  Set
# ``ORCHID_ENABLE_PERF_LOGS=true`` in the container env to flip it on
# for profiling sessions.
_perf_enabled = configure_perf_logger()
if _perf_enabled:
    logger.warning("[API] Perf logs ENABLED via %s — expect verbose [PERF] lines", "ORCHID_ENABLE_PERF_LOGS")

# ── Optional LangChain debug — toggle with LANGCHAIN_DEBUG=true ─────
# Captures every LLM/tool/chain call shape (verbose). Useful only for
# diagnosing where time is spent inside LangChain itself; leave OFF in
# normal runs because it floods the container output.
if os.getenv("LANGCHAIN_DEBUG", "").lower() in ("1", "true", "yes"):
    try:
        from langchain.globals import set_debug, set_verbose

        set_debug(True)
        set_verbose(True)
        logger.warning("[API] LangChain debug + verbose mode ENABLED via LANGCHAIN_DEBUG env var")
    except Exception as exc:  # pragma: no cover — best-effort toggle
        logger.warning("[API] Could not enable LangChain debug: %s", exc)


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
app.include_router(session.router)
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
