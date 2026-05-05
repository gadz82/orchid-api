<p align="center">
  <img src="icon.svg" alt="Orchid" width="80" />
</p>

<h1 align="center">Orchid API</h1>

FastAPI server for the [Orchid](https://github.com/gadz82/orchid) multi-agent AI framework.

Provides HTTP endpoints for chat management, streamed message handling, document uploads, RAG sharing, MCP gateway state, and identity bridging. This is a thin HTTP layer — all agent logic, graph building, and persistence live in the `orchid` library.

## Features

- **Multi-chat session management** — create, list, share, and delete chats per `(tenant_id, user_id)`.
- **Streamed message send** — SSE streaming with full lifecycle event vocabulary including mini-agent markers.
- **File upload + RAG ingestion** — multipart upload, parse, chunk, embed, store; chat-scoped by default.
- **Chat sharing** — promote chat-scoped RAG data to user scope.
- **HITL resume** — pause on `requires_approval: true` tool calls, expose an interrupt response, resume on user decision.
- **Pluggable identity resolution** — Bearer token → `OrchidAuthContext` via integrator-supplied `OrchidIdentityResolver`.
- **OAuth bridging** — `/auth/exchange-code`, `/auth/refresh-token`, `/auth/resolve-identity` so downstream OAuth clients (orchid-mcp, frontends) drop their copy of `client_secret`.
- **MCP gateway state** — server-backed DCR client / auth-code / token store so orchid-mcp can run multi-replica.
- **MCP gateway exposure** — serves `OrchidMCPGatewayConfig` (tool/prompt overrides) consumed by orchid-mcp.
- **Outbound MCP per-server OAuth** — each user authorises each MCP server independently; tokens stored in `OrchidMCPTokenStore`.
- **LangSmith tracing** + CORS for browser frontends.

## Installation

```bash
pip install orchid-ai orchid-api
```

## Quick Start

```bash
# With orchid.yml config:
ORCHID_CONFIG=path/to/orchid.yml uvicorn orchid_api.main:app --port 8000

# Health check:
curl http://localhost:8000/health
```

For a fully wired demo:

```bash
docker compose -f docker-compose.demo.yml up --build
# API:    http://localhost:8000
# Qdrant: http://localhost:6333
```

## Endpoints

### Chat / messages

| Method | Path | Content-Type | Purpose |
|--------|------|-------------|---------|
| `POST` | `/chats` | JSON | Create a chat session |
| `GET` | `/chats` | — | List user's chat sessions |
| `DELETE` | `/chats/{id}` | — | Delete a chat session |
| `GET` | `/chats/{id}/messages` | — | Load chat message history |
| `POST` | `/chats/{id}/messages` | **multipart/form-data** | Send a message (with optional files) |
| `POST` | `/chats/{id}/messages/stream` | **multipart/form-data** | SSE-streamed message send |
| `POST` | `/chats/{id}/upload` | multipart/form-data | Upload documents for chat RAG |
| `POST` | `/chats/{id}/share` | — | Promote chat RAG data to user scope |
| `POST` | `/chats/{id}/resume` | JSON | Resume after a HITL approval pause |
| `GET` | `/health` | — | Readiness check |

### Outbound MCP OAuth (per-user external-server tokens)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/mcp/auth/servers` | List OAuth MCP servers + user auth status |
| `GET` | `/mcp/auth/servers/{name}/authorize` | Generate OAuth authorization URL (PKCE) |
| `GET` | `/mcp/auth/callback` | OAuth IdP redirect callback |
| `DELETE` | `/mcp/auth/servers/{name}/token` | Revoke stored OAuth token |

### Inbound auth centralisation

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/auth-info` | Public posture + upstream-OAuth discovery |
| `POST` | `/auth/exchange-code` | Server-side authorization-code exchange |
| `POST` | `/auth/refresh-token` | Server-side refresh-token exchange |
| `POST` | `/auth/resolve-identity` | Identity bridge — upstream token → `OrchidAuthContext` |

These four endpoints let downstream OAuth clients (the MCP gateway, Next.js frontends) drop their copy of `client_secret` + userinfo URL + JSON-path hints. All four are unauthenticated — protected by PKCE, single-use codes, or the upstream token itself, none of which leak from the client.

### Inbound MCP gateway state (multi-replica gateway support)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/mcp-gateway/state/clients` | Register a DCR client (RFC 7591) |
| `GET` | `/mcp-gateway/state/clients/{client_id}` | Fetch a registered client |
| `POST` | `/mcp-gateway/state/auth-codes` | Insert a pending auth-code record |
| `POST` | `/mcp-gateway/state/auth-codes/lookup-by-upstream-state` | Correlate via the upstream IdP `state` echo |
| `PATCH` | `/mcp-gateway/state/auth-codes/{code}` | Patch (post-callback `identity` / IdP tokens) |
| `POST` | `/mcp-gateway/state/auth-codes/{code}/consume` | Atomic one-shot consume |
| `POST` | `/mcp-gateway/state/tokens` | Issue a gateway access + refresh pair |
| `POST` | `/mcp-gateway/state/tokens/introspect` | Look up by `access_token` xor `refresh_token` |
| `DELETE` | `/mcp-gateway/state/tokens/{access_token}` | Revoke |

These nine endpoints are gated by a shared service token (`MCP_GATEWAY_STATE_SERVICE_TOKEN`) — leave the setting empty to disable the endpoint group entirely (returns 503).

### MCP session management (capability cache warming)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/session/warm` | Warm MCP capability caches for the current `(tenant_key, user_id)` so the first agentic loop avoids discovery RPCs |

### Gateway exposure config

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/mcp-gateway/config` | Tool title/description overrides + MCP Prompts (consumed by orchid-mcp at session init) |

## Streaming event vocabulary

`POST /chats/{id}/messages/stream` returns Server-Sent Events. Every event is a JSON object on a `data:` line; the relevant `event:` types are:

| Event | Payload | When emitted |
|---|---|---|
| `assistant.delta` | `{ "text": "..." }` | Per token of the assistant's reply |
| `supervisor.routing_decision` | `{ "agents": [...], "execution": "parallel\|sequential\|skill" }` | After the supervisor LLM picks routes |
| `agent.started` | `{ "name": "..." }` | When a sub-agent begins executing |
| `agent.finished` | `{ "name": "...", "summary": "..." }` | When a sub-agent emits its final message |
| `mini_agent.decomposed` | `{ "parent": "...", "count": N, "sub_tasks": [...] }` | When a parent agent's decomposer fires |
| `mini_agent.started` | `{ "parent": "...", "mini_id": "...", "description": "..." }` | When each fork starts |
| `mini_agent.finished` | `{ "parent": "...", "mini_id": "...", "status": "ok\|failed\|timeout", "duration_ms": ... }` | When each fork ends |
| `mini_agent.aggregated` | `{ "parent": "...", "n_outcomes": ... }` | When the aggregator collapses outcomes |
| `tool_call.requires_approval` | `{ "tool": "...", "args": {...}, "interrupt_id": "..." }` | When a HITL tool needs user approval |
| `assistant.complete` | `{ "message": "..." }` | Final completion marker |

A consumer that ignores everything except `assistant.delta` + `assistant.complete` still gets a working chat UI; the other events power richer presentation (mini-agent traces, HITL approval cards, supervisor reasoning pills).

## Architecture

```
orchid_api/
  main.py          FastAPI app + lifespan + router plugin discovery
  settings.py      Pydantic BaseSettings + YAML overlay (shared with CLI)
  context.py       AppContext dataclass (singleton, populated at startup)
  auth.py          Bearer token -> OrchidAuthContext via pluggable OrchidIdentityResolver
  models.py        Pydantic request/response models (incl. InterruptResponse)
  tracing.py       LangSmith setup
  mcp_gateway.py   Resolves OrchidMCPGatewayConfig from agents.yaml + env overrides
  lifecycle.py     setup_orchid / teardown_orchid for embedding in your own FastAPI app
  routers/
    _helpers.py            Shared: verify_chat_ownership, auto_title_if_first_message,
                            build_interrupt_response, prepare_graph_state
    chats.py               CRUD: create, list, delete chat sessions
    messages.py            Send messages + document upload (multipart/form-data)
    resume.py              Resume graph after Human-in-the-Loop tool approval
    streaming.py           SSE streaming endpoint (full event vocabulary above)
    sharing.py             Promote chat RAG data to user-common scope
    mcp_auth.py            Outbound MCP per-server OAuth: list, authorize, callback, revoke
    mcp_gateway.py         GET /mcp-gateway/config — gateway exposure overrides + prompts
    auth_info.py           GET /auth-info — public posture + upstream-OAuth discovery
    auth_exchange.py       POST /auth/exchange-code + /auth/refresh-token
    auth_identity.py       POST /auth/resolve-identity — upstream token → identity
    mcp_gateway_state.py   /mcp-gateway/state/* — multi-replica gateway state
    session.py             POST /session/warm — capability-cache warming
    admin.py               POST /index (admin) — bulk RAG ingestion behind allow_index_endpoint
    diagnostics.py         GET /health — readiness check
```

The lifespan runs (in order):

1. Load `orchid.yml` → `Settings`.
2. Load `agents.yaml` → `OrchidAgentsConfig` (with defaults merged, prompt customisations resolved, mini-agent + parallel-tools blocks validated).
3. Build the LangGraph runtime with all configured agents.
4. Initialise the chat storage backend (default SQLite, swap via `CHAT_STORAGE_CLASS`).
5. Initialise the optional checkpointer (required for HITL resume).
6. Build the MCP token store + client registration store + gateway state store.
7. Warm `auth.mode: none` MCP servers proactively (`OrchidSessionWarmer`).
8. Fire any startup hook declared in `orchid.yml` (`startup.hook`).
9. Discover entry-point routers (`orchid_api.routers` group) and include them.

Teardown reverses the order — the chat storage's `close()`, checkpointer pool, and any hook-owned resources (closed by orchid-api's lifespan, not the hook).

## Embedding orchid-api in your own FastAPI app

Instead of running `orchid_api.main:app` standalone, you can **mount orchid's
endpoints inside an existing FastAPI application** you already own. This is
the right approach when you have your own routes, middleware, auth, and
lifespan, and just want to add the agent/chat layer.

orchid-api exports two building blocks:

```python
from orchid_api import setup_orchid, teardown_orchid
from orchid_api.routers import chats, messages, streaming, resume, sharing
```

`setup_orchid()` runs everything orchid needs (load agent config, build the
graph, init storage + checkpointer + MCP token store, run the startup hook).
`teardown_orchid()` closes those resources.

Minimal embedding example:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

from orchid_api import setup_orchid, teardown_orchid
from orchid_api.routers import chats, messages, streaming, resume

@asynccontextmanager
async def lifespan(app: FastAPI):
    await my_db.connect()             # your setup
    await setup_orchid()              # orchid setup
    yield
    await teardown_orchid()           # orchid teardown
    await my_db.disconnect()          # your teardown

app = FastAPI(title="My App", lifespan=lifespan)

# Your own routes
app.include_router(my_business_router)

# Orchid routes, mounted under /ai (any prefix works)
app.include_router(chats.router,     prefix="/ai")
app.include_router(messages.router,  prefix="/ai")
app.include_router(streaming.router, prefix="/ai")
app.include_router(resume.router,    prefix="/ai")
```

Run as usual: `uvicorn my_app:app --port 8000`.

Full working example: [`examples/embedded-api/`](../examples/embedded-api/).

**Notes:**

- The entry-point plugin system (`orchid_api.routers` group) only triggers
  for `orchid_api.main:app`. When embedding, include routers explicitly.
- `orchid_api.app_ctx` is a module-level singleton — one orchid instance per
  Python process. Fine for embedding; you can't run two differently-configured
  orchids in the same process.
- `setup_orchid()` must complete before any orchid route is called. Always
  place it in the FastAPI lifespan, never inline in a handler.

## Extending the API

Integrators can add **custom FastAPI endpoints** to orchid-api without forking
the framework. Two patterns are supported:

### Pattern A — Import & extend

Write your own `main.py` that imports `orchid_api.main.app` and attaches
custom routers:

```python
# my_project/api.py
from orchid_api.main import app
from .routes import admin_router, analytics_router

app.include_router(admin_router)
app.include_router(analytics_router)
```

Run with: `uvicorn my_project.api:app --port 8000`

### Pattern B — Entry-point plugin (recommended for reusable packages)

Declare an entry point in your package's `pyproject.toml`:

```toml
[project.entry-points."orchid_api.routers"]
admin = "my_package.api.admin:router"
analytics = "my_package.api.analytics:router"
```

Each entry must resolve to a `fastapi.APIRouter` instance. After `pip install`
your package, orchid-api auto-registers the routers at startup. Failed plugins
log a warning but do not block startup.

### Accessing orchid-api internals

Your custom routers can freely import from `orchid_api`:

```python
from fastapi import APIRouter, Depends
from orchid_api.auth import get_auth_context
from orchid_api.context import app_ctx
from orchid_api.settings import get_settings
from orchid_ai.core.state import OrchidAuthContext

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/stats")
async def stats(auth: OrchidAuthContext = Depends(get_auth_context)):
    chat_repo = app_ctx.chat_repo
    reader = app_ctx.runtime.get_reader()
    graph = app_ctx.graph
    return {"tenant": auth.tenant_key, ...}
```

Full working example: [`examples/api-extensions/`](../examples/api-extensions/)
— demonstrates both patterns with `/admin/stats`, `/admin/cache/clear`,
`/admin/rag/index-text`, `/admin/agents` endpoints.

## Configuration

All settings are environment variables, optionally populated from `orchid.yml` via `ORCHID_CONFIG`:

### Core

| Setting | Default | Purpose |
|---------|---------|---------|
| `LITELLM_MODEL` | `ollama/llama3.2` | LLM model identifier |
| `AGENTS_CONFIG_PATH` | `agents.yaml` | Path to agent YAML config |
| `VECTOR_BACKEND` | `qdrant` | Vector store backend (`qdrant` or `null`) |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant connection URL |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `CHAT_STORAGE_CLASS` | `orchid_ai.persistence.sqlite.OrchidSQLiteChatStorage` | Storage backend class |
| `CHAT_DB_DSN` | `~/.orchid/chats.db` | Database connection string |
| `STARTUP_HOOK` | — | Async function called at startup |
| `LANGSMITH_TRACING` | `false` | Enable LangSmith tracing |
| `LANGSMITH_API_KEY` | — | LangSmith API key |
| `API_BASE_URL` | `http://localhost:8000` | API base URL (for OAuth callback URLs) |

### Auth

| Setting | Default | Purpose |
|---------|---------|---------|
| `DEV_AUTH_BYPASS` | `false` | Skip auth (dev only) |
| `IDENTITY_RESOLVER_CLASS` | — | Dotted path to `OrchidIdentityResolver` subclass — required for real auth, also powers `/auth/resolve-identity` |
| `AUTH_DOMAIN` | — | Operator-level default platform domain forwarded to the identity resolver |
| `AUTH_CONFIG_PROVIDER_CLASS` | — | Dotted path to `OrchidAuthConfigProvider` subclass — unlocks `/auth-info` upstream-OAuth discovery |
| `AUTH_EXCHANGE_CLIENT_CLASS` | — | Dotted path to `OrchidAuthExchangeClient` subclass — unlocks `/auth/exchange-code` and `/auth/refresh-token` |
| `AUTH_OAUTH_CLIENT_ID_ENV` | — | Name of the env var holding the public upstream `client_id` (read by the provider at runtime so YAML can be checked into version control) |
| `AUTH_OAUTH_SCOPE` | — | Advertised OAuth scope for downstream clients |

### Outbound MCP OAuth

| Setting | Default | Purpose |
|---------|---------|---------|
| `MCP_TOKEN_STORE_CLASS` | `orchid_ai.persistence.mcp_token_sqlite.OrchidSQLiteMCPTokenStore` | Per-user OAuth token store (for external MCP servers) |
| `MCP_TOKEN_STORE_DSN` | `~/.orchid/chats.db` | Token store connection string (defaults to chat DB) |
| `MCP_CLIENT_REGISTRATION_STORE_CLASS` | `orchid_ai.persistence.mcp_client_registration_sqlite.OrchidSQLiteMCPClientRegistrationStore` | Per-server discovered endpoints + DCR credentials |
| `MCP_CLIENT_REGISTRATION_STORE_DSN` | `~/.orchid/chats.db` | Registration store DSN |
| `OAUTH_STATE_STORE_CLASS` | `memory` | PKCE/CSRF state store between `/authorize` + `/callback` (`memory` or dotted class path) |
| `OAUTH_STATE_TTL_SECONDS` | `600` | State TTL for the in-flight OAuth dance |

### Inbound MCP gateway state (multi-replica gateway support)

| Setting | Default | Purpose |
|---------|---------|---------|
| `MCP_GATEWAY_STATE_STORE_CLASS` | `orchid_ai.persistence.mcp_gateway_state_sqlite.OrchidSQLiteMCPGatewayStateStore` | Backend for DCR clients + auth codes + issued tokens |
| `MCP_GATEWAY_STATE_STORE_DSN` | `~/.orchid/chats.db` | Gateway-state DSN (defaults to chat DB) |
| `MCP_GATEWAY_STATE_SERVICE_TOKEN` | — | Shared secret gating `/mcp-gateway/state/*` — empty disables the endpoint group (returns 503) |

**Priority:** env vars > `orchid.yml` > hardcoded defaults.

## Custom storage backends

The default SQLite storage at `~/.orchid/chats.db` is fine for development and small deployments. For production, swap to PostgreSQL (built-in) or implement your own `OrchidChatStorage` subclass.

```yaml
# orchid.yml — built-in PostgreSQL
storage:
  class: orchid_ai.persistence.postgres.OrchidPostgresChatStorage
  dsn: postgresql://user:pass@host:5432/orchid

# orchid.yml — custom JSON-file backend (see examples/custom-storage/)
storage:
  class: examples.custom-storage.storage.json_file.OrchidJSONChatStorage
  dsn: /var/lib/orchid/chats.json
```

The factory at `orchid_ai.persistence.factory.build_chat_storage` resolves the dotted import path and constructs the backend with `dsn=` and `extra_migrations_package=` kwargs. See [`examples/custom-storage/`](../examples/custom-storage/) for a fully worked example including the contract checklist.

## Multi-tenancy

`get_auth_context` returns an `OrchidAuthContext` with `tenant_key` + `user_id` extracted from the bearer token via the configured `OrchidIdentityResolver`. Every chat / message / RAG operation downstream is scoped by these fields:

- Chat persistence — `(tenant_id, user_id)` is the primary partition key.
- RAG retrieval — the `OrchidRAGScope` includes both fields and is honoured by every built-in retrieval strategy.
- MCP token store + client registrations — per-user.

For multi-tenant deployments, supply an `IdentityResolver` that maps a single bearer token to per-tenant identities (e.g. via JWT claims or a back-channel lookup). The identity resolver is also called by `/auth/resolve-identity` to bridge upstream tokens for orchid-mcp and frontends.

## Deployment patterns

| Shape | Storage | Vector | MCP gateway |
|---|---|---|---|
| **Local dev** | SQLite | `null` (in-process) or local Qdrant | not needed |
| **Single VM** | SQLite or Postgres | Qdrant | single-replica orchid-mcp |
| **Multi-replica behind LB** | Postgres | Qdrant cluster | multi-replica orchid-mcp with `OAUTH_STORE_BACKEND=http` pointed here |
| **Embedded inside an existing FastAPI app** | Whatever the host already uses | Anything `OrchidVectorReader` supports | Optional |

For multi-replica installs:

- Run `>=2` orchid-api replicas behind a load balancer (sticky sessions optional but reduce checkpoint contention).
- Set `MCP_GATEWAY_STATE_SERVICE_TOKEN` and route the orchid-mcp gateway's state stores at orchid-api via `OAUTH_STORE_BACKEND=http`.
- Use Postgres for both chat and checkpoint storage.
- Use a shared Qdrant cluster.

## Docker

`orchid-api` is a pip package — it does not ship a Dockerfile. Each
integrator owns its own Dockerfile + compose file; refer to the
examples in this monorepo (or roll your own) for a starting point:

- **`examples/Dockerfile`** — Demo deployment (SQLite + Qdrant)
- Each consumer project under the monorepo defines its own production
  Dockerfile tailored to its persistence and vector backends.

```bash
# Typical pattern: start the stack with a compose file the consumer project owns.
docker compose -f docker-compose.demo.yml up --build    # examples (SQLite)
```

## Development

```bash
pip install -e ".[dev]"
ORCHID_CONFIG=orchid.yml uvicorn orchid_api.main:app --reload --port 8000
```

## MCP gateway exposure (`/mcp-gateway/config`)

Serves the resolved `OrchidMCPGatewayConfig` — tool title/description
overrides + MCP Prompt templates — consumed by the `orchid-mcp` gateway
at each MCP session init. The feature is **optional**: no `mcp_gateway`
block in `agents.yaml` and no env vars → empty config returned.

Resolution order (highest → lowest):

1. **Env vars**:
   - `ORCHID_MCP_GATEWAY_TOOL_<TOOL_NAME_UPPER>_TITLE`
   - `ORCHID_MCP_GATEWAY_TOOL_<TOOL_NAME_UPPER>_DESCRIPTION`
   - `ORCHID_MCP_GATEWAY_PROMPTS_FILE=/path/to/prompts.yml` (replaces
     the list, not merged — accepts a top-level list or `{prompts: [...]}`)
2. `agents.yaml` `mcp_gateway:` block (framework schema).
3. Empty defaults.

Example:

```yaml
# agents.yaml
mcp_gateway:
  tools:
    orchid_ask:
      title: "Ask the Acme Knowledge Base"
  prompts:
    - name: compliance_report
      description: "Generate a compliance-completion report."
      arguments:
        - { name: department, required: true }
      template: "Produce a compliance report for {{department}}."
```

```bash
# Override a title without touching the YAML:
ORCHID_MCP_GATEWAY_TOOL_ORCHID_ASK_TITLE="Ask the Acme Knowledge Base"
# Point at an external prompts file:
ORCHID_MCP_GATEWAY_PROMPTS_FILE=/etc/orchid/prompts.yml
```

Auth: the endpoint goes through the standard `get_auth_context`
dependency (respects `DEV_AUTH_BYPASS`).

## Troubleshooting

- **`401 Unauthorized` with `dev_bypass: false`** — the `IdentityResolver` raised. Either set `DEV_AUTH_BYPASS=true` for local development or wire a real `IDENTITY_RESOLVER_CLASS`.
- **`Cannot resolve chat storage class '…'`** — the dotted import path is wrong or the package is not on `PYTHONPATH`. Confirm via `python -c "import importlib; importlib.import_module('your.module')"`.
- **HITL approve/deny returns 404** — checkpointer is not configured. Add a `checkpointer:` block to `orchid.yml` (sqlite or postgres) so graph state survives the pause.
- **MCP tools missing from agents** — capability cache hasn't warmed. For `auth.mode: none` servers the warm fires at API startup; for `oauth` / `passthrough` servers, the frontend must call `POST /session/warm` (or wait for the lazy backstop on first message).
- **Streamed events arrive but `assistant.complete` never fires** — typically a downstream tool error. Check `agent.finished` and `tool_call.requires_approval` events for clues; enable `LANGSMITH_TRACING=true` to see the per-step LLM traces.
- **`/mcp-gateway/state/*` returns 503** — `MCP_GATEWAY_STATE_SERVICE_TOKEN` is empty. Set it (and configure orchid-mcp's matching `ORCHID_MCP_GATEWAY_STATE_SERVICE_TOKEN`) to enable the multi-replica gateway state endpoints.
- **`Unknown query transformer 'X'`** — a custom transformer is referenced in YAML but never registered. Move the registration into a startup hook (`startup.hook` in `orchid.yml`) so it fires before agents boot.

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -x
ruff check orchid_api/
```

## Code Style

- Python 3.11+, Ruff, line length 120
- `from __future__ import annotations` in every file
- Routers split by domain (SRP): chats, messages, sharing, streaming, etc.
- All runtime state in `AppContext` — no module-level globals

## License

MIT — see [LICENSE](LICENSE).
