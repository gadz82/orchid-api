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


def _apply_yaml_config() -> None:
    """Load ``orchid.yml`` and export values as env vars (if not already set)."""
    config_path = os.environ.get("ORCHID_CONFIG", "") or os.environ.get("DOCEBAU_CONFIG", "")
    if not config_path:
        return

    from orchid_ai.config.yaml_env import apply_yaml_to_env

    apply_yaml_to_env(config_path)


# Apply once at import time — before any Settings() call.
_apply_yaml_config()


class Settings(BaseSettings):
    """All configuration for the agents API, read from env vars."""

    # ── Auth ──────────────────────────────────────────────────
    identity_resolver_class: str = ""  # dotted path to OrchidIdentityResolver subclass
    auth_domain: str = ""  # default domain for identity resolution

    # ── LLM ───────────────────────────────────────────────────
    # Provider API keys (GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY,
    # OPENAI_API_KEY, …) are read directly by LiteLLM / the LangChain
    # chat-model integrations from the environment — there is no need
    # to re-declare them here.  Setting them via docker-compose or a
    # ``.env`` file is sufficient.
    litellm_model: str = "ollama/llama3.2"

    # ── Agent config (ADR-016) ──────────────────────────────────
    agents_config_path: str = "agents.yaml"

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

    # ── Dev mode ──────────────────────────────────────────────
    dev_auth_bypass: bool = False

    # ── Startup hook ─────────────────────────────────────────
    startup_hook: str = ""

    # ── Admin endpoints ──────────────────────────────────────
    # The legacy ``POST /index`` endpoint can trigger a full reindex —
    # disabled by default so a plain authenticated user cannot DOS the
    # vector store.  Flip to ``true`` (via env or orchid.yml) for dev /
    # ops workflows.
    allow_index_endpoint: bool = False

    # ── MCP OAuth token storage (shares DB with chat persistence) ──
    mcp_token_store_class: str = "orchid_ai.persistence.mcp_token_sqlite.OrchidSQLiteMCPTokenStore"
    mcp_token_store_dsn: str = "~/.orchid/chats.db"  # same DB as chat storage by default

    # ── MCP 2025-03-26 client-registration store (RFC 7591 DCR) ──
    # Per-server discovered endpoints + DCR-issued credentials.  Same
    # DSN as the chat + token stores by default (all three backed by
    # the same DB via shared migrations v001 / v002).
    mcp_client_registration_store_class: str = (
        "orchid_ai.persistence.mcp_client_registration_sqlite.OrchidSQLiteMCPClientRegistrationStore"
    )
    mcp_client_registration_store_dsn: str = "~/.orchid/chats.db"

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
