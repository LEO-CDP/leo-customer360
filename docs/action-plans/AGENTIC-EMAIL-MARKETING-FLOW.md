# Agentic Outbound Email Marketing Execution Engine

## Summary

This plan defines the v2.0 Beta path for a complete marketer workflow:

1. Select a segment by `segment_id` from PostgreSQL and sync matched profiles to the correct `crm_*` tables.
2. Create an email template draft with AI agents (Gemini or OpenAI) and human review.
3. Create a campaign draft with AI strategy and objectives, attach schedule and content plan, then require human approval before run.

Final business outcome:

Segment -> CRM sync routing -> AI template draft -> AI campaign draft -> Human approval -> Dispatch -> Track -> Feedback into Customer 360.

## Verified Ground Truth (Repo Audit)

### Database schema reality (`database-init/database-schema.sql`)

- `cdp_segments` exists and supports segment selection by `segment_id`.
- `cdp_master_profiles` exists and carries `lifecycle_stage` (`prospect`, `lead`, `customer`, `vip`, `dormant`, `churn_risk`) and personalization fields.
- CRM targets exist:
  - `crm_customer_contacts`
  - `crm_transactions`
  - `crm_lead`
  - `crm_lead_source`
  - `crm_contact`
  - `crm_campaign`
- `cdp_content_items` exists and is usable as campaign content inventory.

Schema gaps that must be addressed in this story:

- `crm_lead` currently has no `lead_source_id` foreign key.
- `crm_campaign` currently has no `segment_id`, `template_id`, or campaign-content relation table.
- No dedicated email-template table exists yet.

### Dagster service reality (`backend-system/*/dagster_defs.py`)

Implemented and active:

- `identity_resolution` (`identity_resolution_job` + sensor)
- `segmentation` (`segmentation_job` + change-driven polling sensor)
- `analytics` (`analytics_job` + hourly schedule)

Placeholders (log + sleep only):

- `campaign_activation`
- `email_engine`
- `data_synch`
- `scoring`
- `notification_engine`
- `personalization`

Implication: this story must convert `campaign_activation` and `email_engine` to real jobs, and use existing `segmentation` and `analytics` services as dependencies.

## Target Marketer Flow (Required)

1. Marketer selects `segment_id`.
2. System recomputes segment membership and freezes a run snapshot.
3. System syncs matched profiles to `crm_*` by lifecycle routing rules:
   - Customer route: write/update `crm_customer_contacts` and `crm_transactions`.
   - Lead route: write/update `crm_lead` and `crm_lead_source`.
   - Contact route (not customer and not lead): write/update `crm_contact`.
4. AI agent creates email template draft (Gemini or OpenAI).
5. AI agent creates campaign draft in `crm_campaign` with objective, strategy, `start_date`, `end_date`, and selected content from `cdp_content_items`.
6. Human review/approval gate moves campaign from `Draft` to executable states.
7. Dispatch runs via real Dagster jobs.
8. Tracking, webhooks, and analytics feed Customer 360 feedback loop.

## Story-level Acceptance Criteria

- Segment can be selected by `segment_id` and recomputed before sync.
- Routing rules are enforced exactly:
  - `customer` -> `crm_customer_contacts` + `crm_transactions`
  - `lead` -> `crm_lead` + `crm_lead_source`
  - neither `customer` nor `lead` -> `crm_contact`
- AI can produce email template drafts using Gemini or OpenAI, with required compliance placeholders.
- AI can produce campaign drafts with objective, schedule (`start_date`, `end_date`), and content plan from `cdp_content_items`.
- Campaigns created by AI remain `Draft` until explicit human approval.
- Dispatch is idempotent, auditable, tenant-safe, and traceable end to end.
- E2E automation is based on the same verification pattern already used in simulator code.

## Subtasks (P0 Sprint - 8 Blockers)

### SUBTASK-01: Schema and Migration Foundation

Component: `database-init/migrations`  
Priority: P0 Blocker  
Depends on: none  
Blocks: SUBTASK-02..08  
Estimate: 5 pts

Description

Add missing relational structure so your target workflow can be implemented without metadata-only hacks.

Scope of Work

- Create `crm_email_templates` with at least:
  - `template_id`, `tenant_id`, `name`, `subject`, `html_body`, `text_body`, `variables`, `status`, `created_by`, `approved_by`, `approved_at`, `metadata`.
