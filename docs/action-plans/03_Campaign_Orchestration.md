# System Prompt: Agentic Campaign Orchestration UAT Implementation (openai/gpt-5.6-luna)

## Goal

Implement a tenant-safe, human-governed campaign workspace that uses a selected active AI-agent registry configuration to plan campaigns and lets marketers list, inspect, report on, create, edit, submit, approve, reject, and resubmit campaigns. Deliver three usable UAT surfaces:

1. A campaign table view to list, filter, open, create, and manage campaigns.
2. A single-campaign detail view with metadata, content plan, approval/audit timeline, and performance report.
3. A campaign editor that exposes every supported `crm_campaign` field and routes each change through the correct governed API path.

## Input

- Marketer objective or requested action, for example: `Draft an email campaign for high-value dormant users.`
- Target `tenant_id` from authenticated server-side tenant context; never accept a client-supplied tenant as authoritative.
- Active `user_id` from authenticated request context.
- Optional `campaign_id` for view, edit, approval, rejection, or report actions.
- Optional `agent_code` selecting the planning agent from `customer360.cdp_ai_agents`.
- Campaign form values, only for the action requested by the user.

## Non-Negotiable Requirements

- **Services only**: Use routers, services, and repositories. Do not issue raw application SQL or direct database writes from an LLM/tool handler.
- **Tenant isolation**: Set the database session tenant context and add an explicit `tenant_id` predicate to every tenant-owned read, write, join, and content-link operation. A campaign, segment, template, content item, metric, review, or audit row from another tenant must resolve exactly as not found.
- **Human governance**: The model may propose strategy and select only IDs from repository-provided candidate lists. It may never self-approve, set `approved_by`, set `approved_at`, or promote `approval_status`.
- **No invented data**: Do not invent a segment, approved template, content item, performance result, agent configuration, or campaign field value. Ask for a missing decision or return a structured blocked result.
- **Agent registry is global configuration**: `cdp_ai_agents` has no `tenant_id` and no campaign foreign key. Read it as managed runtime configuration; do not let a tenant edit it from the campaign editor. Validate its status and snapshot the chosen configuration into the campaign's JSONB provenance, rather than inventing a relation table or a new foreign key.
- **Embedding is server-owned**: `crm_campaign.embedding` is a `vector(1536)` retrieval field. Do not accept it from the UI or LLM and do not include it in normal API responses. Generate or refresh it only in a trusted server-side indexing path from approved campaign text.
- **Optimistic concurrency**: All governed edits must check `updated_at`; return `409 Conflict` with the current version when a stale editor attempts a write.

## Authoritative Data Contract

### `customer360.crm_campaign`

Use every campaign column according to its ownership and mutability. The UI labels below map directly to persistence fields.

| Field group | Columns | UAT behavior |
|---|---|---|
| Identity and ownership | `campaign_id`, `tenant_id`, `user_id`, `campaign_code`, `created_at`, `updated_at` | Server generates `campaign_id` and timestamps. Derive `tenant_id` and default `user_id` from authentication; allow owner reassignment only when the existing CRM authorization policy permits it. Require `campaign_code` to be unique within the tenant and show it in list/detail views. |
| Core campaign metadata | `name`, `status`, `channel`, `platform`, `objective`, `description`, `keywords`, `lang` | Expose editable controls with validation. Treat `status` as the operational lifecycle label, distinct from approval. Render `keywords` as a string array and use an explicit language value. |
| Schedule and budget | `start_date`, `end_date`, `budget_amount`, `currency` | Use date and numeric/currency inputs. Reject an end date before its start date. Do not let the planner silently replace marketer-entered budget or dates. |
| Audience and creative dependencies | `segment_id`, `template_id` | Resolve only tenant-owned records. A segment must be active with `status_code == 1`; email requires an `Approved` template. For ZNS, use the closed candidate list of tenant-owned Approved ZNS templates. |
| AI planning and approval | `strategy_summary`, `ai_plan`, `approval_status`, `approved_by`, `approved_at` | `strategy_summary` and `ai_plan` are governed draft fields. `approval_status` transitions only through the review service: `Draft` -> `InReview` -> `Approved` or `Rejected`; an edited approved/rejected campaign returns to `InReview`. Only a tenant admin can approve or reject. |
| Extensible provenance | `metadata` | Store validated, namespaced JSON only. Reserve `metadata.agent_provenance` for the immutable selected-agent snapshot and `metadata.editor_context` for supported UI-only extension data. Reject unknown top-level metadata shapes instead of treating it as an unbounded client payload. |
| Retrieval | `embedding` | Server-only; exclude from browser and LLM payloads as described above. |

