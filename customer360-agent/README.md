# Customer 360 — AI Agent Service

Standalone FastAPI service that owns the **AI agent / campaign-planning logic**
extracted from `customer360-api`. It is **provider-agnostic** and works with a
hosted provider or a **local LLM**, chosen entirely by config.

## Why a separate service

The provider abstraction (`ai_providers/`) has no database dependency, so it
runs on its own. `customer360-api` calls this service over HTTP via the client
library this package ships — `leo_customer360_agent.client` (installed like the
DAO; see `pyproject.toml`) — instead of importing the planner in-process.

## Providers (config-driven)

All providers run through one SDK — **[LiteLLM](https://github.com/BerriAI/litellm)**,
whose unified `completion()` speaks every provider's wire format. Config is just
**three values** — the model string carries the provider:

| `LLM_MODEL` | Provider | Also set |
|-------------|----------|----------|
| `gemini/<model>` | Google Gemini (hosted) | `LLM_API_KEY` |
| `openai/<model>` | OpenAI (hosted) | `LLM_API_KEY` |
| `anthropic/<model>` | Anthropic Claude (hosted) | `LLM_API_KEY` |
| `openai/<name>` | Any OpenAI-compatible local LLM (Ollama, vLLM, LM Studio) | `LLM_BASE_URL` (key optional) |

### One generic provider (State pattern)

A single `LLMProvider` (`ai_providers/provider.py`, the Context) delegates to a
`ProviderState` (the State = config: `model`, `api_key`, `api_base`,
`extra_config`). `build_state()` fills defaults from config; `model` and
`extra_config` can be overridden **per request** on `/plan/*`. `extra_config` is
a free dict forwarded verbatim to `litellm.completion` (e.g. `max_tokens`,
`timeout`), merged over the service-wide `LLM_EXTRA_CONFIG`.

## Run

```bash
# local
pip install -r requirements.txt
uvicorn app:app --app-dir src --port 8009

# docker (wired into the repo docker-compose.yml as service `agent`)
docker compose up agent
```

## Prompts (Postgres prompt store)

Prompt bodies are **addressable, versioned data in Postgres**, not text baked
into the planner. `AGENT_DATABASE_URL` is required for planning.

- `src/prompts/` — the store: `port.py` (`PromptTemplate` with `$name`
  substitution that preserves literal `{ }` braces; `PromptStore` protocol),
  `stores.py` (`PgPromptStore` + `validate` rail), `__init__.py` (`get_store()`).
  `src/db.py` — the SQLAlchemy engine (sets `search_path` to `AGENT_DB_SCHEMA`,
  default `customer360`).
- **Schema** — `customer360.prompt_template` + `customer360.prompt_version`
  (append-only, versioned). DDL in **`customer360-database/database-schema.sql`**.
- **Seed** — the default prompt bodies (`campaign.plan.instructions`,
  `campaign.zns.instructions`) live in **`customer360-database/init-prompt-store-seed.sql`**
  (idempotent). Both apply on DB init and via `deployments/postgres/run-sql.sh`.
- **Edit at runtime** — `PgPromptStore.publish()` appends a version + moves the
  pointer; `rollback(key, version)`; `history()` is the audit log.
- **Read path** — `get()` reads a process-local snapshot loaded by `refresh()`
  (called once at startup); no in-code body fallback, so an unpublished key or an
  unreachable DB raises and the `/plan` call returns 502.

The planner reads a prompt via `get_store().get(key).render()`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | liveness + configured model |
| POST | `/plan/campaign` | AI campaign plan from a segment brief + candidate content items |
| POST | `/plan/zalo` | AI selects one approved ZNS template + fills its params |

Provider failures map to HTTP `502`; the client (`leo_customer360_agent.client`)
turns that back into `AIProviderError`.

## Security

`/plan/*` is gated by a shared **bearer token** (`AGENT_API_TOKEN`) — the agent is
internal, called only by `customer360-api`. The API sends
`Authorization: Bearer <token>`; the agent constant-time compares it and returns
`401` on mismatch. `/health` is always open (for probes). If `AGENT_API_TOKEN` is
blank, auth is disabled (a startup warning is logged) — set it in every non-local
env. `customer360-api` sends the SAME value via its `AGENT_API_TOKEN`
(`agent_api_token` in DAO config); in CD both get it from the `AGENT_API_TOKEN`
secret.

## Layout

```
src/
  app.py               FastAPI app + routes
  config.py            LLM (model/api_key/base_url) + DB settings
  db.py                SQLAlchemy engine for the prompt store
  schemas.py           request/response models
  ai_providers/        base (LiteLLM call), provider (State-pattern LLMProvider),
                       campaign_planner/  (base + email + zalo)
  prompts/             port, stores (PgPromptStore), get_store
tests/                 provider + planner + prompt-store + HTTP smoke tests
```

## Tests

```bash
pytest        # from customer360-agent/ (conftest.py puts src/ on sys.path)
```
