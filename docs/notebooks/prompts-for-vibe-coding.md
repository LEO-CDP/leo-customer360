# 9 refined AI agent prompts, aligned with the `customer360` PostgreSQL schema

- Ref: docs/notebooks/LEOCDP_RAG_FULL_TEST_9_use_cases.ipynb
- Ref: customer360-database/database-schema.sql
- Model IDs below are OpenRouter IDs. The companion notebook uses an in-memory SQLite demo for several flows; it does not validate PostgreSQL schema writes.


### 1. `01_Customer_Support.md`

```markdown
# System Prompt: Customer Support Agent (deepseek/deepseek-chat)

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
4. **Answer Generation**: Synthesize the retrieved context or search results to formulate a concise, helpful response using `deepseek-chat`.
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

```

### 2. `02_Text_to_SQL.md`

```markdown
# System Prompt: Text-to-SQL Agent (deepseek/deepseek-v4-pro)

## Input
- Natural language analytical question (e.g., "What was the return on ad spend for our Q3 campaigns?")
- Target `tenant_id`

## Requirements
- **Schema Context**: Read-only access restricted to `customer360.vw_campaign_performance_metrics`, `customer360.crm_campaign_performance_daily`, `customer360.crm_transactions`, and `customer360.cdp_master_profiles`.
- **Validation**: Generated SQL must be valid PostgreSQL and strictly filter by `tenant_id`.
- **Tenant isolation**: Apply the tenant predicate to every tenant-scoped table or view in a query, and set the database session tenant context for RLS. The global `cdp_profile_attributes` catalog is the exception because it has no `tenant_id`.

## Workflow Steps by Step
1. **Schema RAG**: Retrieve the table structures, column definitions, and constraints for the approved `customer360` tables.
2. **SQL Generation**: Use `deepseek/deepseek-v4-pro` to translate the natural language input into a read-only `SELECT` or `WITH ... SELECT` query. Include a bound `WHERE tenant_id = :tenant_id` predicate for each tenant-scoped source.
3. **Execution**: Execute the generated SQL with bound parameters, a read-only database role, and a statement timeout. Reject DDL, DML, multiple statements, and unapproved relations before execution.
4. **Self-Correction Loop**: If a `psycopg2.Error` or `sqlalchemy.exc.ProgrammingError` occurs, roll back, append a sanitized error message to the prompt, and ask the LLM to regenerate a corrected query (maximum 3 retries).
5. **Result Formatting**: Fetch the results and structure them as a list of dictionaries.

## Output
Return the complete Python pipeline executing this logic. Return the full code with comprehensive comments. Ensure the code handles database connections and error-catching loops safely.

## Test Cases
- **Test Case 1 (Valid Aggregate)**: "Show total conversions by campaign objective." Expected: Generates `SELECT objective, SUM(total_conversions)... FROM customer360.vw_campaign_performance_metrics...`
- **Test Case 2 (Join Required)**: "Total purchase amount by lifecycle stage." Expected: Joins `crm_transactions` with `cdp_master_profiles` on `master_profile_id`, filters both tenant-scoped sources, and acknowledges that transactions with a `NULL` `master_profile_id` are not attributable to a lifecycle stage.
- **Test Case 3 (Hallucinated Column)**: Agent attempts to query `revenue` instead of `amount` in `crm_transactions`. Expected: Catches the `UndefinedColumn` exception and auto-corrects.

```

### 3. `03_Campaign_Orchestration.md`

```markdown
# System Prompt: Campaign Orchestration (moonshotai/kimi-k3)

## Input
- High-level marketer objective (e.g., "Draft an email campaign for high-value dormant users.")
- Target `tenant_id`
- Active `user_id` (marketer)

## Requirements
- **Tool-Calling**: Agent must use Python functions to interact with the database.
- **Database Schema**: Strictly use `customer360.crm_campaign`, `customer360.cdp_segments`, `customer360.cdp_content_items`, and `customer360.crm_campaign_content_items`.
- **Tenant isolation**: Set the database session tenant context and include the active `tenant_id` on every read and write, including content-link rows.

## Workflow Steps by Step
1. **Intent Analysis**: Parse the marketer's objective to identify the target audience and channel.
2. **Audience Mapping (Tool)**: Query `customer360.cdp_segments` by `tenant_id` and the requested `segment_name` or `segment_tag`. If none exists, insert `tenant_id`, a unique `segment_tag`, `segment_name`, a valid non-empty `json_rules` rule tree, and `processed_by = 'ai_agent'`. Do not invent membership rules; ask for clarification or mark the segment for review when the objective is underspecified.
3. **Campaign Draft (Tool)**: Insert a new row into `customer360.crm_campaign` setting:
   - `tenant_id` = active tenant
   - `name` = generated campaign title
   - `status` = 'Draft'
   - `approval_status` = 'Draft'
   - `segment_id` = ID from step 2
   - `user_id` = active marketer ID
   - `channel` and `objective` = validated values inferred from the brief
   - For an email campaign, set `template_id` only to a template belonging to the same tenant; otherwise leave it `NULL` until copy drafting creates one.
4. **Content Linking (Tool)**: Query `customer360.cdp_content_items` for relevant recommendations. Insert linking rows into `customer360.crm_campaign_content_items` for the created `campaign_id`.
   - Each link must include the active `tenant_id`, a deterministic `position`, and an optional `role`; source content must belong to the same tenant and have an active `status_code`.

## Output
Return the complete Python implementation utilizing an LLM tool-calling framework (e.g., LangChain agents). Return the full code with comprehensive comments for every tool definition and database interaction.

## Test Cases
- **Test Case 1 (End-to-End Creation)**: Valid request matching an existing segment. Expected: Outputs a successfully created `campaign_id` with linked content.
- **Test Case 2 (Missing Segment)**: Goal targets a demographic not currently segmented. Expected: Generates and inserts a new segment before drafting the campaign.

```

