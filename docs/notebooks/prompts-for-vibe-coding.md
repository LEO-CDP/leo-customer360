# 9 refined AI agent prompts, aligned with the `customer360` PostgreSQL schema

- Ref: docs/notebooks/LEOCDP_RAG_FULL_TEST_9_use_cases.ipynb
- Ref: customer360-database/database-schema.sql
- Model policy: use only `openai/gpt-5.6-luna` and `google/gemini-3.5-flash-lite`. The repository currently defaults Gemini configuration to an older Flash Lite release, so configure `gemini-3.5-flash-lite` explicitly where the provider account supports it. The companion notebook uses an in-memory SQLite demo for several flows; it does not validate PostgreSQL schema writes.

### 1. `01_Customer_Support.md`

```markdown
# System Prompt: Customer Support Agent (google/gemini-3.5-flash-lite)

## Input
- User query text (e.g., "How do I reset my password?")
- User identity identifier (email or phone number)
- Target `tenant_id`

## Requirements
- **RAG Data Source**: Internal help-center knowledge base (vector store).
- **Fallback Search**: Tavily domain-restricted web search, enabled only when `TAVILY_API_KEY` is configured.
- **Database Schema**: Strictly use `customer360.cdp_master_profiles` for identity lookup and `customer360.crm_customer_contacts` for interaction logging. Do not invent tables or columns.
- **Identity requirement**: `crm_customer_contacts.master_profile_id` is `NOT NULL`; an unresolved identity must not be inserted as `NULL`.

## Workflow Steps by Step
1. **Identity Resolution**: Query `customer360.cdp_master_profiles` using the provided email or phone number and `tenant_id` to retrieve the `master_profile_id`.
2. **Context Retrieval**: Embed the user query and search the help-center vector database.
3. **Fallback Evaluation**: If retrieval returns the configured `NOT_FOUND_ANSWERS` result, execute a Tavily search restricted to the company's verified domain. Do not hard-code a similarity threshold unless the retriever defines and tests one.
4. **Answer Generation**: Synthesize the retrieved context or search results to formulate a concise, helpful response using `google/gemini-3.5-flash-lite`.
5. **Interaction Logging**: Insert a new record into `customer360.crm_customer_contacts` mapping the interaction:
   - `tenant_id` = active tenant
   - `master_profile_id` = resolved ID
   - `contact_type` = 'support'
   - `contact_channel` = 'chat'
   - `contact_content` = the synthesized response text
   - `contact_date` may use its database default
   - Set the database session tenant context before the query and insert, and keep the explicit `tenant_id` predicate.

## Output
Return the complete Python script (using LangChain or LlamaIndex) implementing this flow. Return the full code with comprehensive comments explaining the retrieval and database insertion logic.

## Test Cases
- **Test Case 1 (KB Hit)**: User asks a common question present in the vector DB. Expected: Returns KB answer and successfully logs to `crm_customer_contacts`.
- **Test Case 2 (Fallback Hit)**: User asks about a recent feature not in the vector DB. Expected: Triggers Tavily search, returns accurate answer, and logs to `crm_customer_contacts`.
- **Test Case 3 (Unknown User)**: Query comes from an unmapped email. Expected: Returns the answer, but skips the contact insert (or records it in a separately defined anonymous-event store); never inserts `NULL` into `master_profile_id`.

## Code Impact Mapping

RAG retrieval is implemented by `tools/docs-vector-search/src/agent.py` (`RagAgent`, `retrieve`, `query`). Profile lookup and contact CRUD are in `customer360-api/core/routers/identity_api.py` and `customer360-api/core/routers/relations_api.py`, backed by `customer360-dao` relation models and repositories. The combined support-answer, Tavily fallback, and contact-logging workflow is not currently implemented as one service.

```

### 2. `02_Text_to_JSON_rules_in_segment.md`

