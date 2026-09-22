# Customer 360 AI Agent Service

`customer360-agent` is the standalone FastAPI service for AI-assisted campaign
planning. It owns prompt loading, prompt assembly, LLM calls, JSON parsing, and
channel-specific guardrails. It does not own campaign, segment, template, or
customer data and it does not write business records to Postgres.

`customer360-api` prepares a tenant-scoped, closed candidate list, calls this
service over HTTP, validates the returned schedule, and persists the result as a
campaign draft. The service can call Gemini, OpenAI, Anthropic, or an
OpenAI-compatible local runtime through LiteLLM.

## Service boundary and request flow

The provider and planner code has no campaign-database dependency, so it runs as
an independent service. `customer360-api` uses the packaged
`leo_customer360_agent.client` instead of importing planner code in-process.

```mermaid
sequenceDiagram
   participant API as customer360-api
   participant Agent as customer360-agent
   participant DB as Postgres cdp_ai_agents
   participant LLM as LiteLLM provider
   participant CRM as Campaign tables

   API->>API: Resolve tenant, active segment, approved candidates
   API->>Agent: POST /plan/email or /plan/zalo + bearer token
   Agent->>DB: Load published prompt snapshot at startup
   Agent->>Agent: Map request to channel brief and assemble prompt
   Agent->>LLM: Prompt plus segment context and closed candidate list
   LLM-->>Agent: JSON plan
   Agent->>Agent: Parse output and apply channel guardrails
   Agent-->>API: Typed plan, or 502 on unusable AI output
   API->>API: Re-check dates and selected IDs
   API->>CRM: Persist Draft/InReview campaign and ai_plan JSONB
```

The agent receives context and candidates, not database access. The caller
remains responsible for tenant isolation, approval state, final validation, and
persistence. An LLM response is a proposal, not an approval.

### Runtime steps

1. Startup best-effort loads published prompts into a process-local snapshot.
2. The request is authenticated, validated by Pydantic, and mapped to an email
  or ZNS planner brief.
3. The planner appends the brief and closed candidate list to the published
  channel prompt.
4. `LLMProvider` builds provider state from service settings plus optional
  per-request overrides and invokes `litellm.completion()`.
5. The response must be a JSON object with ISO dates. ZNS additionally verifies
  the selected template is on the candidate list and that every required
  parameter is non-empty.
6. The caller validates and persists the result, or aborts without a partial
  draft.

# AI Agent Registry

`customer360.cdp_ai_agents` is the shared registry for Customer 360 scoring
models, rules engines, and task-oriented LLM agents. The schema is owned by
[`customer360-database/database-schema.sql`](../customer360-database/database-schema.sql).
This service uses it as a versioned prompt store; it does not write campaign or
customer records.

| Column group | Columns | Purpose |
|--------------|---------|---------|
| Identity | `agent_code`, `display_name`, `description` | Stable agent identifier and operator-facing metadata. `agent_code` is the primary key. |
| Execution | `model_type`, `model_name`, `status`, `schedule_definition` | Identifies a classification, regression, rules, clustering, or `generative_llm` agent; records its configured model, lifecycle state, and optional batch schedule. |
| Inputs and tuning | `input_features`, `hyperparameters` | Declares expected source features/context and JSONB model settings such as `temperature` and `max_output_tokens`. |
| Prompt identity | `prompt_key`, `prompt_engine` | Gives a prompt-backed agent a stable lookup key and rendering engine. The current agent service supports `none`, which performs `$name` substitution while preserving literal JSON braces. |
| Current prompt | `system_instructions`, `required_variables`, `instruction_version` | Materializes the active instruction body, declared input variables, and published revision. |
| Prompt history | `prompt_versions` | Append-only JSONB array of `{version, body, required_vars, created_at, created_by, note}` entries. |
| Audit | `instruction_updated_by`, `instruction_note`, `created_at`, `updated_at` | Records prompt provenance and row timestamps. |

The database enforces valid `model_type` and `status` values, non-empty prompt
keys/engines, object-shaped `hyperparameters`, array-shaped `prompt_versions`,
and a complete prompt state whenever `prompt_key` is set. It indexes `status`
and non-null `model_name`, and enforces unique non-null `prompt_key` values.

### Agent-Service Read Contract

