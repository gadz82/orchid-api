"""
Centralized application settings — read from environment variables.

Uses pydantic-settings for validation and defaults.  Optionally loads
an ``orchid.yml`` config file whose values act as *defaults* that can
be overridden by real environment variables.

Priority (highest → lowest):
    1. Environment variables (including docker-compose ``environment:``)
    2. ``orchid.yml``  (pointed to by ``ORCHID_CONFIG`` env var)
    3. Hardcoded defaults in this file

The YAML file uses a **nested structure** grouped by domain (``llm:``,
``rag:``, ``storage:``, etc.).  Each nested key maps to a flat env var
via ``_YAML_TO_ENV``.
"""

from __future__ import annotations

import logging
import os

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def _apply_api_yaml_config(config_path: str) -> None:
    """Read the ``api:`` section from ``orchid.yml`` and export as env vars.

    API-specific configuration is handled locally by ``orchid-api`` rather
    than the core framework to keep responsibility segregation clean.
    """
    import yaml

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return

    api_section = data.get("api")
    if not isinstance(api_section, dict):
        return

    _API_YAML_TO_ENV: dict[str, str] = {
        "base_url": "API_BASE_URL",
        "cors_allowed_origins": "CORS_ALLOWED_ORIGINS",
        "allow_index_endpoint": "ALLOW_INDEX_ENDPOINT",
    }

    for key, value in api_section.items():
        env_var = _API_YAML_TO_ENV.get(key)
        if env_var is None:
            continue
        if env_var not in os.environ:
            os.environ[env_var] = str(value)


def _apply_yaml_config() -> None:
    """Load ``orchid.yml`` and export values as env vars (if not already set).

    Skips ``.md`` files — Markdown config applies its own env-var mapping
    through the :class:`orchid_ai.Orchid` facade.

    The ``api:`` section is skipped by the core framework and applied
    locally by :func:`_apply_api_yaml_config` so API-server concerns
    stay inside ``orchid-api``.
    """
    config_path = os.environ.get("ORCHID_CONFIG", "")
    if not config_path:
        return
    if config_path.endswith(".md"):
        return

    from orchid_ai.config.yaml_env import apply_yaml_to_env

    apply_yaml_to_env(config_path, skip_sections={"api"})
    _apply_api_yaml_config(config_path)


# Apply once at import time — before any Settings() call.
_apply_yaml_config()