- Extend `crm_campaign` with:
  - `segment_id` FK -> `cdp_segments.segment_id`
  - `template_id` FK -> `crm_email_templates.template_id`
  - `approval_status` (`Draft`, `InReview`, `Approved`, `Rejected`)
  - `approved_by`, `approved_at`
  - `strategy_summary` (text) and optional `ai_plan` (jsonb)
- Add `lead_source_id` FK to `crm_lead` referencing `crm_lead_source`.
- Add campaign-content relation table, e.g. `crm_campaign_content_items`:
  - `campaign_id` FK
  - `content_item_id` FK
  - `position`, `role`, `metadata`
- Add sync-run audit table, e.g. `crm_segment_sync_runs`, to track each segment sync execution and counts per routing bucket.
- Add/extend indexes and RLS policies for all new tenant-scoped tables.

Acceptance Criteria

- Migrations apply and rollback cleanly.
- New FKs enforce consistency for lead-source and campaign-template/segment/content links.
- RLS policies exist for all new tenant-scoped tables.
- SQLAlchemy models and Pydantic schemas are updated.

Definition of Done

Schema supports direct implementation of your three goals with no missing columns/FKs.

### SUBTASK-02: Segment-ID Driven CRM Sync Engine

Component: `customer360-api/core` + `backend-system/data_synch`  
Priority: P0 Blocker  
Depends on: SUBTASK-01  
Blocks: SUBTASK-03, SUBTASK-05, SUBTASK-08  
Estimate: 8 pts

Description

Implement the exact lifecycle routing behavior you requested from one selected `segment_id`.

Scope of Work

- API endpoint to trigger sync for one segment:
  - `POST /api/v1/admin/crm/sync-segment/{segment_id}`
  - optional dry-run mode returning counts only.
- Recompute selected segment first (reuse segmentation recompute behavior), then resolve members from `cdp_master_profiles`.
- Routing rules:
  - Route A (customer): `lifecycle_stage = 'customer'` -> sync into `crm_customer_contacts` and `crm_transactions`.
  - Route B (lead): `lifecycle_stage = 'lead'` -> sync into `crm_lead` and `crm_lead_source`.
  - Route C (contact): all other lifecycle stages -> sync into `crm_contact`.
- Customer route behavior:
  - Build/update `crm_customer_contacts` from eligible interaction signals.
  - Build/update `crm_transactions` from available transaction facts.
  - Do not fabricate transaction amounts when no source data exists.
- Lead route behavior:
  - Upsert `crm_lead_source` from `acquisition_source` (or configured fallback source).
  - Upsert `crm_lead` with mapped profile identity fields.
  - Populate `lead_source_id` relation.
- Contact route behavior:
  - Upsert `crm_contact` for non-lead/non-customer profiles.
- Idempotency:
  - Re-running same segment sync run cannot duplicate target facts.
- Persist sync-run metrics in `crm_segment_sync_runs`.

Acceptance Criteria

- One selected `segment_id` syncs successfully with deterministic per-route counts.
- Routing rules match requested behavior exactly.
- Sync is idempotent for retries/replays.
- Tenant isolation is enforced end to end.

Definition of Done

Marketer can select a segment and synchronize profile subsets into the expected `crm_*` tables with audit evidence.

### SUBTASK-03: AI Email Template Authoring (Gemini/OpenAI)

Component: `customer360-api/core` + `backend-system/email_engine`  
Priority: P0 Blocker  
Depends on: SUBTASK-01, SUBTASK-02  
Blocks: SUBTASK-05, SUBTASK-08  
Estimate: 5 pts

Description

Create AI-assisted template generation with human governance.

Scope of Work

- Add template generation endpoint/service with model provider selection:
  - Gemini provider
  - OpenAI-compatible provider
- Input contract includes:
  - target segment context
  - objective
  - tone/brand constraints
  - language and locale
- AI output must generate:
  - subject
  - html body
  - text body
  - required placeholders (`unsubscribe_url`, name fields)
- Save templates in `crm_email_templates` with initial `Draft` status.
- Add review API:
  - approve/reject/edit template before campaign use.
- Add basic prompt and output safety checks (length, forbidden claims, HTML safety).

Acceptance Criteria

- Template drafts can be generated through Gemini or OpenAI path.
- Draft templates are persisted and versionable.
- Human approval is required before template can be attached to runnable campaign.

