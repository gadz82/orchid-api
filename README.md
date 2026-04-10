<p align="center">
  <img src="icon.svg" alt="Orchid" width="80" />
</p>

<h1 align="center">Orchid API</h1>

FastAPI server for the [Orchid](../orchid) multi-agent AI framework.

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
| `POST` | `/chat` | JSON | Legacy single-shot (no persistence) |
| `GET` | `/health` | -- | Readiness check |

## Architecture

```
orchid_api/
  main.py          FastAPI app + lifespan (graph build, storage init, tracing)
  settings.py      Pydantic BaseSettings + YAML overlay
  context.py       AppContext dataclass (singleton, populated at startup)
  auth.py          Bearer token -> AuthContext via pluggable IdentityResolver
  models.py        Pydantic response models
  tracing.py       LangSmith setup
  routers/
    chats.py       CRUD: create, list, delete chat sessions
    messages.py    Send messages + document upload (multipart/form-data)
    sharing.py     Promote chat RAG data to user-common scope
    legacy.py      Legacy single-shot /chat endpoint (JSON body)
```

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

**Priority:** env vars > `orchid.yml` > hardcoded defaults.

## Docker

```dockerfile
# Build (from parent directory):
docker build -f orchid-api/Dockerfile -t orchid-api .

# Run:
docker run -p 8000:8000 -e ORCHID_CONFIG=/app/config/orchid.yml orchid-api
```

Or with docker-compose:

```bash
docker compose -f docker-compose.demo.yml up --build    # SQLite
docker compose -f docker-compose.local.yml up --build   # PostgreSQL + Qdrant
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
