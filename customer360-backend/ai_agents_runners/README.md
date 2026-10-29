# AI Agent Runners

`ai_agents_runners` is the Dagster code location for tenant-scoped
orchestration of ordered AI-agent workflows. It reads the workflow and agent
metadata stored in PostgreSQL; it does not invent agent outputs or bypass the
Customer 360 API's tenant boundary.

## Current entry points

| Entry point | Purpose |
| --- | --- |
| `ai_agents_master_job` | Select active workflow steps for an API or cron trigger |
| `run_agent_workflow_master_op` | Execute the master selection task |
| `ai_agent_workflow_schedule_sensor` | Poll effective cron definitions and submit bounded runs |
| `personalization_job` | Compatibility scaffold retained from the removed personalization location |
| `POST /api/v1/segments/{segment_id}/workflow/run` | Submit a tenant/segment API-triggered run |

The master task executes `ranking_recommendation` steps for active profiles
and `eligibility_policy` rules-engine steps for active profiles matched to the
tenant-owned segment. Recommendations use only that workflow step's selected
tenant-owned content candidates. Eligibility evaluation stores per-channel
readiness in `communication_preferences.eligibility_policy` while preserving
the original consent fields. Other model handlers remain explicit scaffolds
and are reported as unsupported rather than returning placeholder success
results.

The eligibility policy is deterministic, not an LLM/legal-compliance verdict.
Only literal JSON `true` grants consent. Missing, false, or malformed values
deny that channel; active applicable suppressions deny it as well. Meta, Google
Ads, and TikTok require their own explicit `meta_ads_opt_in`,
`google_ads_opt_in`, or `tiktok_ads_opt_in` field; consent for email or another
provider is never reused. A `GLOBAL` suppression blocks every channel. A
campaign-scoped suppression is included only when the workflow configuration
or trigger event supplies that `campaign_id`. Add the active
`eligibility_policy` agent to a segment workflow before triggering that
segment's run.

To evaluate a segment, add the active `eligibility_policy` agent to that
tenant's segment workflow, then use the existing
`POST /api/v1/segments/{segment_id}/workflow/run` endpoint with the tenant ID.
The runner loads active profiles whose `segmentation_tags` contain the
segment's `segment_tag`, resolves active tenant/profile/identifier
suppressions (and campaign suppressions when a campaign ID is supplied), runs
the deterministic rules handler, and merges its versioned result under the
profile's `communication_preferences.eligibility_policy` key. Existing
`*_opt_in` source values are not modified. The run summary reports
`eligibility_profiles_updated`.

### Troubleshooting no personalization results

The master op logs a run/tenant/segment context before execution, then logs
profile-domain counts, each ranking step's strategy and candidate count, and
the profile batch that failed. For a successful run with zero rows, look for:

- `No active profiles matched segment` — the segment currently has no active
  matching profiles.
- `No selected recommendation candidates match profile domain` — the selected
  candidates are valid for the tenant but none match that profile's domain.
- `Ranking produced no candidates for profile` — inspect the logged tenant,
  active, domain-matching, rankable, strategy, and `minimum_score` counts.
- `AI recommendation workflow completed with no personalization results` —
  the run finished but wrote zero recommendation rows.

The final Dagster error log also includes the Dagster run ID, trigger, tenant,
and segment so the corresponding database run record and worker logs can be
located quickly.

`RankingRecommendationPipeline` also exposes structured state observability for
each execution (`pipeline.last_state` and an optional `state_observer` callback
receiving `RankingExecutionState`). Each snapshot records the execution phase
(`short_circuited`, `completed`, or `failed`), effective strategy and weights,
candidate/profile counts, SQL rows returned, ranked vs. domain-skipped profile
counts, reason-code distribution, per-profile `ProfileRankingOutcome` entries
with `CandidateDiagnostics`, and execution latency (`duration_ms`).

Ranking settings are resolved by merging `cdp_ai_agents.hyperparameters` with
each workflow step's `configuration` JSON override:

```json
{
  "strategy": "hybrid",
  "top_k": 8,
  "semantic_weight": 0.7,
  "tag_weight": 0.3,
  "minimum_score": 0.25,
  "semantic_query": "optional operator-provided context"
}
```

`strategy` accepts `tags` (default, preserving tag-only behavior), `semantic`,
or `hybrid`. Selected content embeddings are generated lazily from title,
summary, domain, type, and tags. Profile query text uses only its domain and
segment tags plus optional operator context; PII fields are not embedded.
Recommendation vectors support 384 or 768 dimensions, selected through the
existing provider/model dimension setting. Separate partial HNSW indexes cover
each size. The vector distance is computed only
over the bounded workflow candidate list; no global catalog search is performed.
Candidates are matched to each profile's domain at ranking time. If a valid
tenant-owned candidate set has no item for a particular profile domain, that
profile receives no recommendations while other profiles continue processing.
The configured provider, model, dimension, embedding contract version, and
canonical source text are stored with each content vector. Vectors are regenerated when the
provider/model changes or content title, summary, domain, type, or segment tags
change. The canonical database schema supports 384/768-dimensional vectors.
For older databases, the consolidated
[legacy upgrade](../../customer360-database/legacy-upgrade.sql) clears
unsupported sizes for lazy refresh.