Definition of Done

AI template generation works with strict draft-and-review governance.

### SUBTASK-04: AI Campaign Strategy and Draft Creation

Component: `customer360-api/core` + `backend-system/campaign_orchestration`  
Priority: P0 Blocker  
Depends on: SUBTASK-01, SUBTASK-02, SUBTASK-03  
Blocks: SUBTASK-05, SUBTASK-08  
Estimate: 8 pts

Description

AI should create campaign drafts with objective, strategy, schedule, and content linkage; humans approve before activation.

Scope of Work

- Add campaign planning endpoint/service that takes:
  - `segment_id`
  - approved `template_id`
  - marketer objective
  - budget/time constraints
- AI proposes:
  - campaign name and objective
  - strategy summary and action plan
  - schedule window (`start_date`, `end_date`)
  - recommended content set from `cdp_content_items`
- Create campaign row in `crm_campaign` with:
  - status `Draft`
  - approval status `InReview` (or equivalent)
  - linked `segment_id`, `template_id`
- Persist selected content relations in `crm_campaign_content_items`.
- Enforce state machine:
  - AI can create only `Draft`.
  - only authorized human can approve and transition to `Scheduled`/`Running`.

Acceptance Criteria

- AI-created campaign drafts include objective, schedule, and content plan from `cdp_content_items`.
- Draft campaigns cannot run without human approval.
- State transitions are validated and audited.

Definition of Done

AI can prepare complete campaign drafts; human approval remains mandatory gate to execution.

### SUBTASK-05: Dagster Execution Modernization (Campaign + Email)

Component: `backend-system/campaign_activation` + `backend-system/email_engine`  
Priority: P0 Blocker  
Depends on: SUBTASK-02, SUBTASK-03, SUBTASK-04  
Blocks: SUBTASK-06, SUBTASK-08  
Estimate: 8 pts

Description

Replace placeholder jobs with real executable pipelines while preserving current working services.

Scope of Work

- Replace `campaign_activation_job` placeholder with real orchestration:
  - validate approval status
  - load campaign, segment snapshot, template
  - hand off to email dispatch flow
- Replace `email_engine_job` placeholder with real send pipeline:
  - render per recipient
  - dispatch via SMTP/SES adapter
  - write `cdp_campaign_dispatch_logs`
- Keep integration points with existing real services:
  - use `segmentation_job` outputs for selected segment freshness
  - preserve compatibility with `analytics_job` downstream verification
- Keep other placeholder services explicitly out of this sprint scope:
  - `data_synch`, `scoring`, `notification_engine`, `personalization`

Acceptance Criteria

- `campaign_activation` and `email_engine` run real logic, not sleep placeholders.
- Runs are observable in Dagster and retry-safe.
- Dispatch idempotency and failure handling are implemented.

Definition of Done

Email campaign execution path is production-like and orchestrated through real Dagster jobs.

### SUBTASK-06: Tracking, Webhooks, and Compliance Feedback

Component: `customer360-api/core/routers` + `data-tracking-api`  
Priority: P0 Blocker  
Depends on: SUBTASK-05  
Blocks: SUBTASK-07, SUBTASK-08  
Estimate: 5 pts

Description

Capture delivery and engagement events and feed suppression/compliance updates.

Scope of Work

- Add tracking endpoints for open pixel and click redirect.
- Add webhook endpoints for provider delivery, bounce, complaint, open, click.
- Correlate events to `campaign_id` and profile identity.
- Normalize to event catalog and update suppression on hard bounce/complaint.
- Apply dedup behavior for repeated callbacks.

Acceptance Criteria

- Open and click are captured with correct campaign/profile correlation.
- Bounce/complaint immediately affects suppression and future eligibility.
- Duplicate callbacks do not inflate metrics.

Definition of Done

Closed-loop event capture and compliance suppression updates are operational.

### SUBTASK-07: Customer 360 Feedback and Performance Rollups

Component: `backend-system` + `customer360-api` + `analytics`  
Priority: P0 Blocker  
Depends on: SUBTASK-06  
Blocks: SUBTASK-08  
Estimate: 5 pts

Description

Ensure engagement updates profiles and campaign analytics in measurable ways.

Scope of Work