```markdown
# System Prompt: Natural-Language-to-JSON-Rules Segment Agent (openai/gpt-5.6-luna)

## Input
- Natural language segment request (e.g., "Create a segment for all profiles that gender is make, customer_since 2026-09-01")
- Target `tenant_id`
- Optional target `domain`; use `all` when the request says all profiles and does not specify a domain

## Requirements
- **Task boundary**: Convert text into a valid `json_rules` object for `customer360.cdp_segments`. Do not answer with general-purpose SQL, emit `sql_rules`, or update `cdp_master_profiles` directly.
- **Schema context**: Segment membership is evaluated against `customer360.cdp_master_profiles`. Resolve valid fields and data types from `GET /api/v1/segments/segmentable-profile-attributes`; raw-only `cdp_raw_profiles_stage` fields are not valid segment fields.
- **QueryBuilder contract**: Return exactly the Audience Builder shape `{ "condition": "AND" | "OR", "rules": [...] }`. Each leaf uses a canonical `field`, a supported operator, and a correctly typed `value`; nested groups are allowed.
- **Rule safety**: Reject unknown fields, unsupported operators, SQL text, comments, statement separators, DDL/DML, and rules that reference tables other than `cdp_master_profiles`.
- **Tenant isolation**: Preserve the active `tenant_id` in the surrounding segment-creation request and never place a caller-controlled tenant predicate inside `json_rules`.
- **Persistence contract**: The application, not the model, passes the returned `json_rules` through the existing segment compiler/API and creates the segment with `processed_by = 'ai_agent'`.

## Workflow Steps by Step
1. **Load the field catalog**: Retrieve active, segmentable attributes for the requested domain. Confirm that `gender` and `customer_since` resolve to direct `cdp_master_profiles` fields and capture their types (`TEXT` and `DATE`).
2. **Interpret the request**: Treat `make` as the likely typo `male` because `cdp_master_profiles.gender` permits only `male`, `female`, or `other`. Record that normalization in the result. If the text could mean something else, stop and ask for clarification instead of guessing.
3. **Build JSON rules**: Produce only the normalized QueryBuilder rule tree:

~~~json
{
   "condition": "AND",
   "rules": [
      {"field": "gender", "operator": "equal", "value": "male"},
      {"field": "customer_since", "operator": "equal", "value": "2026-09-01"}
   ]
}
~~~

4. **Validate the JSON rules**: Confirm that the root has a non-empty `rules` array, the condition is `AND` or `OR`, every field exists in the catalog, every operator is valid for its data type, and `customer_since` keeps the ISO date value `2026-09-01`. Do not compile or return SQL in this model response.
5. **Pass rules to the current segmentation process**: The application wraps the returned `json_rules` with tenant/domain/name metadata, uses the existing QueryBuilder-compatible compiler to derive `sql_rules`, and submits the complete segment payload to `/api/v1/segments/` with `processed_by = 'ai_agent'`:

~~~json
{
   "tenant_id": "<tenant_id>",
   "domain": "all",
   "segment_tag": "male_customers_since_2026_09_01",
   "segment_name": "Male Customers Since 2026-09-01",
   "description": "Profiles with gender male and customer_since equal to 2026-09-01.",
   "json_rules": {
      "condition": "AND",
      "rules": [
         {"field": "gender", "operator": "equal", "value": "male"},
         {"field": "customer_since", "operator": "equal", "value": "2026-09-01"}
      ]
   },
   "processed_by": "ai_agent",
   "is_active": true
}
~~~

6. **Recompute membership**: After the application has compiled and persisted the segment, use the existing create hook or `POST /api/v1/segments/{segment_id}/recompute`, then poll `/api/v1/segments/admin/recompute-status/{run_id}` until `success` or `failure`. Membership updates are asynchronous and synchronize `segment_tag` on matching `cdp_master_profiles.segmentation_tags`.
7. **Verify before reporting success**: Read the persisted segment and confirm the stored `json_rules` exactly match the model result, then verify `member_count`, `last_computed_at`, and the matched-profile endpoint. If compilation, persistence, recompute, or verification fails, report the failure and do not claim that the segment was created successfully.

## Output
Return a structured JSON result containing `interpretation`, `json_rules`, `validation_status`, and `ready_for_segment_persistence`. The `json_rules` value must be directly usable by the current jQuery QueryBuilder/Audience Builder contract. Do not include generated SQL in this model output.

## Test Cases
- **Test Case 1 (Requested example)**: Input `"create a segment for all profiles that gender is make, customer_since 2026-09-01"`. Expected: Returns `gender = male` and `customer_since = 2026-09-01` as two `AND` rules, with an interpretation note that `make` was normalized to `male`.
- **Test Case 2 (Ambiguous value)**: Input `"create a segment for profiles where gender is m"`. Expected: Does not return ready-to-persist rules; asks whether the user means `male`.
- **Test Case 3 (Unsafe or invalid field)**: Input references `crm_transactions.amount`, a raw staging-only field, or includes SQL such as `1=1; DROP TABLE ...`. Expected: Rejects the request and explains that rules are limited to the segmentable `cdp_master_profiles` catalog.

## Code Impact Mapping

`customer360-api/core/routers/segment_api.py` exposes the segment CRUD, segmentable attribute catalog, matched-profile verification, and asynchronous recompute endpoints. `customer360-dao/src/leo_customer360_dao/schemas/segmentation.py` validates the segment create payload; `customer360-backend/segmentation/segmentation/recompute.py` evaluates persisted rules against active `cdp_master_profiles` rows and synchronizes `segmentation_tags`; and `customer360-frontend/static/js/segments-view.js` contains the current QueryBuilder rule-to-SQL compiler. The AI layer must return only validated `json_rules` and hand them to the existing application compiler/persistence path; it must not invent a parallel SQL path or write directly to PostgreSQL.

```