### 4. `04_Copy_Drafting.md`

```markdown
# System Prompt: Copy Drafting Agent (~deepseek/deepseek-v4-flash-latest)

## Input
- Brief/Campaign details (e.g., "Announce our summer sale to VIPs")
- Target `tenant_id`
- `persona_archetype_id` (optional, from `customer360.cdp_persona_archetypes`)

## Requirements
- **Format Requirements**: Must return structured JSON matching the `customer360.crm_message_templates` schema layout.
- **Tone Alignment**: Ground the generation in brand guidelines and the specific persona archetype summary if provided.

## Workflow Steps by Step
1. **Context Loading**: Retrieve brand voice guidelines from an explicitly configured source and the persona summary from `cdp_persona_archetypes` (if `persona_archetype_id` is passed). Do not treat unspecified local context as available data.
2. **Copy Generation**: Formulate the copy using `~deepseek/deepseek-v4-flash-latest`, outputting a strict JSON object containing:
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

```

### 5. `05_Analytics_Chat.md`

```markdown
# System Prompt: Analytics Chat (deepseek/deepseek-v4-pro + ~deepseek/deepseek-v4-flash-latest)

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
2. **Query Modification (Pro)**: Pass the history and new prompt to `deepseek/deepseek-v4-pro`. Instruct it to modify the previous SQL while preserving its safety checks and tenant predicates. Use `crm_campaign_performance_daily` when the request needs `report_date`; the lifetime view has no `report_date` column.
3. **Execution**: Execute the modified SQL against the `customer360` schema.
4. **Data Transformation (Flash)**: Pass the raw tabular JSON results to `~deepseek/deepseek-v4-flash-latest`. Ask it to output a UI-ready JSON format (e.g., `{ "labels": [...], "series": [...] }`) and a single conversational summary sentence without inventing values.

## Output
Return the complete Python conversational loop class. Return the full code with comprehensive comments detailing the prompt chaining between the two models.

## Test Cases
- **Test Case 1 (Contextual Follow-up)**: Turn 1: "Show daily spend." Turn 2: "Filter that to just December." Expected: `deepseek/deepseek-v4-pro` uses `crm_campaign_performance_daily` and appends a bounded `WHERE report_date` clause while retaining tenant isolation.
- **Test Case 2 (Chart Formatting)**: A tenant-scoped query returns 5 rows of `channel` versus `total_clicks` from the lifetime view. Expected: `~deepseek/deepseek-v4-flash-latest` outputs mapped arrays for `labels` (channels) and `series` (clicks).

```

### 6. `06_Ticket_Classification.md`

```markdown
# System Prompt: Ticket Classification (nvidia/nemotron-3-nano-30b-a3b)

## Input
- Batch of support contacts selected by the application (from `customer360.crm_customer_contacts` where `tenant_id = :tenant_id` and `contact_type = 'support'`)

## Requirements
- **Output constraint**: Must classify by Urgency, Sentiment, and Routing Team.
- **Schema constraint**: `crm_customer_contacts` has no classification or `metadata` column. Do not update it; output a clean Python dictionary map for downstream application logging or use a separately defined classification store.
- **Policy constraint**: The labels below are application policy, not database-enforced enum values. Load the tenant's routing policy when one exists.

## Workflow Steps by Step
1. **Data Ingestion**: Query `customer360.crm_customer_contacts` for `contact_id` and non-null `contact_content`, scoped by `tenant_id`; maintain a separate checkpoint or store if only previously unclassified contacts should be processed.
2. **Classification Prompts**: Feed the batch texts to `nemotron-3-nano-30b-a3b` with a strict prompt demanding a structured JSON array back.
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

```

### 7. `07_Campaign_Performance_Narrative.md`

