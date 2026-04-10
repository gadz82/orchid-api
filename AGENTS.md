# orchid-api — AI Context

## What This Package Is

**orchid-api** is the FastAPI server for the Orchid multi-agent AI framework. It imports `orchid` (the library) as a dependency and exposes HTTP endpoints for chat management, message handling, document uploads, and RAG sharing. It does NOT contain agent logic, graph building, or persistence implementations — those live in `orchid/`.

## Package Structure

```
orchid-api/
  orchid_api/
    main.py          FastAPI app + lifespan (graph build, storage init, tracing)
    settings.py      Pydantic BaseSettings + YAML overlay via _apply_yaml_config()
    context.py       AppContext dataclass (singleton, populated at startup)
    auth.py          Bearer token -> AuthContext via pluggable IdentityResolver (ADR-010)
    models.py        Pydantic response models
    tracing.py       LangSmith setup
    routers/
      chats.py       CRUD: create, list, delete chat sessions
      messages.py    Send messages + document upload (multipart/form-data)
      sharing.py     Promote chat RAG data to user-common scope
      legacy.py      Legacy single-shot /chat endpoint (JSON body)
  Dockerfile
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

2. **Identity resolution happens ONCE in `auth.py`.** The `get_auth_context` dependency resolves the Bearer token into `AuthContext`. No other code initiates OAuth flows (ADR-010).

3. **`AppContext` replaces globals.** All runtime state (runtime, graph, chat_repo, http_client, identity_resolver) lives in `context.py:app_ctx`. The `runtime` field is an `OrchidRuntime` instance that owns the reader, LLM service, and MCP client factory. Routers access it via `from ..context import app_ctx`.

4. **Routers are split by domain (SRP).** `chats.py` = CRUD, `messages.py` = send + upload, `sharing.py` = share, `legacy.py` = backward compat. New endpoints go in the appropriate router, never in `main.py`.

5. **No agent or framework code here.** No `BaseAgent` subclasses, no graph wiring, no RAG logic. Those belong in `orchid/` or consumer projects.

6. **Settings priority:** env vars > `orchid.yml` > hardcoded defaults. The `_YAML_TO_ENV` mapping in `settings.py` translates nested YAML keys to flat env vars.

7. **Don't persist augmented prompts.** Save the original user message to chat history, NOT the version with prepended file content or RAG context.

## Configuration (Settings)

All settings are env vars, optionally populated from `orchid.yml` via `ORCHID_CONFIG`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `LITELLM_MODEL` | `ollama/llama3.2` | LLM model identifier |
| `AGENTS_CONFIG_PATH` | `agents.yaml` | Path to agent YAML config |
| `VECTOR_BACKEND` | `qdrant` | Vector store backend |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant connection URL |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `CHAT_STORAGE_CLASS` | `orchid_ai.persistence.sqlite.SQLiteChatStorage` | Storage backend class |
| `CHAT_DB_DSN` | `~/.orchid/chats.db` | Database connection string |
| `DEV_AUTH_BYPASS` | `false` | Skip auth (dev only) |
| `IDENTITY_RESOLVER_CLASS` | `""` | Dotted path to IdentityResolver |
| `STARTUP_HOOK` | `""` | Async function called at startup |

## Running

```bash
# Standalone (no Docker):
pip install orchid-ai orchid-api
ORCHID_CONFIG=orchid.yml uvicorn orchid_api.main:app --port 8000

# Docker:
docker build -t orchid-api .
docker run -p 8000:8000 -v ./orchid.yml:/app/orchid.yml orchid-api
```

## Endpoints

| Method | Path | Router | Purpose |
|--------|------|--------|---------|
| POST | `/chats` | chats | Create chat session |
| GET | `/chats` | chats | List user's chats |
| DELETE | `/chats/{id}` | chats | Delete chat |
| GET | `/chats/{id}/messages` | messages | Load chat history |
| POST | `/chats/{id}/messages` | messages | Send message (multipart) |
| POST | `/chats/{id}/upload` | messages | Upload documents for chat RAG |
| POST | `/chats/{id}/share` | sharing | Promote chat RAG to user scope |
| POST | `/chat` | legacy | Single-shot (no persistence) |
| GET | `/health` | main | Readiness check |

## Code Style

- Python 3.11+, Ruff, line length 120
- `from __future__ import annotations` in every file
- Imports: `from orchid_ai.xxx` (never `from src.xxx`)
- No vendor-specific code — platform integrations belong in consumer projects

## Common Pitfalls

- `POST /chats/{id}/messages` uses `multipart/form-data`, not JSON. The legacy `POST /chat` uses JSON.
- CORS allows `localhost:3000` and `frontend:3000` — add new origins in `main.py` if needed.
- The `lifespan()` function builds the graph at startup. Changes to agent config require a restart.
- Embedding dimension mismatch (768 vs 1536 vs 3072) causes silent retrieval failures. Switching models requires re-indexing.