### 3. `03_Campaign_Orchestration.md`

```markdown
# System Prompt: Campaign Orchestration (openai/gpt-5.6-luna)

## Input
- High-level marketer objective (e.g., "Draft an email campaign for high-value dormant users.")
- Target `tenant_id`
- Active `user_id` (marketer)

## Requirements
- **Tool-Calling**: Agent must call the existing application services and repositories; it must not bypass their validation with raw database writes.
- **Database Schema**: Strictly use `customer360.crm_campaign`, `customer360.cdp_segments`, `customer360.cdp_content_items`, and `customer360.crm_campaign_content_items`.
- **Tenant isolation**: Set the database session tenant context and include the active `tenant_id` on every read and write, including content-link rows.
- **Current draft-service contract**: `/campaigns/draft` requires an existing tenant-owned `segment_id` that passes `is_active` and `status_code == 1`, plus a tenant-owned `template_id`; the template must be `Approved`. It does not create a segment or copy template inline.

## Workflow Steps by Step
1. **Intent Analysis**: Parse the marketer's objective to identify the target audience and channel.
2. **Audience Mapping (Tool)**: Query `customer360.cdp_segments` by `tenant_id` and the requested `segment_name` or `segment_tag`. The current `/campaigns/draft` service requires an existing segment with `is_active = TRUE` and `status_code = 1`; operationally, its membership computation should also be complete before drafting. If none exists, call the separate `/segments` creation flow with `tenant_id`, a unique `segment_tag`, `segment_name`, a valid non-empty `json_rules` rule tree, and `processed_by = 'ai_agent'`; wait for the tenant-scoped recompute to complete before retrying the draft. Do not invent membership rules or proceed while the segment is not resolvable.
3. **Campaign Draft (Tool)**: Insert a new row into `customer360.crm_campaign` setting:
   - `tenant_id` = active tenant
   - `name` = generated campaign title
   - `status` = 'Draft'
   - `approval_status` = 'InReview' (the current draft repository creates a human-reviewable draft in this state)
   - `segment_id` = ID from step 2
   - `user_id` = active marketer ID
   - `template_id` = a tenant-owned `Approved` template selected before calling `/campaigns/draft`
   - `objective` = validated objective from the brief
   - The current `CampaignDraftRequest` accepts `segment_id`, `template_id`, `objective`, and optional `budget_time_constraints`; it does not accept `channel`.
4. **Content Linking (Tool)**: Let `CampaignDraftRepository.create_draft` build the closed candidate list from active tenant-owned `cdp_content_items`, let the AI return only candidate IDs, and let the repository validate and persist `crm_campaign_content_items` with deterministic positions and roles. Do not insert arbitrary content IDs directly from model output.

## Output
Return the complete Python implementation utilizing an LLM tool-calling framework (e.g., LangChain agents). Return the full code with comprehensive comments for every tool definition and database interaction.

## Test Cases
- **Test Case 1 (End-to-End Creation)**: Valid request matching an existing segment. Expected: Outputs a successfully created `campaign_id` with linked content.
- **Test Case 2 (Missing Segment)**: Goal targets a demographic not currently segmented. Expected: Creates the segment through the separate segment API, waits for its tenant-scoped recompute, then calls the campaign draft endpoint; it must not pretend the campaign was created before the segment is resolvable.

## Code Impact Mapping

`customer360-api/core/routers/campaign_draft_api.py` exposes `/campaigns/draft` and `/campaigns/zalo-draft`. `customer360-api/core/repositories/campaign_draft_repository.py` validates the segment/template, calls the planner, enforces `InReview`, and persists only candidate content IDs. Planner code is in `customer360-agent/src/campaign_planner/email.py` and `zalo.py`; shared provider and prompt resolution is in `base.py` and `customer360-agent/src/prompts/`. Segment creation and recomputation are owned by `customer360-api/core/routers/segment_api.py` and `segment_repository.py`.

```

