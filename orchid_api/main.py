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

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orchid_ai.config.loader import load_config
from orchid_ai.core.repository import VectorStoreAdmin
from orchid_ai.graph.graph import build_graph
from orchid_ai.persistence.factory import build_chat_storage
from orchid_ai.rag.factory import build_reader
from orchid_ai.runtime import OrchidRuntime
from orchid_ai.utils import import_class

from .context import app_ctx
from .routers import chats, legacy, messages, sharing
from .settings import get_settings
from .tracing import configure_tracing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


# ── Lifespan ────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build graph and HTTP client once at startup, clean up on shutdown."""
    settings = get_settings()

    # ── LangSmith tracing (must be configured BEFORE graph build) ──
    configure_tracing(
        enabled=settings.langsmith_tracing,
        api_key=settings.langsmith_api_key,
        project=settings.langsmith_project,
    )

    app_ctx.http_client = httpx.AsyncClient(timeout=15)

    # ── Identity resolver (injectable — consumers provide their own) ──
    if settings.identity_resolver_class:
        resolver_cls = import_class(settings.identity_resolver_class)
        app_ctx.identity_resolver = resolver_cls(http_client=app_ctx.http_client)
        logger.info("[API] Identity resolver: %s", settings.identity_resolver_class)
    else:
        app_ctx.identity_resolver = None
        logger.info("[API] No identity resolver configured — only dev_auth_bypass works")

    # ── OrchidRuntime — single dependency bag for the framework ──
    reader = build_reader(
        vector_backend=settings.vector_backend,
        qdrant_url=settings.qdrant_url,
        embedding_model=settings.embedding_model,
    )
    app_ctx.runtime = OrchidRuntime(
        default_model=settings.litellm_model,
        reader=reader,
    )

    # ── Chat persistence ──
    app_ctx.chat_repo = build_chat_storage(
        class_path=settings.chat_storage_class,
        dsn=settings.chat_db_dsn,
    )
    await app_ctx.chat_repo.init_db()

    # ── Load YAML agent config (ADR-016) ──
    agents_config = load_config(settings.agents_config_path)

    # Pre-create vector store collections for all RAG namespaces in config
    namespaces = [a.rag.namespace for a in agents_config.agents.values() if a.rag.enabled and a.rag.namespace]
    if isinstance(reader, VectorStoreAdmin) and namespaces:
        await reader.ensure_collections([*namespaces, "uploads"])

    # ── Startup hook (consumer-provided) ──
    if settings.startup_hook:
        hook_fn = import_class(settings.startup_hook)
        await hook_fn(reader=reader, settings=settings)
        logger.info("[API] Startup hook executed: %s", settings.startup_hook)

    app_ctx.graph = build_graph(
        config=agents_config,
        runtime=app_ctx.runtime,
    )
    logger.info(
        "[API] Ready — model=%s, domain=%s, vector_backend=%s, agents=%s",
        settings.litellm_model,
        settings.auth_domain,
        settings.vector_backend,
        list(agents_config.agents.keys()),
    )
    yield

    if app_ctx.chat_repo:
        await app_ctx.chat_repo.close()
    if app_ctx.http_client:
        await app_ctx.http_client.aclose()


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

# ── Routers ────────────────────────────────────────────────
app.include_router(chats.router)
app.include_router(messages.router)
app.include_router(sharing.router)
app.include_router(legacy.router)
