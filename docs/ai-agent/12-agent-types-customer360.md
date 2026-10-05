# The 12 Agent Types in Customer 360: A 5W1H Taxonomy

*Reference Document: "Customer_360_AI_Agent_Type_Taxonomy_12.docx"*

This document outlines a canonical taxonomy for predictive ML, segmentation, recommendation, decisioning, graph intelligence, policy execution, and generative AI. It is structured using the 5W1H method (What, Why, Who, Where, When, How) to clarify the execution taxonomy of the 12 Core Agent Types. It also defines the minimum processing contracts used by the Dagster pipeline scaffolds.

---

## 1. WHAT are the 12 Agent Types?

The 12 Agent Types define the fundamental execution methods of AI agents within the platform. They describe *how* an agent computes, rather than the specific business problem it solves. 

Here are the 12 canonical types:

| # | Agent Type | SQL Value | Primary Role |
|---|---|---|---|
| 1 | **Classification** | `classification` | Predict customer class, state, or outcome. |
| 2 | **Regression** | `regression` | Predict continuous customer or business values. |
| 3 | **Clustering** | `clustering` | Discover behavioral customer groups and segments. |
| 4 | **Ranking & Recommendation**| `ranking_recommendation` | Rank products, content, offers, campaigns, or actions. |
| 5 | **Forecasting** | `forecasting` | Predict future customer or business behavior. |
| 6 | **Anomaly Detection** | `anomaly_detection` | Detect unusual customer behavior, events, or data. |
| 7 | **Uplift Modeling** | `uplift_modeling` | Identify customers most likely to change due to an intervention. |
| 8 | **Semantic / Embedding** | `semantic_embedding` | Understand similarity, intent, interests, and content. |
| 9 | **Graph Intelligence** | `graph_ml` | Analyze identity, relationships, influence, and connections. |
| 10 | **Optimization / Decisioning**| `optimization` | Choose the best action under business constraints. |
| 11 | **Rules Engine** | `rules_engine` | Apply deterministic business logic and policies. |
| 12 | **Generative LLM** | `generative_llm` | Reason, generate, explain, summarize, and orchestrate. |

These values are the registry's canonical `model_type` values. A concrete agent
has its own stable `agent_code`; do not create a new type for a business
capability such as churn prediction, lead scoring, or next-best action.

## 2. WHY exactly 12 Types?

**To keep Agent Type and Business Capability Separate.** 
The taxonomy expands the `model_type` from an original five-value list to this agreed 12-value list to prevent the registry from becoming a bloated list of business-specific agent names. 

*   **Agent Type** answers: “How does this agent compute?”
*   **Business Capability** answers: “What problem does it solve?”

*Design Decision:* Keep exactly 12 Agent Types. Do not add business-specific values (e.g., lead_scoring, churn_prediction) to the `model_type`. 

## 3. WHO is involved?

*   **Customers:** The end subjects whose behaviors, identities, and intents are analyzed (e.g., via Clustering or Graph Intelligence).
*   **Concrete Agents:** The distinct implementations (e.g., Lead Scoring Agent, CLV Prediction Agent) that utilize the 12 core types to serve a specific business capability.
*   **Platform Systems:** The registry and databases that store runtime inputs, hyperparameters, prompts, and lifecycle statuses for these agents.

## 4. WHERE are these agents applied?

These agents are deployed within an **Agentic Customer 360 platform**. 
The architecture relies on Recommended Registry Semantics to track them:
*   `agent_code`: Stable identifier for the concrete agent.
*   `model_type`: One of the 12 execution types.
*   `model_name`: The actual underlying model/LLM.
*   `cdp_profile_attributes.agent_code`: Links AI-derived Customer 360 attributes back to their producing agent.
*   `cdp_agent_workflow`: Configures ordered agent execution.

## 5. WHEN should you use each Agent Type? (Type Selection Principles)

*   **When** the output is a discrete state or class -> Use **Classification**.
*   **When** the output is a continuous numeric value -> Use **Regression**.
*   **When** discovering natural groups (rather than manual rules) -> Use **Clustering**.
*   **When** the primary task is ordering candidate items by relevance -> Use **Ranking & Recommendation**.
*   **When** the target is a value or behavior over future time steps -> Use **Forecasting**.
*   **When** detecting unusual observations or behavioral deviations -> Use **Anomaly Detection**.
*   **When** evaluating the incremental impact of an intervention -> Use **Uplift Modeling**.
*   **When** representing meaning, similarity, or intent in vector space -> Use **Semantic / Embedding**.
*   **When** the signal is in relationships between profiles, entities, or events -> Use **Graph Intelligence**.
*   **When** choosing an action subject to multiple constraints -> Use **Optimization / Decisioning**.
*   **When** deterministic, auditable policy logic is required -> Use **Rules Engine**.
*   **When** you need reasoning, generation, explanation, or orchestration -> Use **Generative LLM**.