### 4. `04_Copy_Drafting.md`

```markdown
# System Prompt: Copy Drafting Agent (google/gemini-3.5-flash-lite)

## Input
- Brief/Campaign details (e.g., "Announce our summer sale to VIPs")
- Target `tenant_id`
- `persona_archetype_id` (optional, from `customer360.cdp_persona_archetypes`)

## Requirements
- **Format Requirements**: Must return structured JSON matching the `customer360.crm_message_templates` schema layout.
- **Tone Alignment**: Ground the generation in brand guidelines and the specific persona archetype summary if provided.

## Workflow Steps by Step
1. **Context Loading**: Retrieve brand voice guidelines from an explicitly configured source and the persona summary from `cdp_persona_archetypes` (if `persona_archetype_id` is passed). Do not treat unspecified local context as available data.
2. **Copy Generation**: Formulate the copy using `google/gemini-3.5-flash-lite`, outputting a strict JSON object containing:
   - `name`
   - `subject`
   - `message_body` (plain text)
   - `html_body` (formatted HTML)
   - `variables` (JSON mapping of dynamic tags like `{{first_name}}`)
3. **Template Storage**: Insert a new record into `customer360.crm_message_templates` with:
   - `tenant_id` = active tenant
   - `name` = generated template name
   - `message_type` = 'EMAIL'
   - `status` = 'Draft'
   - `persona_id` = `persona_archetype_id` when provided and tenant-validated
   - The generated fields from step 2.
   - `variables` must be a JSON object; `context` may store the generation inputs.

## Output
Return the complete Python script executing the structured output prompt and the database `INSERT`. Return the full code with comprehensive comments.

## Test Cases
- **Test Case 1 (Standard Brief)**: General product announcement. Expected: Outputs well-formatted HTML and subject line, inserts successfully.
- **Test Case 2 (Persona-Driven)**: Brief includes a `persona_archetype_id` for "Gen-Z Shopper". Expected: Copy tone is distinctively tailored, and the template row saves with the archetype's `persona_id` foreign key linked.

## Code Impact Mapping

`customer360-dao/src/leo_customer360_dao/models/crm.py` defines `MessageTemplate` and the associated approval fields. Campaign drafting reads tenant-owned `Approved` templates through `customer360-api/core/repositories/campaign_draft_repository.py`; activation also requires an approved template. No dedicated email copy-generation route or service currently exists, so this prompt describes new orchestration that must preserve the existing Draft -> InReview -> Approved/Rejected lifecycle.

```

### 5. `05_Analytics_Chat.md`

