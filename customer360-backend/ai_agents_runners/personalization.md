# Recommendation and Personalization Handlers

This document describes recommendation execution in the consolidated
`ai_agents_runners` master workflow and the API read path for its persisted
results. It is not a separate Dagster code location.

## Current status

The current implementation provides:

- ordered workflow selection from `cdp_agent_workflow`;
- API and cron trigger paths;
- active-agent and tenant filtering;
- execution of configured `ranking_recommendation` steps for active profiles;
- tag, semantic, and hybrid ranking over explicitly selected, tenant-owned
  content candidates;
- persisted recommendation run status and per-profile results;
- an API endpoint that reads the latest successful results for a profile.

Other model types are reported as unsupported and are not dispatched. The
compatibility `personalization_job` remains a scaffold; recommendation workflows
execute through the master workflow.

## Registry model: `cdp_ai_agents`

Agent definitions are global and addressed by the stable `agent_code` primary
key. A handler must load and honor:

- `model_type`
- `model_name`
- `status`
- `input_features`
- `hyperparameters`
- `schedule_definition`
- `required_variables`
- `system_instructions`
- `prompt_key`, `prompt_engine`, `instruction_version`, and `prompt_versions`

Only `status = 'ACTIVE'` agents can be selected by the master task. A model
name is metadata, not proof that a trained artifact is available; runtime
dispatch must use an implementation allow-list and fail explicitly when a
handler is unavailable.

The database supports `ranking_recommendation` as the model type used for
content/product ranking. The seeded recommendation agent is
`product_recommendation`; its current lifecycle status must still be checked
at runtime rather than assumed from seed data.

## Workflow model: `cdp_agent_workflow`

Workflow rows are tenant-owned and attach one registered agent to one
tenant-owned segment:

```text
sys_tenant
   |
   +-- cdp_segments (tenant_id, segment_id)
           |
           +-- cdp_agent_workflow
                   |
                   +-- cdp_ai_agents.agent_code
```

The workflow table stores:

- `execution_order` — positive, unique queue position per tenant/segment;
- `is_active` — whether the step is eligible for execution;
- `schedule_definition` — optional five-field cron override;
- `configuration` — JSONB object for segment-specific parameters;
- `candidate_content_item_ids` — bounded UUID array;
- audit timestamps.

The master task executes selection in ascending `execution_order`. It does not
use inactive workflow rows or inactive agents.

## Candidate content contract

Candidate IDs refer to `cdp_content_items`, which are independently scoped by
`tenant_id`. The table stores:

- `item_type`: `news`, `video`, `product`, or `article`;
- `title`, summary, image, and CTA metadata;
- `segment_tags`;
- `status_code`;
- publication and audit timestamps.

Candidate content is valid only for an agent whose `model_type` is
`ranking_recommendation`. This is enforced by the API repository as well as by
the frontend workflow editor. The database trigger validates that every
candidate exists in the same tenant and that candidate IDs are distinct.

The runner requires a non-empty candidate array for a ranking step. It does not
copy the entire catalog into the workflow row or search the whole catalog during
execution.

For non-empty candidate lists, the ranking handler requires
`input_data.domain` and accepts `input_data.segmentation_tags` (default `[]`).
It considers only active content in the workflow tenant whose domain is `all`
or matches the profile domain. `configuration.strategy` selects tag-only,
semantic, or hybrid ranking; semantic and hybrid strategies use the configured
Docs embedding provider at 384 or 768 dimensions and profile context built from
domain and segmentation tags, not profile PII. `configuration.top_k` (or
the `limit` fallback) defaults
to 8 and must be a positive integer no greater than 100. Results include a
one-based rank, strategy scores, matched tags, reason, and content display/CTA
fields. The score is strategy-dependent and is not a calibrated relevance
probability.

Content vectors store canonical text, provider/model, contract version, and
generation time. Content edits invalidate vectors; migration
`008_content_embedding_contract.sql` preserves legacy 384- and 768-dimensional
vectors and clears other sizes. Linked product rows have matching vector columns reserved
for future direct product ranking; current workflows rank linked content items.