At startup, `PgPromptStore.refresh()` reads rows only when both `prompt_key` and
`system_instructions` are present. It validates that the active instruction
matches `instruction_version` in `prompt_versions`, that declared `$variables`
are listed in `required_variables`, and that the rendering engine is supported.
Invalid rows are excluded from the process-local snapshot; a later request for
that key fails cleanly rather than using an in-code prompt fallback.

The current planning endpoints resolve these seeded prompt keys:

| Agent code | Prompt key | Endpoint |
|------------|------------|----------|
| `campaign_planner` | `campaign.plan.instructions` | `POST /plan/email` |
| `zns_campaign_planner` | `campaign.zns.instructions` | `POST /plan/zalo` |

The full prompt-backed catalog is seeded by
[`customer360-database/init-prompt-store-seed.sql`](../customer360-database/init-prompt-store-seed.sql);
score and rules-engine catalog records are seeded by
[`customer360-database/init-core-database.sql`](../customer360-database/init-core-database.sql).
Deployment reseeding updates catalog metadata but preserves a prompt body and
history once a published revision exists.

`model_name` records the catalog model assigned to an agent, for example
`openai/gpt-5.6-luna`. This service currently chooses the actual LiteLLM model
from `LLM_MODEL` or the request-level `model` override; it does not derive the
provider model from `cdp_ai_agents.model_name`. Keep the values aligned through
deployment configuration until model selection is explicitly moved into the
registry.

## Data contracts

Both planning endpoints accept the shared fields below:

| Field | Type | Meaning |
|-------|------|---------|
| `segment_context` | object | Caller-provided segment context. The current API passes `segment_id` and `segment_name`; avoid unnecessary PII. |
| `objective` | string | Marketer's goal. |
| `budget_time_constraints` | string or null | Optional schedule or budget constraints. |
| `model` | string or null | Optional per-request LiteLLM model override. |
| `extra_config` | object or null | Optional LiteLLM parameters, merged over `LLM_EXTRA_CONFIG`. |

### Email planning

`POST /plan/email` receives `candidate_content_items`, normally reduced by
`customer360-api` to active, tenant-owned objects such as:

```json
{
  "segment_context": {"segment_id": "seg-1", "segment_name": "Dormant customers"},
  "objective": "Reactivate lapsed customers",
  "budget_time_constraints": "Two touches in October",
  "candidate_content_items": [
    {"content_item_id": "c1", "title": "We miss you", "item_type": "email"},
    {"content_item_id": "c2", "title": "20% back", "item_type": "email"}
  ]
}
```

The response contains `name`, `objective`, `strategy_summary`, `action_plan`,
`start_date`, `end_date`, and `content_item_ids`. An empty ID list is valid when
no candidate is suitable; invented IDs are not.

### Zalo ZNS planning

`POST /plan/zalo` receives only approved ZNS candidates:

```json
{
  "segment_context": {"segment_id": "seg-1", "segment_name": "Cart abandoners"},
  "objective": "Recover abandoned carts",
  "candidate_templates": [
    {"template_id": "tpl-promo", "name": "Promo", "params": ["offer"]}
  ]
}
```

The response contains the shared plan fields plus:

```json
{
  "template_id": "tpl-promo",
  "template_data": {"offer": "15% off, today only"}
}
```

The planner rejects an off-list template or missing required parameter. It
selects an approved template; it does not author free ZNS message content.

### Persistence handoff

The agent returns a plan only. `customer360-api` maps it into a draft with:

- `crm_campaign`: name, objective, segment, channel/template, strategy summary,
  schedule, `status = Draft`, and `approval_status = InReview`.
- `crm_campaign.ai_plan` JSONB: the complete generated plan, including selected
  content IDs or ZNS `template_data`.
- `crm_campaign_content_items`: ordered links for selected email content items.

For ZNS, `template_data` is retained for the notification engine to use at send
time. The agent itself never writes these rows.

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

Check liveness:

```bash
curl http://localhost:8009/health
```

Call email planning directly (include the header when `AGENT_API_TOKEN` is
configured):