```markdown
# System Prompt: Analytics Chat (openai/gpt-5.6-luna + google/gemini-3.5-flash-lite)

## Input
- Multi-turn conversation history
- Latest user query (e.g., "Now break that down by channel")
- Target `tenant_id`

## Requirements
- **Dual-Model Architecture**: `v4-pro` handles contextual SQL generation; `v4-flash` handles narrative and chart formatting.
- **Schema**: `customer360.vw_campaign_performance_metrics` for lifetime campaign aggregates, `customer360.crm_campaign_performance_daily` for date filters and daily trends, plus `customer360.crm_transactions` and `customer360.cdp_master_profiles` for customer analysis.
- **Tenant isolation**: Preserve the tenant predicate when modifying SQL, including on joined sources, and set the database session tenant context.

## Workflow Steps by Step
1. **State Retrieval**: Load the previous user query and the last generated SQL string.
2. **Query Modification (Luna)**: Pass the history and new prompt to `openai/gpt-5.6-luna`. Instruct it to modify the previous SQL while preserving its safety checks and tenant predicates. Use `crm_campaign_performance_daily` when the request needs `report_date`; the lifetime view has no `report_date` column.
3. **Execution**: Execute the modified SQL against the `customer360` schema.
4. **Data Transformation (Gemini)**: Pass the raw tabular JSON results to `google/gemini-3.5-flash-lite`. Ask it to output a UI-ready JSON format (e.g., `{ "labels": [...], "series": [...] }`) and a single conversational summary sentence without inventing values.

## Output
Return the complete Python conversational loop class. Return the full code with comprehensive comments detailing the prompt chaining between the two models.

## Test Cases
- **Test Case 1 (Contextual Follow-up)**: Turn 1: "Show daily spend." Turn 2: "Filter that to just December." Expected: `openai/gpt-5.6-luna` uses `crm_campaign_performance_daily` and appends a bounded `WHERE report_date` clause while retaining tenant isolation.
- **Test Case 2 (Chart Formatting)**: A tenant-scoped query returns 5 rows of `channel` versus `total_clicks` from the lifetime view. Expected: `google/gemini-3.5-flash-lite` outputs mapped arrays for `labels` (channels) and `series` (clicks).

## Code Impact Mapping

`customer360-api/core/routers/analytics_api.py` and `customer360-api/core/repositories/analytics_repository.py` operate tracking-log aggregation jobs through Dagster and Redis. `customer360-frontend/static/js/analytics.js` renders existing analytics views. There is no conversational SQL-history or chart-narrative service; implement this as a new service using the same safety controls as Text-to-SQL rather than extending the job-submission endpoint.

```

### 6. `06_Ticket_Classification.md`

```markdown
# System Prompt: Ticket Classification (google/gemini-3.5-flash-lite)

## Input
- Batch of support contacts selected by the application (from `customer360.crm_customer_contacts` where `tenant_id = :tenant_id` and `contact_type = 'support'`)

## Requirements
- **Output constraint**: Must classify by Urgency, Sentiment, and Routing Team.
- **Schema constraint**: `crm_customer_contacts` has no classification or `metadata` column. Do not update it; output a clean Python dictionary map for downstream application logging or use a separately defined classification store.
- **Policy constraint**: The labels below are application policy, not database-enforced enum values. Load the tenant's routing policy when one exists.

## Workflow Steps by Step
1. **Data Ingestion**: Query `customer360.crm_customer_contacts` for `contact_id` and non-null `contact_content`, scoped by `tenant_id`; maintain a separate checkpoint or store if only previously unclassified contacts should be processed.
2. **Classification Prompts**: Feed the batch texts to `google/gemini-3.5-flash-lite` with a strict prompt demanding a structured JSON array back.
3. **Categorization Rules**:
   - `urgency`: 'High', 'Medium', 'Low'
   - `sentiment`: 'Positive', 'Neutral', 'Negative'
   - `routing_team`: 'Billing', 'Technical', 'General'
4. **Result Mapping**: Map the LLM's structured JSON output back to the original `contact_id`s in a Python dictionary.

## Output
Return the complete Python batch processing script. Return the full code with comprehensive comments explaining the batch logic and prompt structure.

## Test Cases
- **Test Case 1 (High Urgency)**: "My account was charged twice, I need a refund immediately!" Expected: Urgency: High, Sentiment: Negative, Routing: Billing, subject to the tenant routing policy.
- **Test Case 2 (Low Urgency)**: "How do I change my profile picture?" Expected: Urgency: Low, Sentiment: Neutral, Routing: General.

## Code Impact Mapping

`customer360-api/core/routers/relations_api.py` exposes tenant-scoped contact CRUD, backed by `CustomerContact` in `customer360-dao`. `customer360-dao/src/leo_customer360_dao/crud/crm_sync.py` also creates contact-log rows from profile signals. No classification worker, routing-policy service, or classification persistence model currently exists; keep this feature outside `crm_customer_contacts` until a dedicated, tenant-scoped result store is introduced.

```

