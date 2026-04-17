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
- Pluggable identity resolution (Bearer token -> AuthContext)
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

| Method | Path | Content-Type | Purpose |
|--------|------|-------------|---------|
| `POST` | `/chats` | JSON | Create a chat session |
| `GET` | `/chats` | -- | List user's chat sessions |
| `DELETE` | `/chats/{id}` | -- | Delete a chat session |
| `GET` | `/chats/{id}/messages` | -- | Load chat message history |
| `POST` | `/chats/{id}/messages` | **multipart/form-data** | Send a message (with optional files) |
| `POST` | `/chats/{id}/upload` | multipart/form-data | Upload documents for chat RAG |
| `POST` | `/chats/{id}/share` | -- | Promote chat RAG data to user scope |
| `GET` | `/mcp/auth/servers` | -- | List OAuth MCP servers + user auth status |
| `GET` | `/mcp/auth/servers/{name}/authorize` | -- | Generate OAuth authorization URL (PKCE) |
| `GET` | `/mcp/auth/callback` | -- | OAuth IdP redirect callback |
| `DELETE` | `/mcp/auth/servers/{name}/token` | -- | Revoke stored OAuth token |
| `POST` | `/chat` | JSON | Legacy single-shot (no persistence) |
| `GET` | `/health` | -- | Readiness check |

## Architecture

```
orchid_api/
  main.py          FastAPI app + lifespan + router plugin discovery
  settings.py      Pydantic BaseSettings + YAML overlay (shared with CLI)
  context.py       AppContext dataclass (singleton, populated at startup)
  auth.py          Bearer token -> AuthContext via pluggable IdentityResolver
  models.py        Pydantic request/response models (incl. InterruptResponse)
  tracing.py       LangSmith setup
  routers/
    _helpers.py    Shared: verify_chat_ownership, auto_title_if_first_message,
                   build_interrupt_response, prepare_graph_state
    chats.py       CRUD: create, list, delete chat sessions
    messages.py    Send messages + document upload (multipart/form-data)
    resume.py      Resume graph after Human-in-the-Loop tool approval
    streaming.py   SSE streaming endpoint (with handoff / agent-result events)
    sharing.py     Promote chat RAG data to user-common scope
    mcp_auth.py    MCP per-server OAuth: list, authorize, callback, revoke
    legacy.py      Legacy single-shot /chat endpoint + /index (admin)
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
from orchid_ai.core.state import AuthContext

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/stats")
async def stats(auth: AuthContext = Depends(get_auth_context)):
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

| Setting | Default | Purpose |
|---------|---------|---------|
| `LITELLM_MODEL` | `ollama/llama3.2` | LLM model identifier |
| `AGENTS_CONFIG_PATH` | `agents.yaml` | Path to agent YAML config |
| `VECTOR_BACKEND` | `qdrant` | Vector store backend (`qdrant` or `null`) |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant connection URL |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `CHAT_STORAGE_CLASS` | `orchid_ai.persistence.sqlite.SQLiteChatStorage` | Storage backend class |
| `CHAT_DB_DSN` | `~/.orchid/chats.db` | Database connection string |
| `DEV_AUTH_BYPASS` | `false` | Skip auth (dev only) |
| `IDENTITY_RESOLVER_CLASS` | -- | Dotted path to IdentityResolver subclass |
| `STARTUP_HOOK` | -- | Async function called at startup |
| `LANGSMITH_TRACING` | `false` | Enable LangSmith tracing |
| `LANGSMITH_API_KEY` | -- | LangSmith API key |
| `MCP_TOKEN_STORE_CLASS` | `orchid_ai.persistence.mcp_token_sqlite.SQLiteMCPTokenStore` | MCP OAuth token store backend |
| `MCP_TOKEN_STORE_DSN` | `~/.orchid/mcp_tokens.db` | Token store connection string |
| `API_BASE_URL` | `http://localhost:8000` | API base URL (for OAuth callback URLs) |

**Priority:** env vars > `orchid.yml` > hardcoded defaults.

## Docker

`orchid-api` is a pip package — it does not ship a Dockerfile. Dockerfiles live in consumer projects that depend on it:

- **`docebo/Dockerfile`** — Docebo deployment (PostgreSQL + Qdrant)
- **`examples/Dockerfile`** — Demo deployment (SQLite + Qdrant)

```bash
docker compose -f docker-compose.demo.yml up --build    # examples (SQLite)
docker compose -f docker-compose.local.yml up --build   # docebo (PostgreSQL + Qdrant)
```

## Development

```bash
pip install -e ".[dev]"
ORCHID_CONFIG=orchid.yml uvicorn orchid_api.main:app --reload --port 8000
```

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