### `customer360.cdp_ai_agents`

Before planning, load exactly one selected agent by `agent_code`. It must be `status = 'ACTIVE'` and `model_type = 'generative_llm'`. Read and use all registry metadata as follows:

| Agent metadata | Required orchestration behavior |
|---|---|
| `agent_code`, `display_name`, `description` | Identify the configured planner in UI/API output and explain its intended role. |
| `model_type`, `model_name`, `status` | Enforce the type and active status above; invoke only the registered `model_name`. The configured model must be one of `openai/gpt-5.6-luna` or `google/gemini-3.5-flash-lite`; otherwise return a configuration-blocked result. |
| `schedule_definition` | Display it as configuration metadata. Do not turn it into a campaign dispatch schedule or execute it unless a separately implemented scheduler owns that behavior. |
| `input_features`, `required_variables` | Validate that the assembled planning context supplies every declared value before invocation. For campaign planning, the context must include tenant-safe segment, template, objective, date/budget constraints, candidate content, and current campaign values when editing. |
| `hyperparameters` | Pass only validated provider-supported values to the registered model client. Never merge arbitrary UI JSON into provider parameters. |
| `prompt_key`, `prompt_engine`, `system_instructions`, `instruction_version`, `instruction_updated_by`, `instruction_note`, `prompt_versions` | Resolve the current prompt by `prompt_key`, render it with the supported `prompt_engine` (`none` is currently supported), use `system_instructions` at `instruction_version`, and preserve the append-only version history. Do not mutate the registry during campaign planning. |
| `created_at`, `updated_at` | Display them in the agent provenance and preserve them in the selected-agent snapshot. |

On every planned campaign, persist this complete immutable snapshot under `crm_campaign.metadata.agent_provenance`: all `cdp_ai_agents` columns above, the resolved prompt version, and a server-generated `run_at` timestamp. Also record the generated structured plan in `ai_plan`; do not put secrets, provider credentials, or unrestricted prompts into client-editable metadata.

## Workflow

1. **Authorize and load state**: Resolve `tenant_id` and `user_id` from authentication, set the tenant database session context, and load an existing campaign with both `campaign_id` and `tenant_id` when applicable.
2. **Select and validate the planner**: Load the requested `cdp_ai_agents` record. Confirm its active generative-LLM configuration, registered model name, required variables, prompt state, and supported hyperparameters. Return a structured `agent_configuration_invalid` error when any check fails.
3. **Resolve campaign dependencies**: Query tenant-owned segments, templates, and active content through their existing services. A missing or unresolved segment blocks planning. Do not create a segment from prose in this workflow; delegate it to the segment JSON-rules flow and wait for its tenant-scoped recompute before retrying.
4. **Build a closed planning context**: Give the planner only validated campaign inputs plus a closed candidate list of tenant-owned active `cdp_content_items`. Require the model to return a typed plan containing `name`, `objective`, `strategy_summary`, `action_plan`, dates, and selected candidate IDs only. Preserve explicit marketer form values unless the user asks the planner to propose them.
5. **Persist a governed draft**: Use the existing campaign-draft repository to validate dependencies, save content links with deterministic positions/roles, initialize `status = 'Draft'`, set `approval_status = 'InReview'`, persist `ai_plan`, and write `metadata.agent_provenance`. Create a tenant-scoped audit event.
6. **Edit safely**: Split form changes by ownership. General fields (`campaign_code`, name, status, channel, platform, description, keywords, lang, dates, budget, currency, approved metadata extension keys) use a guarded campaign update service. Governed fields (`segment_id`, `template_id`, objective, strategy summary, AI plan, schedule change that affects review, and content items) use the dedicated draft-edit service, revalidate dependencies, update audit history, and resubmit the campaign to `InReview` when a material change affects an approved or rejected campaign. Never use generic PATCH to alter approval fields or bypass audit/review behavior.
7. **Review**: A tenant admin may approve only an `InReview` campaign after the service revalidates its template and selected content. A rejection records the optional reason. Both decisions appear in the campaign timeline and update `updated_at`.
8. **Report**: Read a single tenant-scoped campaign, `crm_campaign_performance_daily`, and `vw_campaign_performance_metrics`. Return daily rows plus lifetime totals: spend, impressions, clicks, conversions, estimated revenue, CTR, CVR, CPA, and ROAS. When conversions are zero, report CPA as unavailable or derived zero with an explicit zero-conversion warning; never describe it as efficient performance.