```bash
curl -X POST http://localhost:8009/plan/email \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer the-same-token-as-the-agent' \
  -d '{
    "segment_context": {"segment_id": "seg-1", "segment_name": "Dormant customers"},
    "objective": "Reactivate lapsed customers",
    "candidate_content_items": [
      {"content_item_id": "content-1", "title": "We miss you", "item_type": "email"}
    ]
  }'
```

The packaged Python client exposes the same contract to callers such as
`customer360-api`:

```python
from leo_customer360_agent import CampaignPlanBrief, generate_campaign_plan

brief = CampaignPlanBrief(
    segment_context={"segment_id": "seg-1", "segment_name": "Dormant customers"},
    objective="Reactivate lapsed customers",
)
plan = generate_campaign_plan(
    brief,
    [{"content_item_id": "content-1", "title": "We miss you", "item_type": "email"}],
)
```

Install the client with `pip install -e .` from this directory, or use the
repository's `customer360-api` startup/install scripts.

## Prompts (Postgres prompt store)

Prompt bodies are **addressable, versioned data in Postgres**, not text baked
into the planner. `AGENT_DATABASE_URL` is required for planning.

- `src/prompts/` — the store: `port.py` (`PromptTemplate` with `$name`
  substitution that preserves literal `{ }` braces; `PromptStore` protocol),
  `stores.py` (`PgPromptStore` + `validate` rail), `__init__.py` (`get_store()`).
  `src/db.py` — the SQLAlchemy engine (sets `search_path` to `AGENT_DB_SCHEMA`,
  default `customer360`).
- **Schema** — `customer360.cdp_ai_agents` is the single registry for model
  configuration, current instructions, and append-only `prompt_versions` JSONB
  history. DDL in **`customer360-database/database-schema.sql`**.
- **Seed** — the default prompt bodies (`campaign.plan.instructions`,
  `campaign.zns.instructions`) live in **`customer360-database/init-prompt-store-seed.sql`**
  (idempotent). Both apply on DB init and via `deployments/postgres/run-sql.sh`.
- **Edit at runtime** — `PgPromptStore.publish()` appends a revision to the
  agent row and updates `system_instructions`/`instruction_version`;
  `rollback(key, version)` selects an existing JSONB revision and `history()`
  reads the same row-level audit log.
- **Read path** — `get()` reads a process-local snapshot loaded by `refresh()`
  (called once at startup); no in-code body fallback, so an unpublished key or an
  unreachable DB raises and the `/plan` call returns 502.

The planner reads a prompt via `get_store().get(key).render()`.

The two planning keys are:

| Prompt key | Planner |
|------------|---------|
| `campaign.plan.instructions` | Email/content campaign planning |
| `campaign.zns.instructions` | Zalo ZNS campaign planning |

They point to rows in `customer360.cdp_ai_agents`. The fields used by this
service are `prompt_key`, `system_instructions`, `required_variables`,
`instruction_version`, `prompt_versions`, and `prompt_engine`. The current
engine is `none`: `$name` substitution is supported while literal JSON braces
remain unchanged. `prompt_versions` is append-only JSONB history; the active
revision is materialized in `system_instructions` and `instruction_version`.

`PgPromptStore.refresh()` validates each row and replaces the local snapshot.
`publish()`, `rollback()`, and `history()` operate on the same row and revision
log. Schema and seed ownership stays in `customer360-database`:

- DDL: `customer360-database/database-schema.sql`
- Initial catalog: `customer360-database/init-prompt-store-seed.sql`
- Runtime implementation: `src/prompts/stores.py`

There is no in-code prompt fallback. An unpublished or invalid prompt key causes
a clean planning failure instead of silently using different instructions.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | liveness + configured model |
| POST | `/plan/email` | AI campaign plan from a segment brief + candidate content items |
| POST | `/plan/zalo` | AI selects one approved ZNS template + fills its params |

Typical responses are `200` for a plan, `401` for a missing or invalid bearer
token, `422` for an invalid request body, and `502` for provider/configuration
failures, missing prompts, invalid JSON/dates, or channel guardrail failures.
The client (`leo_customer360_agent.client`) turns HTTP, timeout, provider, and
response-parse failures into `AIProviderError`.

## Configuration

Copy `.env.example` to `.env` for local development. Settings are loaded by
`src/config.py`; aliases such as `AI_MODEL`, `DATABASE_URL`, and
`OPENAI_BASE_URL` are accepted for compatibility.