- Update profile touchpoint timestamps from email events.
- Trigger segmentation refresh behavior after relevant profile updates.
- Roll up email metrics into campaign performance surfaces.
- Keep tenant-safe and auditable data changes.

Acceptance Criteria

- Profile timestamps update correctly after send/open/click events.
- Segment refresh is triggered by relevant profile changes.
- Campaign performance views include email metrics.

Definition of Done

Email engagement data becomes actionable in profile and campaign analytics.

### SUBTASK-08: End-to-End Automated Test Suite (Learn from Simulator)

Component: cross-cutting (`all-data-simulator`, `backend-system`, `customer360-api`)  
Priority: P0 Blocker (Beta Gate)  
Depends on: SUBTASK-01..07  
Estimate: 8 pts

Description

Build a full E2E suite based on already-proven test patterns from:

- `all-data-simulator/web_user_simulator.py`
- `all-data-simulator/test_web_user_simulator.py`
- `all-data-simulator/run_tracking_analytics_e2e.sh`

Required E2E sequence

Select Segment ID -> Sync to CRM routes -> Generate AI Template Draft -> Generate AI Campaign Draft -> Human Approve -> Dispatch (mock ESP) -> Simulate Webhooks -> Verify profile and campaign metrics.

Scope of Work

Phase A - Harness baseline

- Reuse contract-check client pattern (request/response strict assertions).
- Reuse deterministic fixture strategy.
- Reuse bounded polling and timeout handling pattern.
- Reuse dual verification pattern (API result + direct PostgreSQL query).

Phase B - CRM routing assertions

- For one selected `segment_id`, assert:
  - `customer` profiles synchronized to `crm_customer_contacts` and `crm_transactions`.
  - `lead` profiles synchronized to `crm_lead` and `crm_lead_source`.
  - other profiles synchronized to `crm_contact`.

Phase C - AI draft and approval assertions

- Assert AI template is created as draft.
- Assert AI campaign is created as draft with objective, strategy, start/end date, and content plan.
- Assert campaign cannot run until human approval event is recorded.

Phase D - Dispatch and tracking assertions

- Mock send path writes one dispatch record per eligible recipient.
- Webhook simulation updates events and suppression behavior.
- Idempotency and retry paths validated.

Phase E - Feedback and analytics assertions

- Verify profile timestamp changes and campaign metric updates.
- Validate API summary and direct DB values agree.

Phase F - CI release gate

- Required CI check for Beta.
- Separate fast PR smoke and full nightly profiles.
- Emit actionable failure artifacts.

Acceptance Criteria

- Full required sequence passes with deterministic fixtures.
- CRM routing behavior is verified exactly against your 1.1/1.2/1.3 rules.
- AI draft + human approval gate is verified.
- Dispatch/webhook/feedback loop is verified.
- CI gate is green and enforced for release.

Definition of Done

Objective evidence exists that the complete marketer workflow works end to end and follows repository-proven test patterns.

## Technical Execution Checklist (Epic Gate)

Use this checklist as the implementation tracker for all technical tasks in this epic.

### A. Task Naming Convention Checklist

- [ ] All sprint tasks use `SUBTASK-XX: <Action + Domain>` format.
- [ ] API route handlers use `verb_noun_scope` naming (example: `sync_segment_crm`).
- [ ] Service methods use `action_domain_object` naming (example: `generate_campaign_draft`).
- [ ] Dagster assets/ops/jobs use explicit domain prefixes (example: `crm_sync_*`, `email_*`, `campaign_*`).
- [ ] New PostgreSQL tables follow module prefix rules:
  - CRM module tables use `crm_*`.
  - CDP module tables use `cdp_*`.
  - System tables use `sys_*`.
- [ ] New environment variables for this epic use `CRM_EMAIL_*` or provider-specific standard names.

### B. PostgreSQL Data Tables Checklist

- [ ] Create `crm_email_templates` table with subject, html/text body, variables, status, approvals, metadata, and `tenant_id`.
- [ ] Alter `crm_campaign` to add `segment_id`, `template_id`, `approval_status`, `approved_by`, `approved_at`, and AI planning fields.
- [ ] Alter `crm_lead` to add `lead_source_id` FK to `crm_lead_source`.
- [ ] Create `crm_campaign_content_items` relation table to map campaign-to-content (`cdp_content_items`).
- [ ] Create `crm_segment_sync_runs` audit table for each segment sync run.
- [ ] Create or confirm `cdp_campaign_dispatch_logs` table supports idempotency keys, provider response, and status timeline.
- [ ] Add FK constraints for all cross-table relationships.
- [ ] Add unique constraints for idempotency-sensitive entities.
- [ ] Add tenant-aware indexes for query hot paths.
- [ ] Add or update RLS policies for all new tenant-scoped tables.
- [ ] Add forward and rollback migration scripts and verify both directions.

