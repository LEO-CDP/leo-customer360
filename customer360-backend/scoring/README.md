# `scoring` — Profile Scoring Agents

The `scoring` code location is the Dagster execution surface for Customer 360
AI agents that compute profile-level scores. The broader agentic platform
supports many agent types—identity, persona, recommendation, activation,
planning, and orchestration—but this service is intentionally scoped to
scoring-oriented agents and their outputs on `cdp_master_profiles`.

The target responsibility is:

```text
resolved customer profiles + approved scoring inputs
                         |
                         v
                 scoring agents
                         |
                         v
       profile probabilities, tiers, values, and scores
                         |
                         v
                   Customer 360
```

## Current implementation status

The Dagster job is currently a runnable scaffold. It logs start/end and sleeps
for `SCORING_PLACEHOLDER_SLEEP_SECONDS` seconds (default `2`); it does not yet
load agents, compute model outputs, or persist scores. The implementation entry
point is [`dagster_defs.py`](dagster_defs.py).

- Job: `scoring_job`
- Op: `scoring_placeholder_op`
- Future execution: select active scoring agents, load eligible profiles,
  compute bounded batches, validate outputs, and persist results

This README describes the intended contract for the implementation without
claiming that the scoring pipeline is already active.

## Agent registry: `cdp_ai_agents`

[`CdpAiAgent`](../../customer360-dao/src/leo_customer360_dao/models/identity.py)
is the unified registry for ML models, rules engines, and task-oriented
agents. A scoring run should resolve an active registry row by `agent_code` and
honor its:

- `model_type` and `model_name`
- `status`
- `schedule_definition`
- `input_features`
- `hyperparameters`
- `required_variables`
- instruction and model version metadata

The registry is shared by the platform. The scoring service must execute only
agents in its scoring scope; it must not silently run planning, content,
identity, notification, or campaign agents.

The initial scoring scope includes:

| Agent code | Profile output |
| --- | --- |
| `lead_scoring` | Conversion probability and lead grade |
| `churn_scoring` | Churn probability and risk tier |
| `clv_scoring` | Predictive customer lifetime value and CLV segment |
| `cx_intelligence` | Customer-experience score derived from feedback and interaction signals |
| `data_quality` | Profile completeness, freshness, consistency, and quality signals |

Other registered agents may produce classifications or recommendations, but
they are outside this service's scoring execution scope unless explicitly
added as a scoring contract.

## Workflow registry: `cdp_agent_workflow`

[`CdpAgentWorkflow`](../../customer360-dao/src/leo_customer360_dao/models/agent_workflow.py)
assigns registered agents to a tenant-owned segment. Each workflow step
contains:

- `tenant_id` and `segment_id`
- `agent_code`
- positive `execution_order`
- `is_active`
- optional `schedule_definition`
- JSONB `configuration`
- bounded candidate content IDs

The scoring job may use active workflow rows to determine which scoring agents
apply to a segment and in what order. Workflow execution is always tenant
scoped, preserves the configured order, and must not allow a workflow to
invoke an inactive or non-scoring agent. Workflow scheduling and configuration
are orchestration metadata; the scoring agent remains responsible for
computing its profile output.

The API validation contract for workflow steps is defined in
[`schemas/agent_workflow.py`](../../customer360-dao/src/leo_customer360_dao/schemas/agent_workflow.py).

## Profile scoring contract

The implementation should:

1. Load active scoring agents and their versioned configuration.
2. Select eligible, tenant-scoped master profiles in bounded batches.
3. Read only the declared `input_features` and required profile/event data.
4. Compute the agent-specific score with a deterministic model or rules
   implementation.
5. Validate ranges, tiers, model version, and evidence before persistence.
6. Update only the score fields owned by that agent and set
   `scores_updated_at`.
7. Preserve prior values or record an explicit failure when a profile cannot
   be scored; never write a success-shaped default.
8. Emit structured run metrics for processed, skipped, failed, and updated
   profiles.

The primary profile outputs include:

- `lead_conversion_probability`, `lead_grade`
- `churn_probability`, `churn_risk_tier`
- `predictive_clv`, `clv_segment`
- `latest_nps_score`, `average_csat`, `overall_sentiment_score`
- profile data-quality indicators and `scores_updated_at`

Every query and write must enforce `tenant_id`. Scoring must not change
identity links, persona history, campaign state, consent, suppression, or
source-of-truth event data.

## Dependencies and local development

The service declares Dagster, `pymc-marketing`, the DAO, and test dependencies
in [`requirements.txt`](requirements.txt). From `customer360-backend/`, load
the workspace with:

```bash
dagster dev -w workspace.yaml
```

When the real implementation is added, scoring-specific unit tests should
cover registry selection, workflow ordering, tenant isolation, batch limits,
output validation, idempotent reruns, and failure handling.