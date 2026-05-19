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

import inspect
import logging
import os
from typing import Any

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
        sig = inspect.signature(resolver_cls.__init__)
        if "http_client" in sig.parameters:
            app_ctx.identity_resolver = resolver_cls(http_client=app_ctx.http_client)
        else:
            app_ctx.identity_resolver = resolver_cls()
        logger.info("[API] Identity resolver: %s", s.identity_resolver_class)
    elif s.dev_auth_bypass:
        # The HTTP layer short-circuits to a hardcoded context in auth.py, but
        # the events processor calls the resolver directly for act_as_user
        # Bloom triggers.  Wire the dev resolver so those paths work too.
        from .dev_identity import DevBypassIdentityResolver

        app_ctx.identity_resolver = DevBypassIdentityResolver()
        logger.warning(
            "[API] No identity resolver configured — using DevBypassIdentityResolver "
            "because DEV_AUTH_BYPASS=true.  MUST NOT be used in production."
        )
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

    # ── Auth-exchange client (optional — code exchange proxy) ──
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
    #
    # For Markdown config (``ORCHID_CONFIG=orchid.md`` or auto-detected),
    # the path is passed through so ``from_config_path`` auto-detects the
    # format and delegates to the MD loader.
    config_path = os.environ.get("ORCHID_CONFIG", "")
    app_ctx.orchid = await Orchid.from_config_path(
        config_path=config_path,
        apply_yaml=bool(config_path and not config_path.endswith(".md")),
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

    # ── Pollen + Bloom (events) — opt-in via agents.yaml ──
    # When ``events.enabled: false`` (the default) ``start_events``
    # returns an :class:`EventsRuntime` with ``enabled=False`` and
    # zero side effects: no producers started, no tables touched,
    # no background tasks created.  Enabled deployments boot the
    # dispatcher / processor / producers here so the four event
    # routers (signals / jobs / runs / schedules) can serve traffic.
    from orchid_ai.config.schema_events import OrchidEventsConfig

    from .events_bootstrap import start_events

    events_cfg = app_ctx.orchid.config.events if app_ctx.orchid is not None else None
    # Guard against mocked / partial configs in tests: treat anything
    # that isn't the real Pydantic model as 'no events'.
    if not isinstance(events_cfg, OrchidEventsConfig):
        events_cfg = None
    app_ctx.events = await start_events(
        events_config=events_cfg,
        chat_storage=app_ctx.chat_repo,
        identity_resolver=app_ctx.identity_resolver,
        session_warmer=(app_ctx.orchid.session_warmer if app_ctx.orchid is not None else None),
        known_agents=set(
            (app_ctx.orchid.config.agents.keys()) if events_cfg is not None and app_ctx.orchid is not None else []
        ),
        graph_invoker=_build_graph_invoker() if app_ctx.orchid is not None else None,
    )
    if app_ctx.events.enabled:
        # Build the FastAPI-backed HTTP ingestion producer when the
        # events config includes at least one ingestion source.
        # HTTPIngestionProducer is an orchid-api adapter (FastAPI dep),
        # so it lives here rather than in the framework library.
        if events_cfg is not None and events_cfg.ingestion.sources:
            from orchid_ai.events.bootstrap import build_signal_source_registry

            from .events.producers.http import HTTPIngestionProducer

            registry = build_signal_source_registry(events_cfg.ingestion.sources)
            http_producer = HTTPIngestionProducer(registry=registry)
            await http_producer.start(app_ctx.events.dispatcher)
            app_ctx.events.producers.append(http_producer)
            app_ctx.events.http_producer = http_producer
        # §26.4 — operator nudge: warn when role mapping is absent
        # AND a service-account trigger exists with default visibility
        # (admin-only).  Without an admin role, those runs are
        # invisible to everyone except via DB inspection.
        _warn_when_no_admin_role_mapping(events_cfg, app_ctx.identity_resolver)
        logger.info(
            "[API] Events subsystem ENABLED — producers=%d processor=%s",
            len(app_ctx.events.producers),
            "yes" if app_ctx.events.processor else "no",
        )

    # ── Expired-token cleanup (one-shot at startup) ───────
    # The MCP token store accumulates expired rows over time; nothing
    # reads them once ``record.is_expired`` is true, but they sit in
    # the DB as a forensic record of every server a user authorised.
    # Run a single ``DELETE`` here so each restart trims the back-log.
    # Operators wanting periodic cleanup wire a cron / k8s job to call
    # ``OrchidMCPTokenStore.cleanup_expired`` directly.  Failures are
    # non-fatal — the gateway still serves traffic.
    if app_ctx.orchid.mcp_token_store is not None:
        try:
            removed = await app_ctx.orchid.mcp_token_store.cleanup_expired()
            if removed:
                logger.info("[API] Purged %d expired MCP token row(s) at startup", removed)
        except Exception as exc:
            logger.warning("[API] MCP token cleanup raised: %s", exc)

    logger.info(
        "[API] Ready — model=%s, vector_backend=%s, agents=%s",
        s.litellm_model,
        s.vector_backend,
        list(app_ctx.orchid.config.agents.keys()),
    )


def _build_graph_invoker():
    """Return a closure that invokes the compiled LangGraph for a Bloom run.

    The closure captures ``app_ctx`` by reference so it always uses the
    graph that was built during startup — safe because both the graph and
    ``app_ctx`` are module-level singletons that are never replaced after
    ``setup_orchid`` returns.

    The invoker builds a minimal initial state from the rendered
    ``JobSpec.prompt`` and the materialised ``OrchidAuthContext``,
    then calls ``graph.ainvoke``.  The LangGraph state's ``final_response``
    field (populated by the supervisor when it decides it is done) is
    returned as-is; ``GraphJobRunner._extract_final_content`` picks it up.
    """
    from langchain_core.messages import HumanMessage

    async def _invoker(run: Any, auth: Any) -> dict:
        graph = app_ctx.orchid.graph
        chat_id = str(run.run_id)
        state = {
            "messages": [HumanMessage(content=run.spec.prompt)],
            "auth_context": auth,
            "chat_id": chat_id,
        }
        config = {"configurable": {"thread_id": chat_id}}
        result = await graph.ainvoke(state, config=config)
        # Return only the serializable final_response — the full graph
        # state contains LangChain BaseMessage objects that are not
        # JSON-serializable and would cause job_store.update() to fail,
        # which prevents the queue ack and triggers spurious retries.
        return {"final_response": result.get("final_response") or ""}

    return _invoker


def _warn_when_no_admin_role_mapping(events_cfg: Any, resolver: Any) -> None:
    """Per §26.4 — log a single warning when the configured resolver
    likely doesn't populate ``OrchidAuthContext.roles`` AND at least
    one ``service_account`` trigger has the default ``admin``
    visibility.  Without the role mapping those runs are inaccessible
    to everyone but operators with DB access.

    The detection is heuristic: if the resolver class name doesn't
    contain ``Role`` or ``Admin`` AND its ``resolve`` method's
    docstring lacks a ``roles`` reference, we assume it's a vanilla
    bearer-only resolver.  False positives here are fine — the warn
    nudges the operator to confirm.
    """
    if events_cfg is None or not events_cfg.enabled:
        return
    # Anyone with at least one default-visibility service-account trigger?
    has_default_sa = False
    for t in events_cfg.triggers:
        identity_mode = getattr(t.emits.identity, "mode", "")
        if identity_mode == "service_account" and t.emits.visibility is None:
            has_default_sa = True
            break
    if not has_default_sa:
        return
    if resolver is None:
        logger.warning(
            "[API] events.enabled=true with a service_account trigger AND no "
            "identity resolver configured — every JobRun from that trigger "
            "will be invisible to API readers (visibility='admin' but no "
            "auth.roles to mark anyone admin).  Set IDENTITY_RESOLVER_CLASS "
            "to a resolver that populates OrchidAuthContext.roles, or opt the "
            "trigger into visibility='tenant' explicitly (see spec §26.4)."
        )
        return
    cls = type(resolver)
    name = cls.__name__
    looks_role_aware = (
        "Role" in name or "Admin" in name or "roles" in (cls.__doc__ or "") or "roles" in (cls.resolve.__doc__ or "")
    )
    if not looks_role_aware:
        logger.warning(
            "[API] Identity resolver %s.%s does not appear to populate "
            "OrchidAuthContext.roles; service_account triggers default to "
            "admin-only visibility (§26.4) which means those runs will be "
            "invisible to every API reader.  Wire role mapping into your "
            "resolver, or opt the affected triggers into visibility='tenant'.",
            cls.__module__,
            name,
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
