# orchid-api — AI Context

## What This Package Is

**orchid-api** is the FastAPI server for the Orchid multi-agent AI framework. It imports `orchid` (the library) as a dependency and exposes HTTP endpoints for chat management, message handling, document uploads, and RAG sharing. It does NOT contain agent logic, graph building, or persistence implementations — those live in `orchid/`.

## Package Structure

```
orchid-api/
  orchid_api/
    main.py          FastAPI app + lifespan + router include + plugin discovery
    settings.py      Pydantic BaseSettings + YAML overlay via _apply_yaml_config()
    context.py       AppContext dataclass (singleton, populated at startup)
    auth.py          Bearer token -> OrchidAuthContext via pluggable OrchidIdentityResolver (ADR-010)
    models.py        Pydantic response models (incl. InterruptResponse)
    tracing.py       LangSmith setup
    mcp_gateway.py   Resolves OrchidMCPGatewayConfig (env-var overrides + YAML)
    lifecycle.py     setup_orchid / teardown_orchid for embedding in your own FastAPI
    routers/
      chats.py               CRUD: create, list, delete chat sessions
      messages.py            Send messages + document upload (multipart/form-data)
      streaming.py           SSE-streamed message send (Phase 9)
      resume.py              Resume after HITL approval pause
      session.py             POST /session/warm — per-user MCP capability warm-up
      sharing.py             Promote chat RAG data to user-common scope
      mcp_auth.py            Outbound MCP per-server OAuth: list/authorize/callback/revoke
                              (callback also warms the just-authorized server)
      mcp_gateway.py         /mcp-gateway/config — gateway exposure overrides
      auth_info.py           /auth-info — public posture + upstream-OAuth discovery (Phase 1)
      auth_exchange.py       /auth/exchange-code + /auth/refresh-token (Phases 2 + 4B)
      auth_identity.py       /auth/resolve-identity — identity bridge (Phase 4A)
      mcp_gateway_state.py   /mcp-gateway/state/* — Phase 3 multi-replica gateway state
  pyproject.toml
```

## Key Dependencies

| Package | Role |
|---------|------|
| `orchid` | Core framework (agents, graph, RAG, persistence) |
| `fastapi` | HTTP framework |
| `uvicorn` | ASGI server |
| `httpx` | Async HTTP client (for identity resolution) |
| `pydantic-settings` | Environment + YAML config |
| `python-multipart` | File upload support |
| `langchain-core` | LangGraph message types |

## Architecture Rules (Apply When Editing This Package)

1. **This is a thin HTTP layer.** Business logic belongs in `orchid/`, not here. Routers call `orchid` APIs and return responses.

2. **Identity resolution happens ONCE in `auth.py`.** The `get_auth_context` dependency resolves the Bearer token into `OrchidAuthContext`. No other code initiates OAuth flows (ADR-010).

3. **`AppContext` owns a single `Orchid` handle.** `context.py:app_ctx.orchid` is the framework's mandatory :class:`orchid_ai.Orchid` facade, created by `lifecycle.setup_orchid()`. Top-level helpers (`runtime`, `graph`, `chat_repo`, `mcp_token_store`, `agents_config`) are **read-through properties** that delegate to `app_ctx.orchid`, so FastAPI deps (`get_runtime`, `get_graph`, `get_chat_repo`, …) keep their existing contract. The only flat fields are adapter-specific concerns that don't belong inside the framework library: `http_client`, `identity_resolver`, `oauth_state_store`. Routers access everything via `from ..context import app_ctx`.

4. **Routers are split by domain (SRP).** `chats.py` = CRUD, `messages.py` = send + upload, `sharing.py` = share. New endpoints go in the appropriate router, never in `main.py`.

5. **No agent or framework code here.** No `OrchidAgent` subclasses, no graph wiring, no RAG logic. Those belong in `orchid/` or consumer projects.

6. **Settings priority:** env vars > `orchid.yml` > hardcoded defaults. The `_YAML_TO_ENV` mapping in `settings.py` translates nested YAML keys to flat env vars.

