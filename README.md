<p align="center">
  <img src="icon.svg" alt="Orchid" width="80" />
</p>

<h1 align="center">Orchid API</h1>

FastAPI server for the [Orchid](https://github.com/gadz82/orchid) multi-agent AI framework.

Provides HTTP endpoints for chat management, message handling, document uploads, and RAG sharing. This is a thin HTTP layer -- all agent logic, graph building, and persistence live in the `orchid` library.

## Features

- Multi-chat session management (create, list, delete)
- Streaming message send with agent graph invocation
- File upload with document parsing and chat-scoped RAG
- Chat sharing (promote RAG data to user scope)
- Pluggable identity resolution (Bearer token -> OrchidAuthContext)
- LangSmith tracing integration
- CORS support for frontend clients

## Installation

```bash
pip install orchid-api
```

Requires the `orchid` library:

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

## Endpoints

### Chat / messages

| Method | Path | Content-Type | Purpose |
|--------|------|-------------|---------|
| `POST` | `/chats` | JSON | Create a chat session |
| `GET` | `/chats` | -- | List user's chat sessions |
| `DELETE` | `/chats/{id}` | -- | Delete a chat session |
| `GET` | `/chats/{id}/messages` | -- | Load chat message history |
| `POST` | `/chats/{id}/messages` | **multipart/form-data** | Send a message (with optional files) |
| `POST` | `/chats/{id}/messages/stream` | **multipart/form-data** | SSE-streamed message send |
| `POST` | `/chats/{id}/upload` | multipart/form-data | Upload documents for chat RAG |
| `POST` | `/chats/{id}/share` | -- | Promote chat RAG data to user scope |
| `POST` | `/chats/{id}/resume` | JSON | Resume after a HITL approval pause |
| `POST` | `/chat` | JSON | Legacy single-shot (no persistence) |
| `GET` | `/health` | -- | Readiness check |

### Outbound MCP OAuth (per-user external-server tokens)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/mcp/auth/servers` | List OAuth MCP servers + user auth status |
| `GET` | `/mcp/auth/servers/{name}/authorize` | Generate OAuth authorization URL (PKCE) |
| `GET` | `/mcp/auth/callback` | OAuth IdP redirect callback |
| `DELETE` | `/mcp/auth/servers/{name}/token` | Revoke stored OAuth token |

### Inbound auth centralisation (Phases 1–5 — see [.knowledge/auth-centralisation.md](../.knowledge/auth-centralisation.md))

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/auth-info` | Public posture + upstream-OAuth discovery (Phase 1) |
| `POST` | `/auth/exchange-code` | Server-side authorization-code exchange (Phase 2) |
| `POST` | `/auth/refresh-token` | Server-side refresh-token exchange (Phase 4B) |
| `POST` | `/auth/resolve-identity` | Identity bridge — upstream token → `OrchidAuthContext` (Phase 4A) |

These four endpoints let downstream OAuth clients (the MCP gateway, Next.js frontends) drop their copy of `client_secret` + userinfo URL + JSON-path hints. All four are unauthenticated — protected by PKCE, single-use codes, or the upstream token itself, none of which leak from the client.

### Inbound MCP gateway state (Phase 3 — multi-replica gateway support)

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

### Gateway exposure config

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/mcp-gateway/config` | Tool title/description overrides + MCP Prompts (consumed by orchid-mcp at session init) |

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
    streaming.py           SSE streaming endpoint (with handoff / agent-result events)
    sharing.py             Promote chat RAG data to user-common scope
    mcp_auth.py            Outbound MCP per-server OAuth: list, authorize, callback, revoke
    mcp_gateway.py         GET /mcp-gateway/config — gateway exposure overrides + prompts
    auth_info.py           GET /auth-info — public posture + upstream-OAuth discovery (Phase 1)
    auth_exchange.py       POST /auth/exchange-code + /auth/refresh-token (Phases 2 + 4B)
    auth_identity.py       POST /auth/resolve-identity — upstream token → identity (Phase 4A)
    mcp_gateway_state.py   /mcp-gateway/state/* — Phase 3 multi-replica gateway state
    legacy.py              Legacy single-shot /chat endpoint + /index (admin)
```

## Embedding orchid-api in your own FastAPI app

Instead of running `orchid_api.main:app` standalone, you can **mount orchid's
endpoints inside an existing FastAPI application** you already own. This is
the right approach when you have your own routes, middleware, auth, and
lifespan, and just want to add the agent/chat layer.

orchid-api exports two building blocks:

```python
from orchid_api import setup_orchid, teardown_orchid
from orchid_api.routers import chats, messages, streaming, resume, sharing, legacy
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
| `STARTUP_HOOK` | -- | Async function called at startup |
| `LANGSMITH_TRACING` | `false` | Enable LangSmith tracing |
| `LANGSMITH_API_KEY` | -- | LangSmith API key |
| `API_BASE_URL` | `http://localhost:8000` | API base URL (for OAuth callback URLs) |

### Auth

| Setting | Default | Purpose |
|---------|---------|---------|
| `DEV_AUTH_BYPASS` | `false` | Skip auth (dev only) |
| `IDENTITY_RESOLVER_CLASS` | -- | Dotted path to `OrchidIdentityResolver` subclass — required for real auth, also powers `/auth/resolve-identity` (Phase 4A) |
| `AUTH_DOMAIN` | -- | Operator-level default platform domain forwarded to the identity resolver |
| `AUTH_CONFIG_PROVIDER_CLASS` | -- | Dotted path to `OrchidAuthConfigProvider` subclass — unlocks `/auth-info` upstream-OAuth discovery (Phase 1) |
| `AUTH_EXCHANGE_CLIENT_CLASS` | -- | Dotted path to `OrchidAuthExchangeClient` subclass — unlocks `/auth/exchange-code` and `/auth/refresh-token` (Phases 2 + 4B) |
| `AUTH_OAUTH_CLIENT_ID_ENV` | -- | Name of the env var holding the public upstream `client_id` (read by the provider at runtime so YAML can be checked into version control) |
| `AUTH_OAUTH_SCOPE` | -- | Advertised OAuth scope for downstream clients |

### Outbound MCP OAuth

| Setting | Default | Purpose |
|---------|---------|---------|
| `MCP_TOKEN_STORE_CLASS` | `orchid_ai.persistence.mcp_token_sqlite.OrchidSQLiteMCPTokenStore` | Per-user OAuth token store (for external MCP servers) |
| `MCP_TOKEN_STORE_DSN` | `~/.orchid/chats.db` | Token store connection string (defaults to chat DB) |
| `MCP_CLIENT_REGISTRATION_STORE_CLASS` | `orchid_ai.persistence.mcp_client_registration_sqlite.OrchidSQLiteMCPClientRegistrationStore` | Per-server discovered endpoints + DCR credentials |
| `MCP_CLIENT_REGISTRATION_STORE_DSN` | `~/.orchid/chats.db` | Registration store DSN |
| `OAUTH_STATE_STORE_CLASS` | `memory` | PKCE/CSRF state store between `/authorize` + `/callback` (`memory` or dotted class path) |
| `OAUTH_STATE_TTL_SECONDS` | `600` | State TTL for the in-flight OAuth dance |

### Inbound MCP gateway state (Phase 3 — multi-replica gateway support)

| Setting | Default | Purpose |
|---------|---------|---------|
| `MCP_GATEWAY_STATE_STORE_CLASS` | `orchid_ai.persistence.mcp_gateway_state_sqlite.OrchidSQLiteMCPGatewayStateStore` | Backend for DCR clients + auth codes + issued tokens |
| `MCP_GATEWAY_STATE_STORE_DSN` | `~/.orchid/chats.db` | Gateway-state DSN (defaults to chat DB) |
| `MCP_GATEWAY_STATE_SERVICE_TOKEN` | -- | Shared secret gating `/mcp-gateway/state/*` — empty disables the endpoint group (returns 503) |

**Priority:** env vars > `orchid.yml` > hardcoded defaults.

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
      title: "Ask the Docebo AI"
  prompts:
    - name: compliance_report
      description: "Generate a compliance-completion report."
      arguments:
        - { name: department, required: true }
      template: "Produce a compliance report for {{department}}."
```

```bash
# Override a title without touching the YAML:
ORCHID_MCP_GATEWAY_TOOL_ORCHID_ASK_TITLE="Ask the Docebo AI"
# Point at an external prompts file:
ORCHID_MCP_GATEWAY_PROMPTS_FILE=/etc/orchid/prompts.yml
```

Auth: the endpoint goes through the standard `get_auth_context`
dependency (respects `DEV_AUTH_BYPASS`).

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -x
ruff check orchid_api/
```

## Code Style

- Python 3.11+, Ruff, line length 120
- `from __future__ import annotations` in every file
- Routers split by domain (SRP): chats, messages, sharing, legacy
- All runtime state in `AppContext` -- no module-level globals

## License

MIT -- see [LICENSE](LICENSE).