## API Contract for UAT

Keep the existing API prefixes and extend the service/repository boundary where the current contracts do not cover the UAT flow.

| UAT capability | API contract |
|---|---|
| Campaign table | `GET /api/v1/campaigns/analytics` for tenant-scoped, paginated performance rows with search, status, channel, platform, objective, sorting, and pagination. Extend the table data with `campaign_id`, `campaign_code`, owner, schedule, budget/currency, and `approval_status` through a repository-backed response, not client-side joins. |
| Create a planned draft | `POST /api/v1/campaigns/draft` for email and `POST /api/v1/campaigns/zalo-draft` for ZNS. Extend request/response models to carry the chosen `agent_code` and server-generated `agent_provenance`; do not accept a client-provided provenance snapshot. |
| Campaign record | Keep tenant-scoped `GET /api/v1/campaigns/{campaign_id}` for all non-embedding campaign metadata and ordered content items. Keep generic create/update limited to fields it can safely own. |
| Governed editor | Extend `PATCH /api/v1/campaigns/{campaign_id}/draft` and its repository request schema to cover the protected campaign fields listed above, content items, agent re-planning, and an `updated_at` precondition. Do not use the generic endpoint for those mutations. |
| Review and history | Use `POST /api/v1/campaigns/{campaign_id}/approve`, `POST /api/v1/campaigns/{campaign_id}/reject`, and `GET /api/v1/campaigns/{campaign_id}/history`. |
| Single-campaign report | Add `GET /api/v1/campaigns/{campaign_id}/report?start_date=&end_date=` backed by a tenant-scoped repository method. Return campaign identity, current metadata, lifetime metrics, daily metrics, date filter coverage, content plan summary, and approval/audit summary. Do not reuse the aggregate dashboard endpoint for a single campaign. |
| Agent selection | Read `GET /api/v1/ai-agents?status=ACTIVE&model_type=generative_llm` and `GET /api/v1/ai-agents/{agent_code}`. Registry mutation remains in the AI-agent administration surface, not campaign routes. |

All write responses return the full non-embedding campaign representation, ordered content items, `updated_at`, `approval_status`, and immutable agent provenance. Return `404` for cross-tenant IDs, `409` for stale updates or blocked transitions, and `422` for invalid form values.

## UAT UI Requirements

### 1. Campaign Table and Management

- Extend the existing `/campaigns` dashboard rather than creating a second competing campaign list.
- Show campaign code/name, operational status, approval status, channel/platform, objective, owner, schedule, budget/currency, segment/template availability, agent display name plus instruction version, and lifetime KPI columns.
- Support server-side search, filters, sort, pagination, refresh, create-planned-draft, open-detail, edit, submit/replan, approve, and reject actions. Display only actions authorized for the user and current lifecycle state.
- A row click must open the campaign detail route; it must not only write to the browser console.
- Use explicit loading, empty, unauthorized, conflict, and dependency-blocked states. Never infer approval from the operational `status` badge.

### 2. Campaign Detail and Report

- Provide a stable detail route such as `/campaigns/{campaign_id}`.
- Render tabs or equivalent views for Overview, Strategy, Content Plan, Performance Report, and History.
- Overview shows all non-embedding `crm_campaign` metadata grouped as identity, targeting, execution, budget, approval, and agent provenance.
- Strategy renders `strategy_summary`, typed `ai_plan`, the selected agent snapshot, prompt/instruction version, model, required-variable readiness, and provenance timestamp as read-only.
- Performance Report calls the single-campaign report endpoint and renders date-filtered daily spend/impressions/clicks/conversions/revenue plus lifetime CTR, CVR, CPA, and ROAS. Preserve zero-conversion warnings.
- History renders ordered audit and review events, including before/after values where authorized and rejection reasons.

