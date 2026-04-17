"""
Orchid-API lifecycle helpers — ``setup_orchid`` / ``teardown_orchid``.

These functions let integrators embed orchid-api into their OWN FastAPI
application.  Instead of running the standalone ``orchid_api.main:app``,
import these functions from your own ``lifespan`` and include whichever
orchid routers you need.

Example — mount orchid into an existing app::

    from contextlib import asynccontextmanager
    from fastapi import FastAPI

    from orchid_api.lifecycle import setup_orchid, teardown_orchid
    from orchid_api.routers import chats, messages, streaming, resume

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await my_own_db.connect()             # your setup
        await setup_orchid()                  # orchid setup
        yield
        await teardown_orchid()               # orchid teardown
        await my_own_db.disconnect()          # your teardown

    app = FastAPI(title="My App", lifespan=lifespan)
    app.include_router(my_own_router)

    # Mount orchid under /ai (or at root — up to you)
    app.include_router(chats.router,     prefix="/ai")
    app.include_router(messages.router,  prefix="/ai")
    app.include_router(streaming.router, prefix="/ai")
    app.include_router(resume.router,    prefix="/ai")

After ``setup_orchid()`` returns, ``app_ctx`` is fully populated with:
  - ``graph`` (compiled LangGraph)
  - ``runtime`` (OrchidRuntime + checkpointer + MCP auth registry)
  - ``chat_repo``, ``mcp_token_store``, ``identity_resolver``
"""

from __future__ import annotations

import logging

import httpx

from orchid_ai.config.loader import load_config
from orchid_ai.core.repository import VectorStoreAdmin
from orchid_ai.graph.graph import build_graph
from orchid_ai.persistence.factory import build_chat_storage
from orchid_ai.persistence.mcp_token_factory import build_mcp_token_store
from orchid_ai.rag.factory import build_reader
from orchid_ai.runtime import OrchidRuntime
from orchid_ai.utils import import_class

from .context import app_ctx
from .settings import Settings, get_settings
from .tracing import configure_tracing

logger = logging.getLogger(__name__)


async def setup_orchid(settings: Settings | None = None) -> None:
    """Initialize the orchid-api runtime (graph, storage, auth, checkpointer).

    Populates the global ``app_ctx`` singleton with everything the built-in
    routers need.  Safe to call from any FastAPI lifespan.

    Parameters
    ----------
    settings : Settings | None
        Optional pre-built Settings object.  When ``None``, reads from env
        vars / ``ORCHID_CONFIG`` via ``get_settings()``.

    Raises
    ------
    Any exception from ``build_graph``, ``build_reader``, ``build_chat_storage``,
    or consumer-provided startup hooks.
    """
    s = settings or get_settings()

    # ── LangSmith tracing (must be configured BEFORE graph build) ──
    configure_tracing(
        enabled=s.langsmith_tracing,
        api_key=s.langsmith_api_key,
        project=s.langsmith_project,
    )

    # ── Shared HTTP client ──
    if app_ctx.http_client is None:
        app_ctx.http_client = httpx.AsyncClient(timeout=15)

    # ── Identity resolver (optional) ──
    if s.identity_resolver_class:
        resolver_cls = import_class(s.identity_resolver_class)
        app_ctx.identity_resolver = resolver_cls(http_client=app_ctx.http_client)
        logger.info("[API] Identity resolver: %s", s.identity_resolver_class)
    else:
        app_ctx.identity_resolver = None
        logger.info("[API] No identity resolver configured — only dev_auth_bypass works")

    # ── OrchidRuntime — dependency bag for the framework ──
    reader = build_reader(
        vector_backend=s.vector_backend,
        qdrant_url=s.qdrant_url,
        embedding_model=s.embedding_model,
    )
    app_ctx.runtime = OrchidRuntime(
        default_model=s.litellm_model,
        reader=reader,
    )

    # ── Chat persistence ──
    app_ctx.chat_repo = build_chat_storage(
        class_path=s.chat_storage_class,
        dsn=s.chat_db_dsn,
    )
    await app_ctx.chat_repo.init_db()

    # ── MCP OAuth token storage ──
    mcp_token_store = build_mcp_token_store(
        class_path=s.mcp_token_store_class,
        dsn=s.mcp_token_store_dsn,
    )
    await mcp_token_store.init_db()
    app_ctx.mcp_token_store = mcp_token_store
    app_ctx.runtime.mcp_token_store = mcp_token_store

    # ── Load agent config ──
    agents_config = load_config(s.agents_config_path)

    # ── Pre-create vector store collections ──
    namespaces = [a.rag.namespace for a in agents_config.agents.values() if a.rag.enabled and a.rag.namespace]
    if isinstance(reader, VectorStoreAdmin) and namespaces:
        await reader.ensure_collections([*namespaces, "uploads"])

    # ── Startup hook (consumer-provided) ──
    if s.startup_hook:
        hook_fn = import_class(s.startup_hook)
        await hook_fn(reader=reader, settings=s)
        logger.info("[API] Startup hook executed: %s", s.startup_hook)

    # ── Checkpointer (optional — required for HITL) ──
    if s.checkpointer_type:
        from orchid_ai.checkpointing import build_checkpointer

        checkpointer = await build_checkpointer(
            checkpointer_type=s.checkpointer_type,
            dsn=s.checkpointer_dsn,
        )
        app_ctx.runtime.checkpointer = checkpointer
        logger.info("[API] Checkpointer: %s", type(checkpointer).__name__)

    # ── Build the compiled LangGraph ──
    app_ctx.graph = build_graph(config=agents_config, runtime=app_ctx.runtime)
    logger.info(
        "[API] Ready — model=%s, vector_backend=%s, agents=%s",
        s.litellm_model,
        s.vector_backend,
        list(agents_config.agents.keys()),
    )


async def teardown_orchid() -> None:
    """Release all orchid-api resources.

    Idempotent: safe to call even when ``setup_orchid`` was not called or
    already torn down.  Call this from your FastAPI lifespan after ``yield``.
    """
    if app_ctx.runtime and app_ctx.runtime.checkpointer:
        from orchid_ai.checkpointing import shutdown_checkpointer

        await shutdown_checkpointer(app_ctx.runtime.checkpointer)
        app_ctx.runtime.checkpointer = None

    if app_ctx.mcp_token_store:
        await app_ctx.mcp_token_store.close()
        app_ctx.mcp_token_store = None

    if app_ctx.chat_repo:
        await app_ctx.chat_repo.close()
        app_ctx.chat_repo = None

    if app_ctx.http_client:
        await app_ctx.http_client.aclose()
        app_ctx.http_client = None