### 7. `07_Campaign_Performance_Narrative.md`

```markdown
# System Prompt: Campaign Performance Narrative (google/gemini-3.5-flash-lite)

## Input
- A tenant-scoped JSON dictionary representing a single row from `customer360.vw_campaign_performance_metrics` (includes `total_spend`, `total_revenue`, `total_conversions`, `total_impressions`, `cpa`, `roas`, `ctr_percentage`).

## Requirements
- **Tone**: Professional, executive-summary style, non-technical plain English.
- **Length**: 3-5 sentences maximum.

## Workflow Steps by Step
1. **Data Parsing**: Load the metric JSON payload.
2. **Insight Extraction**: Identify the strongest performing KPI (e.g., highest ROAS or CTR) and the weakest performing KPI (e.g., high CPA or low impressions).
3. **Narrative Generation**: Use `google/gemini-3.5-flash-lite` to draft a summary explaining what the numbers mean for the business. Highlight efficiency and scale, and never interpret `cpa = 0.00` as good performance when `total_conversions = 0`.
4. **Formatting**: Output the final string. No database updates are necessary.

## Output
Return the complete Python script executing this transformation via the LLM API. Return the full code with comprehensive comments.

## Test Cases
- **Test Case 1 (Highly Profitable)**: High ROAS, Low CPA. Expected narrative focuses on efficiency and recommends scaling spend.
- **Test Case 2 (High Spend, Zero Conversions)**: Expected narrative detects `total_conversions = 0`, avoids treating the derived `cpa = 0.00` as positive, and tactfully describes the conversion-funnel risk. Any pause or scaling recommendation must come from an explicit business policy, not the prompt alone.

## Code Impact Mapping

`customer360-database/database-schema.sql` defines `vw_campaign_performance_metrics`, and existing analytics/dashboard surfaces consume campaign metrics. No LLM campaign-narrative module exists in the repository. Implement this as a read-only service that receives an already tenant-scoped result row; do not add database writes or embed it in campaign activation.

```

### 8. `08_Schema_Mapping_Assistant.md`

```markdown
# System Prompt: Schema-Mapping Assistant (openai/gpt-5.6-luna)

## Input
- A list of raw client CSV header strings (e.g., `["client_mail", "cell", "ltv", "address_1"]`)

## Requirements
- **Target Schema**: `customer360.cdp_raw_profiles_stage` ONLY. 
- **Context Source**: Use `customer360-database/database-schema.sql` as the source of truth for `cdp_raw_profiles_stage` columns and use `customer360.cdp_profile_attributes` as supplemental governed metadata. The catalog can describe master-profile or matching-key fields and is not limited to stage columns.
- **Confidence Flagging**: If a mapping is ambiguous, flag it for human review.

## Workflow Steps by Step
1. **Context Retrieval**: Load the exact column names and datatypes available in `customer360.cdp_raw_profiles_stage`.
2. **Semantic Matching**: Prompt `openai/gpt-5.6-luna` to evaluate each raw client header against the target columns and datatypes.
3. **Decision Logic**:
   - Obvious matches (e.g., `client_mail` -> `email`) map directly.
   - Non-obvious or unmapped fields must return `"target_column": null` and `"requires_review": true` unless the ingestion contract explicitly preserves the value inside `event_payload` JSONB.
   - `tenant_id` and `source_system` are required ingestion context and must be supplied by the pipeline; they are not optional CSV mappings. `domain` defaults to `'banking'` in the schema but should be set explicitly when known.
4. **JSON Construction**: Output a strict JSON dictionary mapping `raw_header` -> `{"target_column": "..." | null, "requires_review": bool, "reasoning": "..."}`.

## Output
Return the complete Python script that runs the semantic mapping prompt and parses the JSON output. Return the full code with comprehensive comments.

## Test Cases
- **Test Case 1 (Direct Match)**: Inputs `['usr_fname', 'usr_lname']`. Expected: Maps strictly to `first_name` and `last_name` with `requires_review: false`.
- **Test Case 2 (Ambiguous Match)**: Input `['membership_points']`. Expected: Returns `target_column: null` with `requires_review: true` unless a documented ingestion contract says to preserve it under `event_payload`; it must not be presented as a canonical stage column.

## Code Impact Mapping

`customer360-api/core/routers/identity_api.py` exposes profile and profile-attribute metadata. `customer360-database/init-core-database.sql` seeds the governed `cdp_profile_attributes` catalog, while `customer360-database/database-schema.sql` is the source of truth for stage columns. No raw-header mapping assistant currently exists; implement it as a review-first ingestion service and route accepted records through the existing raw-profile API or ingestion pipeline.

```

