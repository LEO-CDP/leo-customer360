# `personalization` — Recommendation and Personalization Agents

The `personalization` code location is the Dagster execution surface for
profile-level recommendations and next-best-action decisions. The broader
agentic platform supports many `cdp_ai_agents` types, but this service is
scoped to personalization/recommendation behavior:

```text
Customer 360 profile
  +-- persona state and persona_summary
  +-- persona_embedding lookalike context
  +-- scores and value/risk signals
  +-- active segments
  +-- recent behavior and intent
  +-- eligible candidate items
             |
             v
  recommendation/personalization agents
             |
             v
  ranked recommendations, next-best action, offer, content, or channel choice
```

This service consumes scoring and persona outputs. It must not recompute
identity links, overwrite source events, or silently replace the scoring
agents' profile values.

## Current implementation status

The Dagster job is currently a runnable placeholder. It logs start/end and
sleeps for `PERSONALIZATION_PLACEHOLDER_SLEEP_SECONDS` seconds (default `2`);
it does not yet load agents, query profiles, perform retrieval/ranking, or
persist recommendations.

- Current job name: `scoring_job`
- Op: `personalization_placeholder_op`
- Planned job name: `personalization_job`
- Entry point: [`dagster_defs.py`](dagster_defs.py)

The current `scoring_job` name is retained by the scaffold for workspace
compatibility and should be renamed when the real personalization
implementation is introduced.

## Agent registry: `cdp_ai_agents`

[`CdpAiAgent`](../../customer360-dao/src/leo_customer360_dao/models/identity.py)
is the shared registry for model, rules-engine, and task-agent definitions.
Personalization should resolve active agents by `agent_code`, then honor the
registry metadata:

- `model_type`, `model_name`, and `status`
- `schedule_definition`
- declared `input_features`
- `hyperparameters`
- `required_variables`
- prompt/instruction versions where the agent is prompt-backed

The primary recommendation agent is:

| Agent code | Purpose | Key inputs |
| --- | --- | --- |
| `recommendation` | Rank eligible products, services, content, or experiences | `persona_summary`, `segmentation_tags`, `preferred_channel`, `historical_clv`, `last_activity_at` |

The seeded recommendation contract uses `recommendation-ranking-v2`, a
top-k limit, and a diversity weight. The implementation must rank only
caller-supplied eligible candidates and must never invent product, content, or
offer identifiers.

Supporting personalization-oriented agents include `next_best_action`,
`journey_optimization`, `content_intelligence`, `channel_optimization`, and
`offer_optimization`. Scoring agents such as `lead_scoring`, `churn_scoring`,
and `clv_scoring` belong to the
[`scoring` code location](../scoring/README.md); personalization consumes
their outputs instead of running them.

## Workflow registry: `cdp_agent_workflow`

[`CdpAgentWorkflow`](../../customer360-dao/src/leo_customer360_dao/models/agent_workflow.py)
assigns registered agents to a tenant-owned segment. A workflow step contains:

- `tenant_id` and `segment_id`
- `agent_code`
- positive `execution_order`
- `is_active`
- optional `schedule_definition`
- JSONB `configuration`
- bounded `candidate_content_item_ids`

The personalization job may use active workflow rows to select the
recommendation sequence for a segment. It must:

1. Enforce tenant isolation for every workflow and profile query.
2. Preserve `execution_order`.
3. Skip inactive agents and reject agents outside the personalization scope.
4. Apply workflow configuration without allowing candidate IDs outside the
   caller-provided eligible set.
5. Keep planning/ranking separate from campaign sending and channel delivery.

Workflow payload validation, including schedule and candidate-ID limits, is
defined in
[`schemas/agent_workflow.py`](../../customer360-dao/src/leo_customer360_dao/schemas/agent_workflow.py).

## Persona state and `persona_embedding`

The persona model represents a customer's current state as a multidimensional
estimate rather than a permanent label. The
[`persona_as_a_vector_marketing_8.0.pdf`](../../docs/research-papers/persona_as_a_vector_marketing_8.0.pdf)
research distinguishes observable signals, inferred state, uncertainty,
temporal change, and semantic/behavioral vector representations. A
recommendation must therefore treat persona output as evidence with
confidence—not as an unquestionable fact.

The database stores persona data in separate layers:

- `cdp_master_profiles.persona_name` and `persona_summary` provide compact
  profile-level persona context.
- `cdp_customer_personas` stores the versioned master-profile-to-archetype
  match, component scores, risk/value outputs, and next-best-action context.
- `cdp_persona_archetypes.persona_embedding` stores the shared
  768-dimensional `pgvector` archetype centroid.
- `cdp_master_profiles.analytics` is the flexible JSONB location for
  additional derived analytics when a dedicated column is not appropriate.

`persona_embedding` is not a duplicated per-profile recommendation vector.
Personalization can use cosine similarity against the shared archetype
centroid to retrieve or rank similar profiles/archetypes, then combine that
context with current behavior, eligibility, consent, inventory, and campaign
constraints.

Reference design and persistence details are documented in
[`persona-resolution-paper.md`](../../docs/research-papers/persona-resolution-paper.md)
and [`database-schema.sql`](../../customer360-database/database-schema.sql).

## Personalization output contract

The future implementation should:

1. Load active recommendation/personalization agents and their versions.
2. Select tenant-scoped profiles and active workflow steps in bounded batches.
3. Build a candidate set from approved, eligible products, content, services, or
   offers.
4. Combine declared profile features, persona state, embeddings, recent
   behavior, scores, consent, suppression, inventory, and business rules.
5. Return ranked candidates, explanations/evidence, confidence, effective
   time, and the selected next action when applicable.
6. Persist only validated recommendation state in the owning domain tables or
   approved JSONB fields; do not write directly to provider delivery ledgers.
7. Emit metrics for processed, skipped, blocked, failed, and successful
   profile decisions.

Recommendations must respect tenant boundaries, consent and suppression
policies, candidate-set closure, and uncertainty. A missing feature or
candidate must produce an explicit blocked/insufficient-evidence result, not a
fabricated recommendation.

## Dependencies and local development

The service uses the dependencies declared in
[`requirements.txt`](requirements.txt). From `customer360-backend/`, load the
workspace with:

```bash
dagster dev -w workspace.yaml
```