### Service settings

| Variable | Required | Description |
|----------|----------|-------------|
| `AGENT_API_TOKEN` | Non-local: yes | Shared bearer token. Blank disables planning auth and logs a startup warning. |
| `AGENT_DATABASE_URL` | Real planning: yes | SQLAlchemy URL for the Postgres prompt store. `DATABASE_URL` is an alias. |
| `AGENT_DB_SCHEMA` | No | Prompt schema; defaults to `customer360`. |
| `AGENT_ROOT_PATH` | Behind path proxy: yes | Set to `/agent` when exposed under `/agent`; otherwise blank. |
| `AGENT_API_VERSION` | No | FastAPI version; defaults to `0.1.0`. |

### LLM settings

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_MODEL` | Yes | Provider-prefixed model, such as `gemini/gemini-2.5-flash`, `openai/gpt-5.6-luna`, or `anthropic/claude-3-5-sonnet-latest`. |
| `LLM_API_KEY` | Hosted: yes | Provider credential. `AI_API_KEY` is an alias. |
| `LLM_BASE_URL` | Local/OpenAI-compatible: yes | Custom OpenAI-compatible endpoint. `OPENAI_BASE_URL` is an alias. |
| `LLM_EXTRA_CONFIG` | No | JSON object forwarded to LiteLLM, for example `{"max_tokens": 1200, "timeout": 60}`. |

Provider examples:

| Runtime | Configuration |
|---------|-------------|
| Gemini | `LLM_MODEL=gemini/gemini-2.5-flash` and `LLM_API_KEY=...` |
| OpenAI | `LLM_MODEL=openai/gpt-5.6-luna` and `LLM_API_KEY=...` |
| Anthropic | `LLM_MODEL=anthropic/claude-3-5-sonnet-latest` and `LLM_API_KEY=...` |
| Ollama/vLLM/LM Studio | `LLM_MODEL=openai/llama3.1` and `LLM_BASE_URL=http://host.docker.internal:11434/v1`; key may be blank. |

`model` and `extra_config` can be supplied per request, but credentials remain
service configuration. Do not send secrets in the request body.

### Connecting `customer360-api`

The API client reads these DAO settings:

```dotenv
AGENT_SERVICE_URL=http://localhost:8009
AGENT_API_TOKEN=the-same-token-as-the-agent
```

It sends `Authorization: Bearer <token>` and calls `/plan/email` or `/plan/zalo`.
In the repository Docker Compose deployment the service is named `agent`, so the
API uses `http://agent:8009`; the agent receives its Postgres URL through
`AGENT_DATABASE_URL` and both services receive the shared token from the root
`.env`.

## Security

`/plan/*` is gated by a shared **bearer token** (`AGENT_API_TOKEN`) — the agent is
internal, called only by `customer360-api`. The API sends
`Authorization: Bearer <token>`; the agent constant-time compares it and returns
`401` on mismatch. `/health` is always open (for probes). If `AGENT_API_TOKEN` is
blank, auth is disabled (a startup warning is logged) — set it in every non-local
env. `customer360-api` sends the SAME value via its `AGENT_API_TOKEN`
(`agent_api_token` in DAO config); in CD both get it from the `AGENT_API_TOKEN`
secret.

Keep candidate lists tenant-scoped and approved/active before calling the agent.
The agent cannot enforce database row-level security on opaque request objects.
Treat generated plans as drafts, validate dates and IDs again before activation,
and avoid unnecessary PII in segment context, prompts, logs, or candidates.

## Layout

```
src/
  app.py               FastAPI app + routes
  config.py            LLM (model/api_key/base_url) + DB settings
  db.py                SQLAlchemy engine for the prompt store
  models/              request/response models
  ai_providers/        base (LiteLLM call), provider (State-pattern LLMProvider),
                       campaign_planner/  (base + email + zalo)
  prompts/             port, stores (PgPromptStore), get_store
tests/                 provider + planner + prompt-store + HTTP smoke tests
```

The server modules under `src/` run directly with Uvicorn. The
`leo_customer360_agent` package is the intentionally small client distribution;
installing it does not install FastAPI or LiteLLM.

## Tests

```bash
pytest        # from customer360-agent/ (conftest.py puts src/ on sys.path)
```