### 3. Campaign Editor

- Provide a create/replan flow with objective, approved segment/template selection, agent selection, optional budget/time constraints, and closed candidate-content selection.
- Provide a standard metadata form for editable general fields and a separately identified governed section for audience/template, strategy, schedule, content plan, and re-planning. Explain blocked fields with API validation errors, not silent disabling.
- Render `metadata` as schema-constrained, namespaced extension data. Render agent provenance and `ai_plan` read-only; neither can be manually overwritten.
- Use `updated_at` as a precondition on save. On `409`, retain unsaved form values and require the user to reload or reconcile.
- Do not expose `tenant_id`, `campaign_id`, `approved_by`, `approved_at`, or `embedding` as editable inputs.

## Output

Return a production-ready implementation plan and code changes for the API, repository/service, Pydantic schemas, frontend route/template/view, and focused tests. Include only the registered model policy values `openai/gpt-5.6-luna` and `google/gemini-3.5-flash-lite`. Reuse the existing `CampaignDraftRepository`, CRM repository, AI-agent repository, tenant middleware, audit logging, and current frontend campaign dashboard patterns. Do not add a new framework or duplicate CRUD path.

## UAT Acceptance Tests

- **Table management**: A marketer sees only their tenant's campaigns. Filters and sorting are server-side; clicking a row opens the matching tenant-owned detail page; a cross-tenant campaign ID returns `404`.
- **Agent configuration**: Selecting an active generative planner persists a complete immutable agent snapshot containing every `cdp_ai_agents` field. An inactive, non-generative, malformed-prompt, missing-variable, or unsupported-model agent blocks planning without creating a campaign.
- **Email draft creation**: A valid active segment, Approved tenant template, candidate content, and selected planner create an `InReview` draft with ordered content links, `ai_plan`, provenance, audit event, and no client-controlled approval data.
- **ZNS draft creation**: The service chooses only from the closed list of Approved tenant ZNS templates and persists `channel = 'zalo_zns'`; an unavailable template blocks the draft.
- **Editor coverage**: The editor can save every supported non-embedding campaign field through the correct API. A material governed edit to an approved/rejected campaign records before/after audit data and returns it to `InReview`; a stale `updated_at` returns `409`.
- **Approval**: A non-admin cannot approve/reject. An admin cannot approve a campaign whose template or content is no longer valid. Approval and rejection appear in detail history with the reviewer and time.
- **Single-campaign report**: The report contains tenant-scoped daily and lifetime metrics. A campaign with zero conversions displays the zero-conversion warning and does not label `CPA = 0.00` as good performance.
- **No unsafe paths**: The browser never receives `embedding`, provider credentials, or mutable agent provenance. No planner tool issues raw database writes or creates a caller-controlled tenant predicate.

## Code Impact Mapping

- `customer360-database/database-schema.sql` is the source of truth for `crm_campaign`, `crm_campaign_performance_daily`, `vw_campaign_performance_metrics`, and `cdp_ai_agents`.
- `customer360-dao/src/leo_customer360_dao/models/crm.py` and `schemas/crm.py` own campaign ORM/Pydantic contracts; extend the governed draft schemas instead of opening approval fields in generic updates.
- `customer360-dao/src/leo_customer360_dao/models/identity.py` and `schemas/system.py`, plus `customer360-api/core/routers/ai_agent_api.py`, expose the global AI-agent registry.
- `customer360-api/core/routers/campaign_draft_api.py` and `core/repositories/campaign_draft_repository.py` own planning, protected edits, content selection, review transitions, concurrency, and audit history.
- `customer360-api/core/routers/crm_api.py` and `core/repositories/crm_repository.py` own generic campaign CRUD and analytics; add the single-campaign report through this repository boundary with tenant scoping.
- `customer360-frontend/static/js/campaign-view.js` and `static/templates/campaign/campaign-dashboard.html` are the existing campaign list/dashboard. Extend them with management actions and a real detail route/editor; do not build a disconnected parallel campaign UI.