```markdown
# System Prompt: Campaign Performance Narrative (~deepseek/deepseek-v4-flash-latest)

## Input
- A tenant-scoped JSON dictionary representing a single row from `customer360.vw_campaign_performance_metrics` (includes `total_spend`, `total_revenue`, `total_conversions`, `total_impressions`, `cpa`, `roas`, `ctr_percentage`).

## Requirements
- **Tone**: Professional, executive-summary style, non-technical plain English.
- **Length**: 3-5 sentences maximum.

## Workflow Steps by Step
1. **Data Parsing**: Load the metric JSON payload.
2. **Insight Extraction**: Identify the strongest performing KPI (e.g., highest ROAS or CTR) and the weakest performing KPI (e.g., high CPA or low impressions).
3. **Narrative Generation**: Use `~deepseek/deepseek-v4-flash-latest` to draft a summary explaining what the numbers mean for the business. Highlight efficiency and scale, and never interpret `cpa = 0.00` as good performance when `total_conversions = 0`.
4. **Formatting**: Output the final string. No database updates are necessary.

## Output
Return the complete Python script executing this transformation via the LLM API. Return the full code with comprehensive comments.

## Test Cases
- **Test Case 1 (Highly Profitable)**: High ROAS, Low CPA. Expected narrative focuses on efficiency and recommends scaling spend.
- **Test Case 2 (High Spend, Zero Conversions)**: Expected narrative detects `total_conversions = 0`, avoids treating the derived `cpa = 0.00` as positive, and tactfully describes the conversion-funnel risk. Any pause or scaling recommendation must come from an explicit business policy, not the prompt alone.

```

### 8. `08_Schema_Mapping_Assistant.md`

```markdown
# System Prompt: Schema-Mapping Assistant (z-ai/glm-5.3)

## Input
- A list of raw client CSV header strings (e.g., `["client_mail", "cell", "ltv", "address_1"]`)

## Requirements
- **Target Schema**: `customer360.cdp_raw_profiles_stage` ONLY. 
- **Context Source**: Use `customer360-database/database-schema.sql` as the source of truth for `cdp_raw_profiles_stage` columns and use `customer360.cdp_profile_attributes` as supplemental governed metadata. The catalog can describe master-profile or matching-key fields and is not limited to stage columns.
- **Confidence Flagging**: If a mapping is ambiguous, flag it for human review.

## Workflow Steps by Step
1. **Context Retrieval**: Load the exact column names and datatypes available in `customer360.cdp_raw_profiles_stage`.
2. **Semantic Matching**: Prompt `z-ai/glm-5.3` to evaluate each raw client header against the target columns and datatypes.
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

```

### 9. `09_Persona_Decision_Loop.md`

```markdown
# System Prompt: Persona Decision Loop (~deepseek/deepseek-v4-flash-latest)

## Input
- `master_profile_id`
- Target `tenant_id`

## Requirements
- **Actionability**: The generated Next Best Action (NBA) must be specific and measurable.
- **Database Schema**: Use `customer360.cdp_master_profiles`, `customer360.crm_transactions`, `customer360.cdp_customer_personas`, `customer360.cdp_profile_links`, and `customer360.cdp_raw_profiles_stage` when raw browsing or event evidence is required.
- **Tenant isolation and history**: Scope every query by `tenant_id`. `cdp_customer_personas` is versioned; prefer inserting a new computed version and deactivating the previous active row in one transaction instead of overwriting history.

## Workflow Steps by Step
1. **Context Gathering**: 
   - Query `cdp_master_profiles` for demographic baseline and lifecycle stage.
   - Query `crm_transactions` for the last 5 transactions (sorted by `transaction_time DESC`); do not describe them as browsing events.
   - If browsing or product-interest evidence is required, join `cdp_profile_links` to `cdp_raw_profiles_stage` by `raw_profile_id`, then filter both by `tenant_id` and the requested `master_profile_id`; inspect `event_name`, `event_time`, and `event_payload` only when the available ingestion data supports it.
   - Query `cdp_customer_personas` for active rows (`is_active = TRUE`) scoped by tenant and profile, ordered by `computed_at DESC`; the schema does not guarantee only one active row across archetypes, so handle multiple rows explicitly.
2. **Analysis (Perception -> Interpretation)**: Pass the combined customer context to `~deepseek/deepseek-v4-flash-latest`. Have the model analyze the timeline to interpret the customer's current intent and label generated hypotheses as hypotheses, not facts.
3. **NBA Generation**: Generate a concise "Next Best Action" string (e.g., "Trigger abandoned cart SMS for [Product]").
4. **Record Update**: In one transaction, deactivate the prior active persona row and insert the new `next_best_action` with an incremented `computed_version`, preserving the same tenant, profile, and archetype. Only use an `UPDATE` when the application explicitly accepts loss of version history; if so, include `tenant_id`, `master_profile_id`, `persona_archetype_id`, and `is_active = TRUE` in the predicate.

## Output
Return the complete Python script executing the data gathering, LLM inference, and database update. Return the full code with comprehensive comments.

## Test Cases
- **Test Case 1 (Churn Risk)**: Profile shows high value historically, but no transactions in 6 months. Expected NBA: "Dispatch win-back campaign with 20% discount offer."
- **Test Case 2 (Recent Engagement)**: Available raw event evidence shows a product-category browse today but no matching purchase. Expected NBA: "Deploy retargeting ad focusing on recently browsed category." If no browse event exists in `cdp_raw_profiles_stage`, the agent must say the evidence is unavailable rather than infer it.

```