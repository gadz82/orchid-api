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

# ── Nested YAML path → flat env var mapping ────────────────────
_YAML_TO_ENV: dict[tuple[str, str], str] = {
    # ── agents ────────────────────────────────────────────────
    ("agents", "config_path"): "AGENTS_CONFIG_PATH",
    # ── llm ───────────────────────────────────────────────────
    ("llm", "model"): "LITELLM_MODEL",
    ("llm", "ollama_api_base"): "OLLAMA_API_BASE",
    ("llm", "groq_api_key"): "GROQ_API_KEY",
    ("llm", "gemini_api_key"): "GEMINI_API_KEY",
    ("llm", "anthropic_api_key"): "ANTHROPIC_API_KEY",
    ("llm", "openai_api_key"): "OPENAI_API_KEY",
    # ── auth ──────────────────────────────────────────────────
    ("auth", "dev_bypass"): "DEV_AUTH_BYPASS",
    ("auth", "identity_resolver_class"): "IDENTITY_RESOLVER_CLASS",
    ("auth", "domain"): "AUTH_DOMAIN",
    # ── startup ──────────────────────────────────────────────
    ("startup", "hook"): "STARTUP_HOOK",
    # ── rag ───────────────────────────────────────────────────
    ("rag", "vector_backend"): "VECTOR_BACKEND",
    ("rag", "qdrant_url"): "QDRANT_URL",
    ("rag", "embedding_model"): "EMBEDDING_MODEL",
    ("rag", "openai_api_key"): "OPENAI_API_KEY",
    ("rag", "gemini_api_key"): "GEMINI_API_KEY",
    # ── upload ────────────────────────────────────────────────
    ("upload", "vision_model"): "VISION_MODEL",
    ("upload", "namespace"): "UPLOAD_NAMESPACE",
    ("upload", "max_size_mb"): "UPLOAD_MAX_SIZE_MB",
    ("upload", "chunk_size"): "CHUNK_SIZE",
    ("upload", "chunk_overlap"): "CHUNK_OVERLAP",
    # ── storage ───────────────────────────────────────────────
    ("storage", "class"): "CHAT_STORAGE_CLASS",
    ("storage", "dsn"): "CHAT_DB_DSN",
    # ── mcp ───────────────────────────────────────────────────
    ("mcp", "catalog_url"): "MCP_CATALOG_URL",
    ("mcp", "notifications_url"): "MCP_NOTIFICATIONS_URL",
    # ── tracing ───────────────────────────────────────────────
    ("tracing", "langsmith_tracing"): "LANGSMITH_TRACING",
    ("tracing", "langsmith_api_key"): "LANGSMITH_API_KEY",
    ("tracing", "langsmith_project"): "LANGSMITH_PROJECT",
}


def _apply_yaml_config() -> None:
    """Load ``orchid.yml`` and export values as env vars (if not already set)."""
    config_path = os.environ.get("ORCHID_CONFIG", "") or os.environ.get("DOCEBAU_CONFIG", "")
    if not config_path:
        return

    try:
        import yaml

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("[Settings] ORCHID_CONFIG=%s not found — ignoring", config_path)
        return

    applied = 0
    total = 0
    for section, body in data.items():
        if not isinstance(body, dict):
            continue
        for key, value in body.items():
            total += 1
            env_var = _YAML_TO_ENV.get((section, key))
            if env_var is None:
                logger.debug(
                    "[Settings] Unknown YAML key %s.%s — skipping",
                    section,
                    key,
                )
                continue
            if env_var not in os.environ:
                os.environ[env_var] = str(value)
                applied += 1

    logger.info(
        "[Settings] Loaded %d/%d values from %s (env overrides take precedence)",
        applied,
        total,
        config_path,
    )


# Apply once at import time — before any Settings() call.
_apply_yaml_config()


class Settings(BaseSettings):
    """All configuration for the agents API, read from env vars."""

    # ── Auth ──────────────────────────────────────────────────
    identity_resolver_class: str = ""  # dotted path to IdentityResolver subclass
    auth_domain: str = ""  # default domain for identity resolution

    # ── LLM ───────────────────────────────────────────────────
    litellm_model: str = "ollama/llama3.2"
    groq_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Agent config (ADR-016) ──────────────────────────────────
    agents_config_path: str = "agents.yaml"

    # ── Vector DB ─────────────────────────────────────────────
    qdrant_url: str = "http://qdrant:6333"
    vector_backend: str = "qdrant"

    # ── Embeddings ──────────────────────────────────────────
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str = ""

    # ── Chat persistence ───────────────────────────────────
    chat_storage_class: str = "orchid.persistence.sqlite.SQLiteChatStorage"
    chat_db_dsn: str = "~/.orchid/chats.db"

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

    # ── MCP ───────────────────────────────────────────────────
    mcp_catalog_url: str = ""
    mcp_notifications_url: str = ""

    # ── Tracing ───────────────────────────────────────────────
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "agents"


def get_settings() -> Settings:
    """Singleton-ish factory — pydantic-settings reads env on each call."""
    return Settings()