## Repository Readiness TODO

Review date: 2026-09-30. `OK` means the current repository already provides the required behavior. `DONE` means the requested frontend implementation is now present and wired. `TODO` means implementation or focused UAT coverage is still required.

| Status | Area | Current repository evidence | Required next step |
|---|---|---|---|
| OK | `crm_campaign` persistence model | DAO model includes campaign identity, ownership, operational metadata, schedule, budget, audience/template links, approval fields, AI plan, metadata, timestamps, and server-only embedding mapping. | Keep the model contract; do not expose `embedding` in browser payloads. |
| OK | `cdp_ai_agents` registry | DAO model, Pydantic schemas, repository, and `/ai-agents` CRUD endpoints expose the registry metadata and prompt-version fields. | Keep registry administration separate from campaign editing. |
| OK | Email closed candidate planning | `CampaignDraftRepository.create_draft` supplies active tenant content candidates and validates returned content IDs before persistence. | Preserve the candidate allowlist and add agent provenance around it. |
| OK | ZNS closed template planning | `create_zns_draft` selects only tenant-owned Approved ZNS templates and the planner rejects off-list templates or missing required parameters. | Preserve this validation in the UAT flow. |
| OK | Approval state machine | Dedicated draft endpoints and repository implement `Draft`/`InReview`/`Approved`/`Rejected`, admin-only approval/rejection, dependency revalidation, and review rows. | Add missing UI actions and end-to-end UAT coverage. |
| OK | Audit and concurrency primitives | Draft edits/reviews write `sys_audit_log` or `crm_campaign_reviews`; draft edits use `updated_at` conflict checks. | Extend the same guarantees to every newly editable field. |
| TODO | Aggregate campaign dashboard analytics | DAO repository provides tenant predicates, pagination, filters, sorting, and charts, but `/campaigns/analytics` and related routes accept `tenant_id` from the query string instead of deriving it from authenticated request context. | Derive tenant identity from authentication, retain explicit repository predicates, and then extend list rows with management metadata and approval/agent information. |
| TODO | Basic campaign CRUD/detail response | Generic `/campaigns` CRUD returns `CampaignRead` and attaches ordered content items, but the generic handlers do not add explicit tenant predicates to item get/update/delete operations and creation accepts `tenant_id` in the body. | Add an authenticated tenant-scoped campaign service/repository boundary before calling this UAT-ready; keep approval fields on dedicated draft routes and add the planned detail/report route. |
| TODO | Agent-aware orchestration | Draft requests do not accept `agent_code`; planner resolution uses the generic configured provider/prompt path and does not load or validate `cdp_ai_agents`. | Add server-side agent selection, active/model/prompt/variable/hyperparameter validation, and structured configuration errors. |
| TODO | Agent provenance snapshot | Campaign creation does not persist `metadata.agent_provenance`; draft responses do not return the selected agent snapshot or prompt version. | Persist an immutable, secret-free snapshot of every `cdp_ai_agents` field plus resolved prompt version and `run_at`. Never accept this snapshot from the client. |
| TODO | Registered model policy | Current provider resolution accepts a model override and does not enforce the action-plan allowlist of `openai/gpt-5.6-luna` and `google/gemini-3.5-flash-lite`. | Resolve the model only from the validated agent registry and reject unsupported models before creating a campaign. |
| TODO | Complete campaign draft request | `CampaignDraftRequest` only accepts `segment_id`, `template_id`, `objective`, and budget/time text. It cannot carry the selected agent or explicit campaign metadata values. | Extend the request/service contract without allowing client approval fields or tenant overrides. |
| TODO | Complete governed editor | `EditCampaignDraftRequest` only supports objective, strategy summary, dates, and content items. It cannot safely edit campaign code/name/status/channel/platform/description/keywords/lang/budget/currency, segment/template, metadata, or re-planning configuration as required. | Split general and governed fields into guarded service operations, validate all fields, preserve audit snapshots, and require `updated_at`. |
| DONE | Governed campaign segment replacement | `PATCH /campaigns/{campaign_id}/draft` now accepts an explicit `segment_id`, validates tenant ownership and active/computed readiness, records before/after segment names and tags in audit history, clears the stale strategy plan, and forces re-review. | Extend the same governed contract to remaining protected fields such as template and agent selection. |
| DONE | A/B experiment definition and comparison | DAO models, migration, tenant-scoped API routes, variant allocation/control validation, winner/status updates, variant-attributed performance aggregation, and campaign detail UI are implemented. | Add activation-time traffic assignment and ingestion guarantees so performance rows receive `experiment_variant_id` automatically. |
| TODO | Tenant source of truth for analytics | Analytics routes accept `tenant_id` as a query parameter even though the action plan requires authenticated server-side tenant context. | Derive tenant identity from request authentication/middleware and retain explicit repository predicates; add regression tests for a caller-supplied foreign tenant. This is also a blocker for the dashboard `OK` claim above. |
| TODO | Single-campaign report API | No `GET /campaigns/{campaign_id}/report` exists; DAO analytics only expose aggregate dashboard queries and global spend trends. | Add a tenant-scoped repository method and response containing campaign metadata, lifetime metrics, filtered daily metrics, content summary, and audit/approval summary. |
| TODO | Report zero-conversion semantics | The database view returns `cpa = 0.00` when conversions are zero, and no single-campaign report currently adds a warning. | Add an explicit `zero_conversion_warning`/availability field and ensure UI copy never describes zero CPA as good performance. |
| DONE | Campaign table management UI | `campaign-dashboard.html` now exposes campaign creation and management entry points; `campaign-view.js` navigates rows to detail and edit routes and retains server-side filters, sorting, pagination, and refresh. | Complete the remaining API response enrichment and browser UAT coverage when the backend report/editor contracts are available. |
| DONE | Campaign row navigation | `onRowClick` now navigates to `/campaigns/{campaign_id}` and the row edit action navigates to `/campaigns/{campaign_id}/edit`. | Add browser UAT coverage for navigation and authorization states. |
| DONE | Campaign detail/report UI | `campaign-details.html` now provides Overview, Strategy, Content Plan, Performance Report, and History panels; `campaign-view.js` loads campaign metadata, history, approval actions, and available aggregate analytics. | Replace aggregate fallback with the dedicated daily report endpoint when implemented and add browser UAT coverage. |
| DONE | Full campaign editor UI | `campaign-editor.html` now provides create/edit metadata and governed-plan sections; `campaign-view.js` wires create and existing-campaign saves through the current generic and draft APIs. | Complete backend support for full governed-field updates, agent selection, and optimistic-concurrency preconditions, then add browser UAT coverage. |
| TODO | List/detail API response completeness | Analytics rows contain performance-view fields only; draft response omits general campaign metadata, `metadata`, and agent provenance. | Define response schemas that expose all approved non-embedding fields needed by table/detail/editor without leaking secrets or embeddings. |
| TODO | Agent integration tests | `customer360-api/tests/test_ai_agents.py` covers registry CRUD, but campaign draft tests do not cover agent selection, invalid registry state, required variables, model policy, or provenance. | Add API/repository tests for valid and blocked agent planning and immutable provenance. |
| TODO | Report/API tests | Existing campaign tests cover CRUD, draft lifecycle, approval, rejection, history, and tenant isolation, but no single-campaign report contract exists. | Add tests for daily date bounds, lifetime totals, zero conversions, foreign campaign IDs, and report response shape. |
| TODO | Frontend UAT tests | Current frontend code has dashboard rendering only; no tests cover row navigation, detail/editor interactions, authorization states, or conflict handling. | Add browser/component UAT coverage for table management, detail/report, editor save, approval, rejection, and stale-update recovery. |

### Recommended UAT Delivery Order

1. **TODO** Agent-aware service contract, registry validation, provenance snapshot, and API tests.
2. **TODO** Complete governed campaign editor API, full response schemas, and single-campaign report API.
3. **DONE** Extend table management actions and implement detail/report/editor routes and templates.
4. **TODO** Add frontend UAT tests and run the existing campaign lifecycle, tenant-isolation, and activation suites together.