## 6. HOW do they operate? (Input and Output Contracts)

Every pipeline receives a common execution envelope and returns a
tenant-scoped result envelope. `input_data` is the normalized, type-specific
feature/context payload prepared by the caller; `configuration` carries
agent/workflow parameters. These contracts deliberately do not decide how
features are sourced, how models are trained, or where results are persisted.

### Common input

| Field | Required | Contract |
|---|---|---|
| `tenant_id` | Yes | UUID that scopes every read and write. |
| `segment_id` | Yes | UUID of the tenant-owned segment being processed. |
| `agent_code` | Yes | Non-blank registry code, at most 100 characters. |
| `model_type` | Yes | Exactly one of the 12 canonical values above. |
| `input_data` | Yes | JSON object containing prepared features, history, candidates, or context. |
| `configuration` | No | JSON object containing validated workflow/agent parameters; defaults to `{}`. |
| `candidate_content_item_ids` | No | Unique UUID list; accepted only for `ranking_recommendation`. The handler must verify every candidate belongs to `tenant_id`. Defaults to `[]`. |
| `trigger_event` | No | JSON object with event context; defaults to `{}`. |

Input objects must be JSON-serializable and may not contain NaN or infinity.
The shared runner validates the envelope; each eventual implementation must
also validate its type-specific feature names, ranges, required fields, and
model/configuration compatibility before doing work.

### Type-specific result

The `result` object is validated against the minimum shape below. Additional
fields such as confidence, explanations, bounds, reasons, and provenance may
be added where they are meaningful, but should be versioned and documented
when consumers depend on them.

| Agent Type | Required `result` fields | Additional validation |
|---|---|---|
| Classification | `label` | Optional `probability` must be finite and in `[0, 1]`. |
| Regression | `value` | Must be a finite number. |
| Clustering | `cluster_id` | Optional `membership_score` must be finite and in `[0, 1]`. |
| Ranking & Recommendation | `ranked_items` | Must be a list; each item should carry a stable ID, rank, score, and optional reason. |
| Forecasting | `forecast_series` | Must be a list; each point should identify its time/period and forecast value. |
| Anomaly Detection | `anomaly_score`, `is_anomaly` | Score must be finite; flag must be boolean. |
| Uplift Modeling | `uplift_score` | Must be a finite number; document treatment/control semantics with the implementation. |
| Semantic / Embedding | `embedding` | Must be a non-empty list of finite numbers. |
| Graph Intelligence | `relationship_signals` | Must be a JSON object; include graph/model provenance as appropriate. |
| Optimization | `selected_action` | Must be a string; include decision score/rationale where available. |
| Rules Engine | `decision`, `decision_trace` | Decision is `allow`, `deny`, or `review`; trace is a list. |
| Generative LLM | One of `text`, `structured_output`, or `summary` | Validate the selected response schema and any safety/business constraints before returning it. |

### Common output

Successful processing returns `contract_version`, Dagster `run_id`,
`tenant_id`, `segment_id`, `agent_code`, `model_type`, `result`, and
`completed_at` (UTC timestamp). The tenant and segment are copied from the
validated input rather than inferred from model output. Outputs must remain
JSON-serializable. A pipeline must raise/report a failure if it cannot produce
and validate its result; it must not return an empty or success-shaped
placeholder.

## 7. WHERE and HOW are pipelines implemented?

The reusable contracts and 12 type-specific handler entry points live in
`customer360-backend/ai_agents_runners/agent_pipeline/`. The
`agent_type_pipeline_job` Dagster job accepts a JSON `payload`, validates it,
dispatches by `model_type`, validates the handler result, and returns the
common output envelope. Each handler is intentionally unimplemented and
raises `NotImplementedError`; this prevents an unfinished agent from appearing
to complete successfully. The workflow master currently selects steps but
does not invoke these handlers.

An eventual handler should remain a deterministic unit of work behind the
shared validation boundary:

1. Validate the type-specific fields and configuration before side effects.
2. Scope every data read/write to the input `tenant_id`; do not trust tenant
   identifiers returned by a model.
3. Use Dagster `context.log` for structured operational context, not for
   customer payloads, credentials, prompts containing personal data, or model
   output that should be protected.
4. Make writes idempotent using the Dagster run ID or a stable business key.
   Keep external calls bounded with explicit timeouts and surface failures so
   Dagster can report/retry them intentionally.
5. Return only after constructing and validating the type-specific `result`
   and the common output envelope. Persisting results or writing profile
   attributes requires a separate, explicit tenant-safe implementation.

For local execution, load `customer360-backend/workspace.yaml` from
`customer360-backend/`, then select `agent_type_pipeline_job` in Dagster. Supply
`ops.run_agent_type_pipeline_op.config.payload` with the common input fields
above. Until a handler is implemented, the run is expected to fail explicitly.