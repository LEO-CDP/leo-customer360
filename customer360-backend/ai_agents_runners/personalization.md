# Recommendation and Personalization Handlers

This document describes the recommendation/personalization scope that will be
implemented behind the consolidated `ai_agents_runners` master workflow. It is
not a separate Dagster code location.

## Current status

The current implementation provides:

- ordered workflow selection from `cdp_agent_workflow`;
- API and cron trigger paths;
- active-agent and tenant filtering;
- ranking of explicit, tenant-scoped active content candidates by overlap with
  supplied profile segmentation tags;
- an explicit execution plan for future handlers.

The ranking handler does not perform learned-model inference, rank profiles,
write recommendation results, or persist next-best actions. The master
workflow currently selects steps but does not yet dispatch agent handlers.
The compatibility `personalization_job` only preserves the former scaffold
entry point.

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

An empty candidate array means no explicit candidate restriction. It does not
mean that the runner should silently copy the entire catalog into the
workflow row or query the whole catalog during execution. The ranking handler
returns an empty `ranked_items` list when no candidate IDs are provided.

For non-empty candidate lists, the ranking handler requires
`input_data.domain` and accepts `input_data.segmentation_tags` (default `[]`).
It selects candidates only when their `tenant_id` matches the workflow tenant,
`status_code = 1`, and their domain is `all` or matches the profile domain.
Every requested candidate must pass those filters; otherwise the handler
fails rather than silently dropping a candidate. Results are ordered by the
number of overlapping segment tags, newest publication timestamp, then
content-item UUID for deterministic ties. The optional `configuration.limit`
defaults to 8 and must be a positive integer.

Each returned item includes `item_id`, one-based `rank`, overlap `score`,
`matched_tags`, a reason, and the content display/CTA fields. The score is a
tag-overlap count, not a calibrated relevance probability. The handler does
not persist results.

The separate API endpoint
`GET /api/v1/content-items/recommended` currently ranks content for a master
profile by overlap between `cdp_content_items.segment_tags` and
`cdp_master_profiles.segmentation_tags`. That endpoint is not a persisted
workflow result and should not be confused with future agent-run output.

## Profile and persona inputs

Future recommendation handlers may consume tenant-scoped data from:

- `cdp_master_profiles` — mastered profile identity, segmentation tags,
  analytics, persona summary, and profile-level derived fields;
- `cdp_customer_personas` — versioned profile/persona matches and score
  details;
- `cdp_persona_archetypes` — shared persona metadata and embeddings;
- `cdp_content_items` — eligible content/product candidates;
- active segment membership represented through the segment metadata and
  profile segmentation tags.

These inputs are evidence, not permission. Handlers must also apply consent,
suppression, inventory, campaign eligibility, and domain constraints before
returning a recommendation.

## Handler contract

An agent-specific handler added behind the master task should:

1. receive one ordered workflow plan item and its trigger event;
2. validate the agent's declared inputs and configuration;
3. query only the plan item's tenant data;
4. reject missing features, unavailable models, invalid candidates, and
   unsupported model types explicitly;
5. produce ranked candidates or a structured blocked/failure result;
6. persist only to an approved owning table or schema contract;
7. emit processed, skipped, blocked, failed, and completed metrics.

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