7. **Don't persist augmented prompts.** Save the original user message to chat history, NOT the version with prepended file content or RAG context.

8. **MCP capability caches are warmed proactively.** `setup_orchid()` calls `Orchid.warm_unauthenticated_capabilities()` after the framework is built so every `auth.mode: none` MCP server's capability cache is populated before the first request. Per-user warming runs once per `(tenant_key, user_id)` — either explicitly via `POST /session/warm` (preferred, called by the frontend after login) or implicitly as a fire-and-forget task scheduled by `get_auth_context` on the first authenticated request. A per-server post-OAuth warm fires from `oauth_callback` once a token is persisted. All three hooks swallow exceptions — warm failures NEVER abort startup or break a request.

## Configuration (Settings)

All settings are env vars, optionally populated from `orchid.yml` via `ORCHID_CONFIG`. The full matrix is in `README.md`; the high-level groups are:

- **Core** — `LITELLM_MODEL`, `AGENTS_CONFIG_PATH`, `VECTOR_BACKEND`, `QDRANT_URL`, `EMBEDDING_MODEL`, `CHAT_STORAGE_CLASS`, `CHAT_DB_DSN`, `CHAT_EXTRA_MIGRATIONS_PACKAGE`, `STARTUP_HOOK`, `API_BASE_URL`, `LANGSMITH_*`.
- **Auth (consumer-pluggable)** — `IDENTITY_RESOLVER_CLASS` (Phase 4A — also powers `/auth/resolve-identity`), `AUTH_DOMAIN`, `AUTH_CONFIG_PROVIDER_CLASS` (Phase 1), `AUTH_EXCHANGE_CLIENT_CLASS` (Phases 2 + 4B), `AUTH_OAUTH_CLIENT_ID_ENV`, `AUTH_OAUTH_SCOPE`, `DEV_AUTH_BYPASS`.
- **Outbound MCP** — `MCP_TOKEN_STORE_CLASS`, `MCP_TOKEN_STORE_DSN`, `MCP_CLIENT_REGISTRATION_STORE_CLASS`, `MCP_CLIENT_REGISTRATION_STORE_DSN`, `OAUTH_STATE_STORE_CLASS`, `OAUTH_STATE_TTL_SECONDS`.
- **Inbound gateway state (Phase 3)** — `MCP_GATEWAY_STATE_STORE_CLASS`, `MCP_GATEWAY_STATE_STORE_DSN`, `MCP_GATEWAY_STATE_SERVICE_TOKEN` (empty disables `/mcp-gateway/state/*`).

## Running

```bash
# Standalone:
pip install orchid-ai orchid-api
ORCHID_CONFIG=orchid.yml uvicorn orchid_api.main:app --port 8000
```

Dockerfiles live in consumer projects (each integrator ships their own), not here.

## Endpoints

Chat / messages:

| Method | Path | Router | Purpose |
|--------|------|--------|---------|
| POST | `/chats` | chats | Create chat session |
| GET | `/chats` | chats | List user's chats |
| DELETE | `/chats/{id}` | chats | Delete chat |
| GET | `/chats/{id}/messages` | messages | Load chat history |
| POST | `/chats/{id}/messages` | messages | Send message (multipart) |
| POST | `/chats/{id}/messages/stream` | streaming | SSE-streamed message send |
| POST | `/chats/{id}/upload` | messages | Upload documents for chat RAG |
| POST | `/chats/{id}/resume` | resume | Resume after a HITL approval pause |
| POST | `/chats/{id}/share` | sharing | Promote chat RAG to user scope |
| POST | `/session/warm` | session | Warm per-user MCP capability caches (passthrough + oauth) — idempotent |
| GET | `/health` | diagnostics | Readiness check |

Outbound MCP OAuth (per-user external-server tokens):

| Method | Path | Router | Purpose |
|--------|------|--------|---------|
| GET | `/mcp/auth/servers` | mcp_auth | List OAuth servers + user auth status |
| GET | `/mcp/auth/servers/{name}/authorize` | mcp_auth | Generate OAuth authorization URL |
| GET | `/mcp/auth/callback` | mcp_auth | OAuth IdP redirect callback |
| DELETE | `/mcp/auth/servers/{name}/token` | mcp_auth | Revoke stored OAuth token |

