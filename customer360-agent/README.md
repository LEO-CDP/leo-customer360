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
| Execution | `model_type`, `model_name`, `status`, `schedule_definition` | Identifies one of the 10 supported execution types: classification, regression, clustering, ranking/recommendation, forecasting, anomaly detection, uplift modeling, optimization, rules, or generative LLM; records its configured model, lifecycle state, and optional batch schedule. |
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
| `notification_planner` | `campaign.zns.instructions` (stable compatibility key) | `POST /plan/zalo` (ZNS adapter) |

The full prompt-backed, scoring, and rules-engine catalog is seeded by
[`customer360-database/init-cdp-ai-agents.sql`](../customer360-database/init-cdp-ai-agents.sql).
Deployment reseeding updates catalog metadata but preserves a prompt body and
history once a published revision exists.

`notification_planner` defines template-based planning for web, Zalo, WhatsApp,
chatbot, and caller-supported notification channels. Its input contract includes
`channel`, `candidate_templates`, and `delivery_constraints`. The existing
`/plan/zalo` endpoint remains a ZNS-only adapter; the registry entry does not
provide notification delivery or additional HTTP endpoints. The seed does not
rename or delete older registry rows; manage those explicitly per deployment.
Existing published prompts are preserved, so publish a reviewed unified prompt
revision through the prompt store before using it for other channels.

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
  `campaign.zns.instructions`) live in **`customer360-database/init-cdp-ai-agents.sql`**
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