The runner writes each run to `cdp_profile_recommendation_runs` and its ranked
items to `cdp_profile_recommendations`. The API endpoint reads persisted
results; it does not run ranking when the request arrives.

If ranking returns no candidates, the step still fails rather than weakening
its filters or inventing a fallback. The error distinguishes missing
tenant-owned candidates, inactive content, a profile-domain mismatch, missing
model embeddings, and scores below `minimum_score`. It reports counts at each
filter stage, domain, strategy, and threshold; the runner adds the tenant,
segment, agent, and profile identifiers. Diagnosis reads only the selected IDs
in the same tenant and runs only on the failure path.

## Profile recommendation read API

The Customer 360 API implements:

```http
GET /api/v1/content-items/recommended?master_profile_id=<uuid>&limit=8
```

`master_profile_id` is required. `limit` defaults to 8 and accepts values from
1 through 50. The authenticated tenant context is always used; callers cannot
select another tenant. An optional `segment_id` restricts the results to one
segment, and `item_type` filters to `news`, `video`, `product`, or `article`.

For each profile, the API reads the latest successful run for each active
segment the profile belongs to, then merges results and deduplicates content
items across segments, keeping the strongest recommendation. Returned rows must
still refer to active workflow/agent records and active content matching the
profile domain. A missing or inactive profile returns `404`; a valid profile
with no eligible persisted results receives an empty list. Results include the
content fields plus `matched_tags`, `segment_id`, `agent_code`, `rank`, `score`,
`semantic_score`, `tag_score`, `strategy`, `reason`, and `generated_at`.

The read path is Redis cache lookup, then a persisted-results query on a miss.
Cache keys isolate tenants, profiles, and request filters. Content repository
mutations and successful Dagster recommendation runs invalidate the tenant's
recommendation cache; other changes become visible within `CACHE_TTL_SECONDS`.
Disable caching with `CACHE_ENABLED=false` for immediate reads while debugging.

The route is implemented in
[content_api.py](../../customer360-api/core/routers/content_api.py), and its
tenant-scoped query is in
[content_repository.py](../../customer360-api/core/repositories/content_repository.py).

## Profile and persona inputs

The implemented ranking handler currently consumes the profile's domain and
segmentation tags plus explicitly selected content candidates. Other
tenant-scoped data that could support future handlers includes:

- `cdp_master_profiles` — mastered profile identity, segmentation tags,
  analytics, persona summary, and profile-level derived fields;
- `cdp_customer_personas` — versioned profile/persona matches and score
  details;
- `cdp_persona_archetypes` — shared persona metadata and embeddings;
- `cdp_content_items` — eligible content/product candidates;
- active segment membership represented through the segment metadata and
  profile segmentation tags.

These future inputs are not currently part of the ranking contract. Any
handler that adds them must define their tenant-scoped read contract and apply
the relevant consent, suppression, inventory, campaign eligibility, and domain
constraints before returning a recommendation.

## Handler and persistence contract

The existing recommendation handler:

1. receives one ordered workflow step and trigger context;
2. validates ranking configuration and profile inputs;
3. evaluates only the step's selected candidates within the step tenant;
4. persists per-profile results under the Dagster run ID and records run status.

Future agent handlers must validate declared inputs/configuration, query only
the plan item's tenant data, reject unavailable or unsupported implementations
explicitly, persist only to an approved owning table/schema contract, and emit
clear execution outcomes.

No handler may:

- cross tenant boundaries;
- rewrite identity links or source events;
- treat a missing model as a successful default;
- select candidates outside the tenant's eligible set;
- send campaigns or notifications directly;
- overwrite another agent's owned output without an explicit contract.

## References

- [AI runner orchestration](./README.md)
- [Workflow schema](../../customer360-dao/src/leo_customer360_dao/models/agent_workflow.py)
- [Workflow migration](../../customer360-database/migrations/006_cdp_agent_workflow.sql)
- [Agent registry schema](../../customer360-database/database-schema.sql)
- [Content item model](../../customer360-dao/src/leo_customer360_dao/models/content.py)