### C. Backend Tasks Checklist

- [ ] Implement `POST /api/v1/admin/crm/sync-segment/{segment_id}` with optional dry-run.
- [ ] Recompute segment membership before sync and persist membership snapshot/run metrics.
- [ ] Implement lifecycle routing logic exactly:
  - `customer` -> `crm_customer_contacts` and `crm_transactions`
  - `lead` -> `crm_lead` and `crm_lead_source`
  - non-customer and non-lead -> `crm_contact`
- [ ] Implement idempotent upsert logic for all sync target tables.
- [ ] Implement AI template draft generation with provider switch (`gemini` / `openai`).
- [ ] Implement template review APIs (`approve`, `reject`, `edit`) and enforce approval requirement.
- [ ] Implement AI campaign draft creation with objective, strategy, schedule, and content linkage.
- [ ] Enforce campaign state machine so AI-created campaigns remain `Draft` until human approval.
- [ ] Replace `campaign_activation` Dagster placeholder with real execution flow.
- [ ] Replace `email_engine` Dagster placeholder with real rendering/dispatch flow.
- [ ] Implement provider delivery webhooks and tracking endpoints (open pixel and click redirect).
- [ ] Implement suppression updates for bounce/complaint and callback dedup handling.
- [ ] Implement profile feedback updates and campaign performance rollups.
- [ ] Add E2E automation that follows simulator pattern (`web_user_simulator.py` and `run_tracking_analytics_e2e.sh`).

### D. Required Environment Config Checklist

- [ ] Define AI provider selector:
  - `CRM_EMAIL_AI_PROVIDER` (`openai` or `gemini`)
- [ ] Define OpenAI credentials/config when provider is OpenAI:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
- [ ] Define Gemini credentials/config when provider is Gemini:
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL`
- [ ] Define sender identity:
  - `CRM_EMAIL_FROM_NAME`
  - `CRM_EMAIL_FROM_ADDRESS`
  - `CRM_EMAIL_REPLY_TO`
- [ ] Define compliance and tracking URLs:
  - `CRM_EMAIL_UNSUBSCRIBE_BASE_URL`
  - `CRM_EMAIL_TRACKING_BASE_URL`
- [ ] Define SMTP transport values for SMTP mode:
  - `SMTP_HOST`
  - `SMTP_PORT`
  - `SMTP_USERNAME`
  - `SMTP_PASSWORD`
  - `SMTP_USE_TLS`
- [ ] Define runtime controls:
  - `CRM_EMAIL_BATCH_SIZE`
  - `CRM_EMAIL_RATE_LIMIT_PER_SEC`
  - `CRM_EMAIL_MAX_RETRIES`
  - `CRM_EMAIL_RETRY_BACKOFF_MS`
- [ ] Define webhook security values:
  - `CRM_EMAIL_WEBHOOK_SIGNING_SECRET`
- [ ] Ensure values exist in both `.env.example` and active environment files used by services.

### E. Definition of Ready and Done Checklist

- [ ] Ready: all required schema migrations are reviewed before backend implementation starts.
- [ ] Ready: all required environment keys are documented and available in deployment manifests.
- [ ] Done: unit tests pass for services, repositories, and routers touched by this epic.
- [ ] Done: E2E workflow passes from segment sync to approved campaign dispatch and feedback updates.
- [ ] Done: audit logs and tenant-isolation behavior are verified for all new flows.

## Out of Scope (v2.1+)

- Multi-touch attribution modeling.
- Advanced RL/agent optimization loops.
- New channel expansion beyond email.
- Full replacement of non-email placeholder Dagster services not required by this story.

## Non-Functional Requirements (Beta Gates)

- Tenant isolation and RLS safety.
- SQL safety for generated filters and dynamic queries.
- Audit logging for sync, draft generation, approval, dispatch, and webhook actions.
- Secrets management for AI and ESP credentials.
- Deliverability circuit breaker and retry controls.