The [use-case agents](#use-case-agent-endpoints) add eight more keys, seeded as
`INACTIVE` generative agents in the `service_prompt_seeds` block of
`init-cdp-ai-agents.sql` (`ON CONFLICT (agent_code) DO NOTHING`, so a prompt an
operator has already published is never overwritten on upgrade):

| Prompt key | Agent code | Route |
|------------|------------|-------|
| `analytics.sql.instructions` | `analytics_sql_generator` | `/analytics/sql` |
| `analytics.narrative.instructions` | `analytics_narrator` | `/analytics/narrative` |
| `support.answer.instructions` | `support_answerer` | `/support/answer` |
| `tickets.classify.instructions` | `ticket_classifier` | `/tickets/classify` |
| `onboarding.map_schema.instructions` | `schema_mapping_assistant` | `/onboarding/map-schema` |
| `marketing.draft_copy.instructions` | `marketing_copy_drafter` | `/marketing/draft-copy` |
| `marketing.orchestrate.instructions` | `campaign_orchestrator` | `/marketing/orchestrate/step` |
| `persona.decide.instructions` | `persona_decision_agent` | `/persona/decide` |

A stored body holds **static instruction text only**, with no `$placeholders` and
empty `required_variables`, like the other service prompts. The service appends
the data for each request in code (schema, rows, evidence, tool list), so
operators can change the rules without touching request handling. Two rules in
the text are tied to code: `analytics.sql.instructions` and
`support.answer.instructions` tell the model to reply `NOT_FOUND_ANSWERS` when it
cannot answer, and `persona.decide.instructions` falls back to the first allowed
action. Keep those if you edit them (tests check the seed for both). Changes are
picked up when the service refreshes its snapshot at startup.

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
- Initial catalog: `customer360-database/init-cdp-ai-agents.sql`
- Runtime implementation: `src/prompts/stores.py`

There is no in-code prompt fallback. An unpublished or invalid prompt key causes
a clean planning failure instead of silently using different instructions.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | liveness + configured model |
| POST | `/plan/email` | AI campaign plan from a segment brief + candidate content items |
| POST | `/plan/zalo` | AI selects one approved ZNS template + fills its params |
| POST | `/plan/segment` | Natural-language audience description to a QueryBuilder rule tree from the caller's attribute catalog (never SQL) |
| POST | `/products/generate-content` | Validated title and summary fields for imported product rows |
| POST | `/analytics/sql` | Question (plus earlier turns) to one checked SELECT; the caller runs it |
| POST | `/analytics/narrative` | Result rows to a plain-English summary, with a chart shape |
| POST | `/marketing/orchestrate/step` | Next validated action of a campaign workflow; the caller runs the tools |
| POST | `/marketing/draft-copy` | Brand-grounded subject, preview, body and caption, with safety checks |
| POST | `/support/answer` | Support answer from the supplied passages only |
| POST | `/tickets/classify` | Urgency, sentiment and routing team per ticket |
| POST | `/onboarding/map-schema` | Proposed field mappings for client onboarding |
| POST | `/persona/decide` | Perception, interpretation and next best action from behavioural evidence |

Typical responses are `200` for a plan, `401` for a missing or invalid bearer
token, `422` for an invalid request body, and `502` for provider/configuration
failures, missing prompts, invalid JSON/dates, or channel guardrail failures.
The client (`leo_customer360_agent.client`) turns HTTP, timeout, provider, and
response-parse failures into `AIProviderError`. It wraps `/plan/email`,
`/plan/zalo` and `/plan/segment` only; the use-case routes below are plain HTTP.

### Use-case agent endpoints

Eight **stateless** routes serve nine business use cases: support answers,
Text-to-SQL, campaign orchestration, copy drafting, analytics chat, ticket
classification, campaign performance narrative, schema mapping and the persona
decision loop. `customer360-api` resolves the tenant, owns the database, and runs
any SQL or tool. This service never connects to customer data, accepts no tenant,
and executes no SQL or tool. It turns the data it is sent into a checked answer.

They use the same bearer token as `/plan/*`. Instruction text comes from the
prompt store (see [Prompts](#prompts-postgres-prompt-store)), and each route uses
its own model when `LLM_USE_CASE_MODELS` sets one.

| Route | Request | Response | Notes |
|-------|---------|----------|-------|
| `/analytics/sql` | `question`; `schema` (table name to description, 1 to 200 entries); optional `history` (up to 5 `{question, sql}`); `k` (1 to 10) | `status` (`ok`, `not_found` or `refused`), `sql`, `error` | The caller runs the SQL in its own tenant-scoped, read-only session. A question that matches no table returns `not_found` without a model call. `refused` means the model wrote something that is not a single SELECT; the raw text comes back for logging and must never be run. Follow-up questions ("now split that by city") work by sending the earlier turns in `history`. |
| `/analytics/narrative` | `question`; `rows` (up to 200) | `narrative`, `ungrounded`, `chart` | `ungrounded` lists numbers in the text that are not in the rows (a safety net, tolerance of 0.5, not proof). `chart` is a shape for the UI (`bar`, `line`, `metric` or `table`), or null. No rows means no model call. |
| `/marketing/orchestrate/step` | `goal`; `tools` (name, description, `required`, `side_effect`); `steps` (earlier tool, args, observation, executed); `context` | `kind` (`tool`, `finish` or `error`), `tool`, `args`, `summary`, `requires_approval`, `reason` | The caller runs the tool, asks for approval when `requires_approval` is true, and sends the observation back as a new step. `error` means the reply was unusable (unknown tool, missing argument, bad JSON); ask again. Nothing is executed here. |
| `/tickets/classify` | `tickets` (1 to 50 of `{id, text}`); optional `teams` | `results`: `ticket_id`, `urgency`, `sentiment`, `team`, `needs_review`, `reason` | A label outside the allowed set is never guessed. It is left empty and flagged for review. A provider outage flags the ticket instead of failing the batch. |
| `/onboarding/map-schema` | `source_columns` (name, optional `samples`); `target_schema` (field to description); `include_samples` (default false); `max_targets` | `mappings`: `source`, `target`, `suggested`, `confidence`, `status` (`mapped`, `needs_review` or `unmapped`), `reason` | `mapped` needs confidence of at least 0.8, a real target field, and no other column claiming it. Sample values can be customer data, so they go to the model only when `include_samples` is true. |
| `/marketing/draft-copy` | `brief` (`objective`, `audience`); `guidelines`; optional length limits | `subject`, `preview_text`, `email_body`, `social_caption`, `checks`, `blocked` | `{{first_name}}` and `{{unsubscribe_url}}` are added if the model omits them. Unsafe HTML sets `blocked`. Length, guaranteed-outcome claims and hype words are flagged in `checks` for the reviewer, not rewritten. |
| `/support/answer` | `question`; `kb_passages`; optional `web_passages` and `allowed_domain` | `answer`, `found`, `source_kind` (`kb`, `web` or null), `sources` | Answers only from the passages sent. Web passages from any host other than `allowed_domain` or its subdomains are dropped. When nothing answers, `found` is false and the answer is a plain "not found" message. |
| `/persona/decide` | `evidence` (`persona`, `features`, `transactions` up to 10, `history` up to 5); optional `allowed_actions`, `suppressed` | `perception`, `interpretation`, `action`, `channel`, `rationale`, `cited`, `needs_review`, `reason` | The action must come from the allowed list (default: the platform's six `next_best_action` values, and the first is the fallback). Every cited reference, such as `features.churn_probability`, must exist in the evidence, or the result is flagged. Evidence that contains direct identifiers (email, phone, name, address and similar) is rejected with `422`. |

Status codes: `200` for every answer, including flagged ones (`needs_review`,
`not_found`, `refused`, `kind: error`); `401` for a missing or wrong token; `422`
for an invalid or oversized request (unknown fields are rejected); `502` when the
provider fails or returns something unusable for an agent with no safe default
(SQL, narrative, copy, mapping, support), or when the agent's prompt is not
published.

Example: ask for SQL, run it in the caller's own session, then summarise the rows.

```bash
curl -s -X POST http://localhost:8009/analytics/sql \
  -H "Authorization: Bearer $AGENT_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"question": "how many segments are there",
       "schema": {"cdp_segments": "cdp_segments: Saved audience groups. Columns: segment_id, segment_name, member_count"}}'
# {"status":"ok","sql":"SELECT COUNT(*) AS n FROM cdp_segments","error":null}

curl -s -X POST http://localhost:8009/analytics/narrative \
  -H "Authorization: Bearer $AGENT_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"question": "how many segments are there", "rows": [{"n": 8}]}'
```

The caller owns these responsibilities: resolve the tenant and run SQL on a role
that cannot bypass row-level security (a superuser sees every tenant), run only
`status: ok` SQL, execute orchestrator tools and approvals, build the persona
evidence and the schema descriptions, and treat every `needs_review` result as a
decision for a person.

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
| `LLM_USE_CASE_MODELS` | No | JSON object giving a use-case route its own model, for example `{"sql": "openai/gpt-4o", "tickets": "openai/gpt-4o-mini"}`. A route without an entry uses `LLM_MODEL`. Allowed keys: `sql`, `narrative`, `support`, `tickets`, `schema_mapping`, `copy`, `orchestrator`, `persona`. An unknown key stops the service at startup, so a typo cannot silently fall back to the default. |

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
API and Dagster services use `http://agent:8009`. Dagster needs this URL when
product imports check agent readiness and generate content. The agent receives
its Postgres URL through `AGENT_DATABASE_URL`; the root `.env` supplies the
shared token to the API, Dagster, and agent services.

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

The use-case routes follow the same rules and add three:

- **Model SQL is untrusted.** `/analytics/sql` only proposes a single SELECT (writes,
  stacked statements and write keywords are refused). The caller must still run it
  on a read-only role that cannot bypass row-level security. A database superuser
  bypasses row-level security, so it would expose every tenant.
- **Direct identifiers are rejected, not forwarded.** `/persona/decide` returns
  `422` for evidence containing keys such as email, phone, name or address, and
  `/onboarding/map-schema` sends sample values to the model only on request.
- **Nothing here acts.** The orchestrator route returns an action for the caller
  to run, and marks data-changing tools `requires_approval`. Keep the approval and
  dry-run decision in the caller.

## Layout

```
src/
  app.py               FastAPI app + routes
  config.py            LLM (model/api_key/base_url) + DB settings
  db.py                SQLAlchemy engine for the prompt store
  models/              request/response models (use_cases.py: the eight use-case routes)
  ai_providers/        base (LiteLLM call), provider (State-pattern LLMProvider),
                       campaign_planner/  (base + email + zalo)
  prompts/             port, stores (PgPromptStore), get_store
  use_case_routes.py   the eight stateless use-case routes (provider_for picks the model)
  use_cases/           one module per agent, plus instructions (prompt-store lookup),
                       sql_guard (SELECT-only check), schema_docs, text_clean
tests/                 provider + planner + prompt-store + HTTP smoke tests, plus the
                       use-case agent, route and prompt-seed tests
```

The server modules under `src/` run directly with Uvicorn. The
`leo_customer360_agent` package is the intentionally small client distribution;
installing it does not install FastAPI or LiteLLM.

## Tests

```bash
pytest        # from customer360-agent/ (conftest.py puts src/ on sys.path)
```

The default run is hermetic: no live model and no database. The use-case tests
run against the exact prompt bodies in `customer360-database/init-cdp-ai-agents.sql`
(through an in-memory store), so the seed and the code cannot drift apart, which
means the tests need the whole repository checked out, not just this folder.

A few tests read a real database and are skipped unless `AGENT_TEST_DATABASE_URL`
is set (a SQLAlchemy URL such as `postgresql+psycopg2://user:pass@localhost:5432/customer360_agents_test`).
They refuse to run unless the database is named `customer360_agents_test`. Build
it from the schema files in `customer360-database` and seed it with
`customer360-seeding`. They create and drop a throwaway SELECT-only role and never
write business data.