### 9. `09_Persona_Decision_Loop.md`

```markdown
# System Prompt: Persona Decision Loop (google/gemini-3.5-flash-lite; proposed extension)

## Input
- `master_profile_id`
- Target `tenant_id`

## Requirements
- **Actionability**: The generated Next Best Action (NBA) must be specific and measurable.
- **Database Schema**: Use `customer360.cdp_master_profiles`, `customer360.crm_transactions`, `customer360.cdp_customer_personas`, `customer360.cdp_profile_links`, and `customer360.cdp_raw_profiles_stage` when raw browsing or event evidence is required.
- **Tenant isolation and history**: Scope every query by `tenant_id`. `cdp_customer_personas` is versioned; prefer inserting a new computed version and deactivating the previous active row in one transaction instead of overwriting history.
- **Current implementation boundary**: The existing `PersonaResolutionEngine` computes the NBA from the master-profile snapshot and uses deterministic rules; optional Google Gemini/offline helpers generate non-PII labels and summaries. It does not currently call the proposed Gemini reasoning flow or load transactions/raw events for this decision loop.

## Workflow Steps by Step
1. **Context Gathering**: 
   - Query `cdp_master_profiles` for demographic baseline and lifecycle stage.
   - Query `crm_transactions` for the last 5 transactions (sorted by `transaction_time DESC`); do not describe them as browsing events.
   - If browsing or product-interest evidence is required, join `cdp_profile_links` to `cdp_raw_profiles_stage` by `raw_profile_id`, then filter both by `tenant_id` and the requested `master_profile_id`; inspect `event_name`, `event_time`, and `event_payload` only when the available ingestion data supports it.
   - Query `cdp_customer_personas` for active rows (`is_active = TRUE`) scoped by tenant and profile, ordered by `computed_at DESC`; the schema does not guarantee only one active row across archetypes, so handle multiple rows explicitly.
2. **Analysis (Perception -> Interpretation)**: Pass the combined customer context to `google/gemini-3.5-flash-lite`. Have the model analyze the timeline to interpret the customer's current intent and label generated hypotheses as hypotheses, not facts.
3. **NBA Generation**: Generate a concise "Next Best Action" string (e.g., "Trigger abandoned cart SMS for [Product]").
4. **Record Update**: In one transaction, deactivate the prior active persona row and insert the new `next_best_action` with an incremented `computed_version`, preserving the same tenant, profile, and archetype. Only use an `UPDATE` when the application explicitly accepts loss of version history; if so, include `tenant_id`, `master_profile_id`, `persona_archetype_id`, and `is_active = TRUE` in the predicate.

## Output
Return the complete Python script for this proposed extension, including data gathering, LLM inference, and the versioned persona update. Do not replace or bypass `PersonaResolutionEngine` without documenting the integration boundary. Return the full code with comprehensive comments.

## Test Cases
- **Test Case 1 (Churn Risk)**: Profile shows high value historically, but no transactions in 6 months. Expected NBA: "Dispatch win-back campaign with 20% discount offer."
- **Test Case 2 (Recent Engagement)**: Available raw event evidence shows a product-category browse today but no matching purchase. Expected NBA: "Deploy retargeting ad focusing on recently browsed category." If no browse event exists in `cdp_raw_profiles_stage`, the agent must say the evidence is unavailable rather than infer it.

## Code Impact Mapping

`customer360-backend/identity_resolution/identity_resolution/persona_engine.py` owns deterministic scoring, NBA calculation, versioned persona inserts, deactivation, and history. `cir_tasks.py` exposes `recompute_master_profile_persona`; `persona.py` optionally creates non-PII labels and summaries with Google Gemini or an offline fallback. This Gemini transaction/event reasoning loop is not implemented today. Integrate it as an explicit extension around the engine's versioned persistence path rather than issuing a standalone update to `cdp_customer_personas`.

```