Inbound auth centralisation (Phases 1–5 — see [.knowledge/auth-centralisation.md](../.knowledge/auth-centralisation.md)):

| Method | Path | Router | Phase | Purpose |
|--------|------|--------|-------|---------|
| GET | `/auth-info` | auth_info | 1 | Public posture + upstream-OAuth discovery |
| POST | `/auth/exchange-code` | auth_exchange | 2 | Server-side authorization-code exchange |
| POST | `/auth/refresh-token` | auth_exchange | 4B | Server-side refresh-token exchange |
| POST | `/auth/resolve-identity` | auth_identity | 4A | Upstream token → `OrchidAuthContext` |

Inbound MCP gateway state (Phase 3, gated by `MCP_GATEWAY_STATE_SERVICE_TOKEN`):

| Method | Path | Router | Purpose |
|--------|------|--------|---------|
| POST | `/mcp-gateway/state/clients` | mcp_gateway_state | Register a DCR client |
| GET | `/mcp-gateway/state/clients/{client_id}` | mcp_gateway_state | Fetch a registered client |
| POST | `/mcp-gateway/state/auth-codes` | mcp_gateway_state | Insert an auth-code record |
| POST | `/mcp-gateway/state/auth-codes/lookup-by-upstream-state` | mcp_gateway_state | Correlate via upstream `state` echo |
| PATCH | `/mcp-gateway/state/auth-codes/{code}` | mcp_gateway_state | Patch identity / IdP tokens |
| POST | `/mcp-gateway/state/auth-codes/{code}/consume` | mcp_gateway_state | Atomic one-shot consume |
| POST | `/mcp-gateway/state/tokens` | mcp_gateway_state | Issue gateway access + refresh pair |
| POST | `/mcp-gateway/state/tokens/introspect` | mcp_gateway_state | Look up by access xor refresh |
| DELETE | `/mcp-gateway/state/tokens/{access_token}` | mcp_gateway_state | Revoke |

Gateway exposure config:

| Method | Path | Router | Purpose |
|--------|------|--------|---------|
| GET | `/mcp-gateway/config` | mcp_gateway | Resolved MCP-gateway exposure config (tool overrides + prompts) |

## MCP gateway exposure config

``/mcp-gateway/config`` serves an :class:`OrchidMCPGatewayConfig` (tool
title/description overrides + MCP Prompts) consumed by any MCP-facing
gateway (e.g. ``orchid-mcp``) at session init.  Resolution precedence:

1. Env vars (highest): ``ORCHID_MCP_GATEWAY_TOOL_<NAME>_(TITLE|DESCRIPTION)``
   and ``ORCHID_MCP_GATEWAY_PROMPTS_FILE`` (path to a YAML file — replaces
   the YAML ``mcp_gateway.prompts`` list rather than merging).
2. ``agents.yaml`` / programmatic ``OrchidAgentsConfig.mcp_gateway``.
3. Empty defaults (feature is **optional** — no block / no env vars →
   gateway shows built-in tool titles + no prompts).

The endpoint goes through the standard ``get_auth_context`` dependency,
so ``DEV_AUTH_BYPASS=true`` lets unauthenticated callers fetch it for
local dev; production requires a valid Bearer.  See
``orchid_api/mcp_gateway.py`` for the resolver.

## Code Style

- Python 3.11+, Ruff, line length 120
- `from __future__ import annotations` in every file
- Imports: `from orchid_ai.xxx` (never `from src.xxx`)
- No vendor-specific code — platform integrations belong in consumer projects

## Common Pitfalls

- `POST /chats/{id}/messages` uses `multipart/form-data`, not JSON.
- CORS allows `localhost:3000` and `frontend:3000` — add new origins in `main.py` if needed.
- The `lifespan()` function builds the graph at startup. Changes to agent config require a restart.
- Embedding dimension mismatch (768 vs 1536 vs 3072) causes silent retrieval failures. Switching models requires re-indexing.