For Gemini, set `DOCS_EMBEDDING_PROVIDER=gemini` and configure
`DOCS_GEMINI_API_KEY`, `DOCS_GEMINI_EMBEDDING_MODEL`, and
`DOCS_GEMINI_EMBEDDING_DIMENSIONS` to either 384 or 768. Provider/model/dimension
must remain the same between profile and content embeddings.
The local FastEmbed provider remains available for other consumers, but
recommendation workflows reject it; recommendations use OpenAI or Gemini only.

## Agent-type strategy layout

Each canonical `model_type` has a dedicated module under
`ai_agents_runners/agent_pipeline/agent_types/`. Its strategy owns that type's
result schema and processing implementation. `pipelines.py` is the shared
dispatcher; `contracts.py` contains only the common input and output envelope.
New model types should add their strategy and result model in their own module,
then register the strategy in `PIPELINE_HANDLERS`.

## Database contract

### `customer360.cdp_ai_agents`

The global agent registry uses `agent_code` as its primary key. Relevant
runtime fields are:

- `display_name`, `description`
- `model_type` — classification, regression, clustering,
  `ranking_recommendation`, forecasting, anomaly detection, uplift modeling,
  optimization, rules engine, or generative LLM
- `model_name`
- `status` — `ACTIVE`, `INACTIVE`, `TRAINING`, `DEPRECATED`, or `FAILED`
- `schedule_definition`
- `input_features` (`TEXT[]`)
- `hyperparameters` (`JSONB` object)
- prompt and instruction version fields

The runner selects only agents with `status = 'ACTIVE'`.

### `customer360.cdp_segments`

Segments are tenant-owned through `tenant_id`. The composite
`(tenant_id, segment_id)` relationship is the tenant boundary used by the
workflow table.

### `customer360.cdp_agent_workflow`

Each row assigns one global agent to one tenant-owned segment:

- `tenant_id`
- `segment_id`
- `agent_code`
- `execution_order`
- `is_active`
- optional `schedule_definition`
- `candidate_content_item_ids` (`UUID[]`)
- `configuration` (`JSONB` object)

The database enforces unique agent and execution-order values per
tenant/segment, validates candidate IDs against the same tenant, and applies
row-level security using `app.tenant_id`.

The effective schedule is:

1. `cdp_agent_workflow.schedule_definition`, when non-null;
2. otherwise `cdp_ai_agents.schedule_definition`.

### `customer360.cdp_content_items`

Candidate IDs refer to tenant-owned content rows with:

- `content_item_id`
- `tenant_id`
- `domain`
- `item_type` — `news`, `video`, `product`, or `article`
- `title`, `summary`, CTA fields
- `segment_tags`
- `status_code`

Candidate content is supported only for agents whose `model_type` is
`ranking_recommendation`. The API and repository reject candidate IDs for other
agent types. Content rows also store provider-versioned pgvector embeddings
used by semantic and hybrid ranking.

## API-triggered execution

The Customer 360 API validates the segment in the caller's tenant and submits
an asynchronous Dagster run:

```http
POST /api/v1/segments/{segment_id}/workflow/run
X-Tenant-Id: <tenant UUID>
Content-Type: application/json
```

```json
{
  "event": {
    "event_name": "profile.updated",
    "profile_id": "..."
  }
}
```

The API returns `202 Accepted` with a Dagster `run_id`. The tenant and segment
are sent in Dagster run configuration and tags; the event object is forwarded
as handler context. The run summary reports executed steps, profiles processed,
and recommendations written.

Saving the Agent Workplan with
`PUT /api/v1/segments/{segment_id}/workflow` also submits a master-workflow
run after the workflow transaction commits. The response remains the updated
step list and includes the submitted Dagster run ID in `X-Dagster-Run-Id`.
if submission fails, the API returns `503` and explicitly reports that the
workflow was saved but the run was not submitted.

## Cron-triggered execution

`ai_agent_workflow_schedule_sensor` polls every
`AI_AGENTS_WORKFLOW_POLL_SECONDS` seconds, defaulting to `60`. The sensor
submits a UTC timestamp to `ai_agents_master_job`. The master task:

1. loads active workflow rows joined to active agents;
2. resolves the effective schedule;
3. matches five-field cron expressions or supported cron macros;
4. selects only steps due in the current UTC minute;
5. orders the plan by tenant, segment, `execution_order`, and `agent_code`.

The database query is parameterized and applies tenant/segment filters for API
runs. A cron run may inspect all tenants because it is an internal scheduler
run; every selected row still carries its own tenant ID and remains isolated
in the execution plan.

## Compatibility job

The former `personalization` code location was removed. Its runnable scaffold
is now registered as `personalization_job` in the same Dagster code location.
It remains a compatibility job; segment recommendation workflows execute
through `ai_agents_master_job`.

## Development and tests

From `customer360-backend/`:

```bash
pip install -r ai_agents_runners/requirements.txt
dagster dev -w workspace.yaml
```

Focused runner tests:

```bash
PYTHONPATH=customer360-backend \
  python -m pytest ai_agents_runners/tests/test_runner.py -q
```

The runner tests cover tenant/segment API selection, ordered plans, and cron
due/not-due filtering.
