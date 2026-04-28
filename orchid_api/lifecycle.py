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
  - ``orchid`` (the :class:`Orchid` facade owning runtime + graph + persistence)
  - ``identity_resolver``, ``oauth_state_store``, ``http_client``
    (adapter-specific concerns managed at the HTTP layer)

The heavy wiring (reader, chat storage, checkpointer, ...) is delegated
to :class:`orchid_ai.Orchid`; this module only owns its adapter-specific
concerns (tracing, shared HTTP client, identity resolver, OAuth state
store).
"""

from __future__ import annotations

import logging

import httpx

from orchid_ai import Orchid
from orchid_ai.mcp.oauth_state import build_oauth_state_store
from orchid_ai.utils import import_class

from .context import app_ctx
from .settings import Settings, get_settings
from .tracing import configure_tracing

logger = logging.getLogger(__name__)


async def setup_orchid(settings: Settings | None = None) -> None:
    """Initialise the orchid-api runtime.

    Populates the global ``app_ctx`` singleton with everything the
    built-in routers need.  Safe to call from any FastAPI lifespan.

    Parameters
    ----------
    settings : Settings | None
        Optional pre-built Settings object.  When ``None``, reads from
        env vars / ``ORCHID_CONFIG`` via ``get_settings()``.

    Raises
    ------
    Any exception from :meth:`Orchid.from_config_path`, the OAuth state
    store factory, or consumer-provided startup hooks.
    """
    s = settings or get_settings()

    # ── Tracing — must run BEFORE graph build ─────────────
    configure_tracing(
        enabled=s.langsmith_tracing,
        api_key=s.langsmith_api_key,
        project=s.langsmith_project,
    )

    # ── Shared HTTP client ────────────────────────────────
    if app_ctx.http_client is None:
        app_ctx.http_client = httpx.AsyncClient(timeout=15)

    # ── Identity resolver (optional) ──────────────────────
    if s.identity_resolver_class:
        resolver_cls = import_class(s.identity_resolver_class)
        app_ctx.identity_resolver = resolver_cls(http_client=app_ctx.http_client)
        logger.info("[API] Identity resolver: %s", s.identity_resolver_class)
    else:
        app_ctx.identity_resolver = None
        logger.info("[API] No identity resolver configured — only dev_auth_bypass works")

    # ── Auth-config provider (optional — upstream OAuth discovery) ──
    # Resolves non-secret upstream-OAuth endpoints + public client_id
    # from consumer-provided config.  Surfaced over
    # ``GET /auth-info`` so downstream OAuth clients (MCP gateway,
    # frontends) can auto-configure instead of duplicating env vars.
    if s.auth_config_provider_class:
        provider_cls = import_class(s.auth_config_provider_class)
        app_ctx.auth_config_provider = provider_cls()
        logger.info("[API] Auth config provider: %s", s.auth_config_provider_class)
    else:
        app_ctx.auth_config_provider = None

    # ── Auth-exchange client (optional — Phase 2 code exchange proxy) ──
    # When wired, ``POST /auth/exchange-code`` delegates to this client,
    # which holds the upstream ``client_secret`` and performs the
    # authorization-code exchange against the IdP.  Downstream OAuth
    # clients (MCP gateway, frontends) can then run as public PKCE
    # clients and drop their own copy of ``client_secret``.
    if s.auth_exchange_client_class:
        exchange_cls = import_class(s.auth_exchange_client_class)
        app_ctx.auth_exchange_client = exchange_cls()
        logger.info("[API] Auth exchange client: %s", s.auth_exchange_client_class)
    else:
        app_ctx.auth_exchange_client = None

    # ── Build the framework via the mandatory ``Orchid`` facade ──
    # orchid-api applies YAML → env at module import time (settings.py),
    # so ``apply_yaml=False`` prevents a double-application; every knob
    # below is already resolved from ``Settings``.
    app_ctx.orchid = await Orchid.from_config_path(
        config_path="",
        apply_yaml=False,
        agents_config_path=s.agents_config_path,
        model=s.litellm_model,
        vector_backend=s.vector_backend,
        qdrant_url=s.qdrant_url,
        embedding_model=s.embedding_model,
        chat_storage_class=s.chat_storage_class,
        chat_db_dsn=s.chat_db_dsn,
        chat_extra_migrations_package=s.chat_extra_migrations_package or None,
        mcp_token_store_class=s.mcp_token_store_class,
        mcp_token_store_dsn=s.mcp_token_store_dsn,
        mcp_client_registration_store_class=s.mcp_client_registration_store_class,
        mcp_client_registration_store_dsn=s.mcp_client_registration_store_dsn,
        mcp_gateway_state_store_class=s.mcp_gateway_state_store_class,
        mcp_gateway_state_store_dsn=s.mcp_gateway_state_store_dsn,
        checkpointer_type=s.checkpointer_type,
        checkpointer_dsn=s.checkpointer_dsn,
        startup_hook=s.startup_hook,
        startup_hook_kwargs={"settings": s},
    )

    # ── OAuth PKCE / CSRF state store ─────────────────────
    app_ctx.oauth_state_store = await build_oauth_state_store(
        store_type=s.oauth_state_store_class,
        dsn=s.oauth_state_store_dsn,
        ttl_seconds=float(s.oauth_state_ttl_seconds),
    )

    # ── Proactive MCP capability warm-up ──────────────────
    # ``auth.mode: none`` MCP servers need no user identity, so we can
    # populate their per-server capability caches before any user
    # authenticates.  ``passthrough`` and ``oauth`` servers wait for a
    # user-session start (``POST /session/warm`` from the frontend, or
    # the lazy backstop in ``get_auth_context`` on first authenticated
    # request).  Failures here NEVER abort startup.
    try:
        warm_report = await app_ctx.orchid.warm_unauthenticated_capabilities()
        logger.info(
            "[API] MCP startup warm-up: warmed=%s, skipped=%s, failed=%s",
            warm_report.warmed,
            warm_report.skipped,
            warm_report.failed,
        )
    except Exception as exc:
        logger.warning("[API] MCP startup warm-up raised: %s", exc)

    logger.info(
        "[API] Ready — model=%s, vector_backend=%s, agents=%s",
        s.litellm_model,
        s.vector_backend,
        list(app_ctx.orchid.config.agents.keys()),
    )


async def teardown_orchid() -> None:
    """Release all orchid-api resources.

    Idempotent: safe to call even when ``setup_orchid`` was not called
    or already torn down.  Call this from your FastAPI lifespan after
    ``yield``.

    Delegates the library-level resources to
    :meth:`context.AppContext.release_resources` (which in turn calls
    :meth:`Orchid.close`) and only retains the adapter-specific HTTP
    client close.
    """
    await app_ctx.release_resources()

    if app_ctx.http_client:
        await app_ctx.http_client.aclose()
        app_ctx.http_client = None