class Settings(BaseSettings):
    """All configuration for the agents API, read from env vars."""

    # ── Auth ──────────────────────────────────────────────────
    identity_resolver_class: str = ""  # dotted path to OrchidIdentityResolver subclass
    auth_domain: str = ""  # default domain for identity resolution
    # Dotted path to an OrchidAuthConfigProvider subclass.  When set,
    # ``GET /auth-info`` returns the resolved upstream-OAuth discovery
    # block so downstream OAuth clients (MCP gateway, frontends) can
    # auto-configure instead of duplicating endpoint/client_id env vars.
    auth_config_provider_class: str = ""
    # Dotted path to an OrchidAuthExchangeClient subclass.  When set,
    # orchid-api exposes ``POST /auth/exchange-code`` and handles the
    # secret-bearing upstream-OAuth exchange on behalf of downstream
    # OAuth clients.  Lets the MCP gateway and Next.js frontends run
    # as public PKCE-only clients without holding ``client_secret``
    # themselves.
    auth_exchange_client_class: str = ""
    # Name of the env var that holds the PUBLIC upstream-OAuth
    # ``client_id``.  The provider reads the env var named here at
    # runtime.  A level of indirection so ``orchid.yml`` can be checked
    # into version control without leaking the actual client id.
    auth_oauth_client_id_env: str = ""
    # Advertised OAuth scope for downstream clients — empty string means
    # "use whatever the upstream defaults to".
    auth_oauth_scope: str = ""

    # ── LLM ───────────────────────────────────────────────────
    # Provider API keys (GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY,
    # OPENAI_API_KEY, …) are read directly by LiteLLM / the LangChain
    # chat-model integrations from the environment — there is no need
    # to re-declare them here.  Setting them via docker-compose or a
    # ``.env`` file is sufficient.
    litellm_model: str = "ollama/llama3.2"

    # ── Agent config ──────────────────────────────────
    agents_config_path: str = "agents.yaml"

    # ── Config format — auto-detect or force md / yaml ────────
    # ``auto`` (default): picks based on file extension (``.md`` → MD,
    # ``.yaml`` / ``.yml`` → YAML, directory → checks for ``orchid.md``
    # then ``orchid.yml``).
    # ``md``: force Markdown config loader.
    # ``yaml``: force YAML config loader (existing behaviour).
    orchid_config_format: str = "auto"

    # ── Hot-reload ────────────────────────────────────────────
    # Seconds between config-change checks.  0 = disabled.
    # Only active when a config watcher is present (created
    # automatically by the MD config loader when ``watch=True``).
    orchid_reload_interval: int = 30

    # ── Vector DB ─────────────────────────────────────────────
    qdrant_url: str = "http://qdrant:6333"
    vector_backend: str = "qdrant"

    # ── Embeddings ──────────────────────────────────────────
    embedding_model: str = "text-embedding-3-small"

    # ── Chat persistence ───────────────────────────────────
    chat_storage_class: str = "orchid_ai.persistence.sqlite.OrchidSQLiteChatStorage"
    chat_db_dsn: str = "~/.orchid/chats.db"

    # Dotted import path of an integrator-supplied migrations package.
    # Applied after the framework migrations by both the chat storage and
    # the MCP token store (they share the DB).  Empty string disables it.
    chat_extra_migrations_package: str = ""

    # ── Document upload ───────────────────────────────────────
    vision_model: str = ""
    upload_namespace: str = "uploads"
    upload_max_size_mb: int = 20
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # ── Content sources ─────────────────────────────────────────
    content_sources_json: str = ""

    # ── Streaming ─────────────────────────────────────────────
    # Hard ceiling on a single SSE response. The graph and any MCP
    # tool it dispatches share this budget — once it expires the
    # generator emits a final ``error`` event and stops streaming. Set
    # high enough to accommodate slow tools but low enough to bound
    # damage when an upstream hangs.
    stream_max_seconds: int = 300

    # ── Rate limiting (per tenant + user, in-memory token bucket) ──
    # Each limit is the burst capacity AND the refill-per-period; a
    # user who hits the cap waits for a token to refill before the
    # endpoint accepts their next call. Set ``rate_limit_*`` to 0 to
    # disable that specific bucket; the dependency still runs (so
    # auth still resolves) but never rejects.
    rate_limit_messages_per_minute: int = 30
    rate_limit_uploads_per_minute: int = 10
    rate_limit_index_per_minute: int = 5

    # ── Dev mode ──────────────────────────────────────────────
    dev_auth_bypass: bool = False

    # ── Startup hook ─────────────────────────────────────────
    startup_hook: str = ""

    # ── Admin endpoints ──────────────────────────────────────
    # ``POST /index`` triggers a full reindex — disabled by default so
    # a plain authenticated user cannot DOS the vector store. Flip to
    # ``true`` (via env or orchid.yml) for dev / ops workflows.
    allow_index_endpoint: bool = False

    # ── MCP OAuth token storage (shares DB with chat persistence) ──
    mcp_token_store_class: str = "orchid_ai.persistence.mcp_token_sqlite.OrchidSQLiteMCPTokenStore"
    mcp_token_store_dsn: str = "~/.orchid/chats.db"  # same DB as chat storage by default

    # ── MCP 2025-03-26 client-registration store (RFC 7591 DCR) ──
    # Per-server discovered endpoints + DCR-issued credentials.  Same
    # DSN as the chat + token stores by default (all four backed by
    # the same DB via the unified v001 migration).
    mcp_client_registration_store_class: str = (
        "orchid_ai.persistence.mcp_client_registration_sqlite.OrchidSQLiteMCPClientRegistrationStore"
    )
    mcp_client_registration_store_dsn: str = "~/.orchid/chats.db"

    # ── MCP gateway-state store (INBOUND MCP OAuth) ────
    # Holds DCR registrations, pending auth codes, and issued
    # access/refresh tokens for the orchid-mcp gateway.  Shared
    # across replicas so multi-instance gateway deployments don't
    # reinvent their own state.
    mcp_gateway_state_store_class: str = (
        "orchid_ai.persistence.mcp_gateway_state_sqlite.OrchidSQLiteMCPGatewayStateStore"
    )
    mcp_gateway_state_store_dsn: str = "~/.orchid/chats.db"
    # Shared service token — downstream gateways (orchid-mcp) must
    # present this on every ``/mcp-gateway/state/*`` request.  Empty
    # string disables the endpoints entirely (returns 503 at the
    # router) — the safe posture when no token is configured.
    mcp_gateway_state_service_token: str = ""

    # ── MCP OAuth state store (PKCE + CSRF state between /authorize + /callback) ──
    # Built-in types: "memory" (default, single-instance).  Swap for a
    # dotted class path or registered type (e.g. "redis") for multi-worker
    # deployments so state survives across replicas.
    oauth_state_store_class: str = "memory"
    oauth_state_store_dsn: str = ""
    oauth_state_ttl_seconds: int = 600

    # ── Checkpointer (LangGraph state persistence) ────────────
    checkpointer_type: str = ""  # "memory", "sqlite", "postgres", or dotted class path; empty = disabled
    checkpointer_dsn: str = ""  # connection string or file path

    # ── API base URL (for OAuth callback construction) ───────
    api_base_url: str = "http://localhost:8000"

    # ── CORS ───────────────────────────────────────────────────
    # Comma-separated list of allowed browser origins.  Default keeps
    # backward compat with the demo setups (orchid-frontend at :3000
    # on localhost and inside the Docker ``frontend`` service).
    cors_allowed_origins: str = "http://localhost:3000,http://frontend:3000"

    # ── Tracing ───────────────────────────────────────────────
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "agents"


def get_settings() -> Settings:
    """Singleton-ish factory — pydantic-settings reads env on each call."""
    return Settings()
