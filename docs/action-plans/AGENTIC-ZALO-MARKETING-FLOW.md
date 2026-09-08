# Agentic Outbound Zalo OA Marketing Execution Engine

## Summary

This plan defines the v2.0 Beta path for a complete marketer workflow:

1. Select a segment by `segment_id` from PostgreSQL and sync matched profiles with phone identity into the correct CRM routes.
2. Create Zalo OA message template drafts with AI agents (Gemini or OpenAI) and human review.
3. Create campaign drafts with AI strategy, schedule, and content plan, then require human approval before run.

Final business outcome:

Segment -> CRM sync routing -> Phone eligibility filter -> AI Zalo template draft -> AI campaign draft -> Human approval -> Dispatch -> Track -> Feedback into Customer 360.

## Verified Ground Truth (Repo Audit)

### Database schema reality (`database-init/database-schema.sql`)

- `cdp_segments` exists and supports segment selection by `segment_id`.
- `cdp_master_profiles` exists with lifecycle, contact, and personalization attributes used for targeting.
- CRM entities exist and can anchor channel-level campaign execution:
	- `crm_campaign`
	- `crm_campaign_member`
	- `crm_campaign_performance_daily`
	- `crm_contact`
	- `crm_lead`
	- `crm_lead_source`
	- `crm_customer_contacts`
	- `crm_transactions`
- `cdp_content_items` exists and can provide message content inventory.

Schema gaps that must be addressed in this story:

- No dedicated `crm_zalo_templates` table exists.
- No dedicated `crm_zalo_oa_accounts` table exists.
- No dedicated Zalo dispatch log table exists.
- No dedicated Zalo opt-out/suppression table exists.
- No explicit campaign linkage for Zalo template/account/channel config exists on `crm_campaign`.

### Service and orchestration reality

Implemented and active Dagster services:

- `identity_resolution` (`identity_resolution_job` + sensor)
- `segmentation` (`segmentation_job` + change-driven polling sensor)
- `analytics` (`analytics_job` + hourly schedule)

Placeholders (log + sleep only):

- `campaign_activation`
- `notification_engine`
- `data_synch`
- `email_engine`
- `scoring`
- `personalization`

Implication: this story must convert `campaign_activation` and `notification_engine` into real Zalo OA execution jobs, while reusing `segmentation` and `analytics`.

## Target Marketer Flow (Required)

1. Marketer selects `segment_id`.
2. System recomputes segment membership and freezes run snapshot.
3. System syncs matched profiles to CRM by lifecycle routing rules:
	 - Customer route: write/update `crm_customer_contacts` and `crm_transactions`.
	 - Lead route: write/update `crm_lead` and `crm_lead_source`.
	 - Contact route (not customer and not lead): write/update `crm_contact`.
4. System filters Zalo-eligible recipients (valid phone, consented, not suppressed).
5. AI agent creates Zalo OA message template draft (Gemini or OpenAI).
6. AI agent creates campaign draft in `crm_campaign` with objective, `start_date`, `end_date`, and linked content from `cdp_content_items`.
7. Human review/approval gate moves campaign from `Draft` to executable states.
8. Dispatch runs through Zalo OA adapters; delivery/read/click feedback is normalized into Customer 360 analytics.

## Story-level Acceptance Criteria

- Segment can be selected by `segment_id` and recomputed before channel dispatch.
- Routing rules are enforced exactly:
	- `customer` -> `crm_customer_contacts` + `crm_transactions`
	- `lead` -> `crm_lead` + `crm_lead_source`
	- neither `customer` nor `lead` -> `crm_contact`
- Zalo eligibility filters enforce valid phone, consent, and suppression rules.
- AI can produce Zalo template drafts using Gemini or OpenAI.
- AI can produce campaign drafts with objective, schedule, and content plan.
- Campaigns created by AI remain `Draft` until explicit human approval.
- Dispatch and callback ingestion are auditable, idempotent, tenant-safe, and retry-safe.
- E2E automation follows simulator verification patterns and includes API + DB dual checks.

## Subtasks (P0 Sprint - 8 Blockers)

### SUBTASK-01: Schema and Migration Foundation for Zalo OA

Component: `database-init/migrations`  
Priority: P0 Blocker  
Depends on: none  
Blocks: SUBTASK-02..08  
Estimate: 5 pts

Description

Add missing relational structure for Zalo template lifecycle, account linkage, and dispatch traceability.

Scope of Work

- Create `crm_zalo_templates` table with at least:
	- `template_id`, `tenant_id`, `name`, `message_text`, `variables`, `status`, `created_by`, `approved_by`, `approved_at`, `metadata`.
- Create `crm_zalo_oa_accounts` table with:
	- `oa_account_id`, `tenant_id`, `oa_name`, `oa_id`, `status`, `config`, `metadata`.
- Create `crm_zalo_dispatch_logs` table with:
	- `dispatch_id`, `tenant_id`, `campaign_id`, `master_profile_id`, `phone_hash`, `provider_message_id`, `status`, `sent_at`, `delivered_at`, `read_at`, `error_code`, `metadata`.
- Create `crm_zalo_suppression` table with:
	- `suppression_id`, `tenant_id`, `phone_hash`, `reason`, `source`, `created_at`, `metadata`.
- Extend `crm_campaign` with:
	- `segment_id` FK -> `cdp_segments.segment_id`
	- `zalo_template_id` FK -> `crm_zalo_templates.template_id`
	- `oa_account_id` FK -> `crm_zalo_oa_accounts.oa_account_id`
	- `approval_status` (`Draft`, `InReview`, `Approved`, `Rejected`)
	- `approved_by`, `approved_at`
	- `strategy_summary` and optional `ai_plan`.
- Add campaign-content relation table `crm_campaign_content_items` if not present.
- Add sync-run audit table `crm_zalo_sync_runs` with per-route and eligibility counts.
- Add/extend indexes and RLS policies for all new tenant-scoped tables.

Acceptance Criteria

- Migrations apply and rollback cleanly.
- New FKs enforce campaign-template-account integrity.
- RLS policies exist for all new tenant-scoped tables.
- SQLAlchemy models and Pydantic schemas are updated.

Definition of Done

Schema supports deterministic Zalo OA workflow with governance and dispatch observability.

### SUBTASK-02: Segment-ID Driven CRM Sync and Zalo Eligibility

Component: `customer360-api/core` + `backend-system/data_synch`  
Priority: P0 Blocker  
Depends on: SUBTASK-01  
Blocks: SUBTASK-03, SUBTASK-05, SUBTASK-08  
Estimate: 8 pts

Description

Implement selected-segment sync and recipient eligibility for Zalo OA activation.

Scope of Work

- API endpoint to trigger one-segment Zalo sync run:
	- `POST /api/v1/admin/zalo/sync-segment/{segment_id}`
	- optional dry-run mode returning route and eligibility counts only.
- Recompute selected segment and resolve members from `cdp_master_profiles`.
- Enforce lifecycle routing into CRM tables:
	- Route A: `customer` -> `crm_customer_contacts` + `crm_transactions`
	- Route B: `lead` -> `crm_lead` + `crm_lead_source`
	- Route C: non-customer/non-lead -> `crm_contact`
- Apply Zalo eligibility filters:
	- valid normalized phone
	- consent/opt-in enabled
	- not in `crm_zalo_suppression`
- Persist run record in `crm_zalo_sync_runs`.
- Guarantee idempotent rerun behavior.

Acceptance Criteria

- One selected `segment_id` syncs with deterministic per-route counts.
- Zalo eligibility and suppression filters are consistently applied.
- Sync run data is tenant-safe and auditable.

Definition of Done

Marketer can select one segment and produce a deterministic Zalo-eligible audience.

### SUBTASK-03: AI Zalo Template Authoring (Gemini/OpenAI)

Component: `customer360-api/core` + `backend-system/notification_engine`  
Priority: P0 Blocker  
Depends on: SUBTASK-01, SUBTASK-02  
Blocks: SUBTASK-04, SUBTASK-05, SUBTASK-08  
Estimate: 5 pts

Description

Create AI-assisted Zalo message template drafts with compliance guardrails and human governance.

Scope of Work

- Add template generation endpoint/service with provider selection:
	- Gemini provider
	- OpenAI-compatible provider
- Input contract includes:
	- segment context
	- objective and offer
	- tone/brand constraints
	- locale/language
- AI output generates:
	- message text variants
	- CTA text variants
	- required variables and short links placeholders
- Save drafts in `crm_zalo_templates` with `Draft` status.
- Add review API: approve/reject/edit template before campaign use.
- Add safety checks: policy keywords, forbidden claims, length limits.

Acceptance Criteria

- Zalo template drafts can be generated through Gemini or OpenAI path.
- Draft templates are persisted and versionable.
- Human approval is mandatory before template can be attached to a runnable campaign.

Definition of Done

AI Zalo template generation works with enforceable governance.

### SUBTASK-04: AI Campaign Strategy and Draft Creation (Zalo OA)

Component: `customer360-api/core` + `backend-system/campaign_orchestration`  
Priority: P0 Blocker  
Depends on: SUBTASK-01, SUBTASK-02, SUBTASK-03  
Blocks: SUBTASK-05, SUBTASK-08  
Estimate: 8 pts

Description

AI should produce Zalo campaign drafts with strategy, schedule, and content linkage; humans must approve before activation.

Scope of Work

- Add campaign planning endpoint/service that takes:
	- `segment_id`
	- approved `zalo_template_id`
	- selected `oa_account_id`
	- objective and schedule constraints
- AI proposes:
	- campaign name and objective
	- strategy summary
	- schedule window (`start_date`, `end_date`)
	- recommended content set from `cdp_content_items`
- Create campaign row in `crm_campaign` with:
	- status `Draft`
	- approval status `InReview`
	- linked `segment_id`, `zalo_template_id`, `oa_account_id`
- Persist content links in `crm_campaign_content_items`.
- Enforce state machine:
	- AI can create only `Draft`
	- only authorized human can approve and transition to `Scheduled`/`Running`

Acceptance Criteria

- AI campaign drafts include objective, schedule, and content plan.
- Draft campaigns cannot run without human approval.
- State transitions are validated and audited.

Definition of Done

AI can prepare complete Zalo campaign drafts while human approval remains mandatory.

### SUBTASK-05: Dagster Execution Modernization (Campaign + Notification)

Component: `backend-system/campaign_activation` + `backend-system/notification_engine`  
Priority: P0 Blocker  
Depends on: SUBTASK-02, SUBTASK-03, SUBTASK-04  
Blocks: SUBTASK-06, SUBTASK-08  
Estimate: 8 pts

Description

Replace placeholders with real orchestration for Zalo OA dispatch.

Scope of Work

- Replace `campaign_activation_job` placeholder with real orchestration:
	- validate approval status
	- load campaign, template, and eligible audience snapshot
	- trigger notification dispatch
- Replace `notification_engine_job` placeholder with real send pipeline:
	- render final message payload
	- dispatch through Zalo OA adapter
	- persist send attempts to `crm_zalo_dispatch_logs`
- Integrate with existing real services:
	- use `segmentation_job` outputs for selected segment freshness
	- preserve compatibility with `analytics_job` downstream verification
- Keep unrelated placeholder services out of this sprint scope.

Acceptance Criteria

- `campaign_activation` and `notification_engine` run real logic, not sleep placeholders.
- Dispatch runs are observable, retry-safe, and idempotent.
- Per-recipient send results are auditable.

Definition of Done

Zalo OA execution path is production-like and orchestrated via real jobs.

### SUBTASK-06: Zalo Webhooks, Compliance, and Suppression Feedback

Component: `customer360-api/core/routers` + `data-tracking-api`  
Priority: P0 Blocker  
Depends on: SUBTASK-05  
Blocks: SUBTASK-07, SUBTASK-08  
Estimate: 5 pts

Description

Capture delivery/read/click/unfollow events and keep suppression/compliance state consistent.

Scope of Work

- Add webhook endpoints for Zalo OA callbacks (delivered/read/click/failure/opt-out).
- Correlate events to `campaign_id`, `provider_message_id`, and profile identity when available.
- Normalize to canonical event contracts and update suppression on explicit opt-out or policy events.
- Deduplicate repeated callbacks and verify webhook signatures.

Acceptance Criteria

- Webhook events are captured with correct campaign/message correlation.
- Opt-out and failure events update suppression eligibility correctly.
- Duplicate callbacks do not inflate metrics.

Definition of Done

Closed-loop callback handling and suppression updates are operational.

### SUBTASK-07: Customer 360 Feedback and Zalo Performance Rollups

Component: `backend-system/analytics` + `customer360-api`  
Priority: P0 Blocker  
Depends on: SUBTASK-06  
Blocks: SUBTASK-08  
Estimate: 5 pts

Description

Ensure Zalo engagement updates profile intelligence and campaign metrics.

Scope of Work

- Update profile touchpoint timestamps from Zalo events.
- Trigger segmentation refresh behavior after relevant profile updates.
- Roll up Zalo metrics into campaign performance surfaces.
- Keep tenant-safe and auditable data changes.

Acceptance Criteria

- Profile timestamps update correctly after sent/delivered/read/click events.
- Segment refresh is triggered by relevant profile changes.
- Campaign performance views include Zalo channel metrics.

Definition of Done

Zalo engagement data becomes actionable inside profile and campaign analytics.

### SUBTASK-08: End-to-End Automated Test Suite (Learn from Simulator)

Component: cross-cutting (`all-data-simulator`, `backend-system`, `customer360-api`)  
Priority: P0 Blocker (Beta Gate)  
Depends on: SUBTASK-01..07  
Estimate: 8 pts

Description

Build a full E2E suite based on proven simulator patterns from:

- `all-data-simulator/web_user_simulator.py`
- `all-data-simulator/test_web_user_simulator.py`
- `all-data-simulator/run_tracking_analytics_e2e.sh`

Required E2E sequence

Select Segment ID -> Sync to CRM routes -> Filter Zalo-eligible recipients -> Generate AI Zalo Template Draft -> Generate AI Campaign Draft -> Human Approve -> Dispatch (mock OA adapter) -> Simulate Webhooks -> Verify profile and campaign metrics.

Scope of Work

Phase A - Harness baseline

- Reuse contract-check client pattern (request/response strict assertions).
- Reuse deterministic fixture strategy.
- Reuse bounded polling and timeout handling pattern.
- Reuse dual verification pattern (API result + direct PostgreSQL query).

Phase B - CRM routing and eligibility assertions

- For one selected `segment_id`, assert lifecycle routing tables receive expected rows.
- Assert phone eligibility and suppression filters are deterministic.

Phase C - AI draft and approval assertions

- Assert AI template is created as draft.
- Assert AI campaign is created as draft with objective, strategy, and start/end date.
- Assert campaign cannot run until human approval is recorded.

Phase D - Dispatch and webhook assertions

- Mock dispatch path writes one record per eligible recipient.
- Webhook simulation updates event facts and suppression state.
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
- CRM routing and Zalo eligibility behavior are verified exactly.
- AI draft + human approval gate is verified.
- Dispatch/webhook/feedback loop is verified.
- CI gate is green and enforced for release.

Definition of Done

Objective evidence exists that the complete Zalo OA workflow works end to end and follows repository-proven test patterns.

## Technical Execution Checklist (Epic Gate)

Use this checklist as the implementation tracker for all technical tasks in this epic.

### A. Task Naming Convention Checklist

- [ ] All sprint tasks use `SUBTASK-XX: <Action + Domain>` format.
- [ ] API route handlers use `verb_noun_scope` naming (example: `sync_segment_zalo`).
- [ ] Service methods use `action_domain_object` naming (example: `generate_zalo_campaign_draft`).
- [ ] Dagster assets/ops/jobs use explicit domain prefixes (example: `zalo_sync_*`, `zalo_dispatch_*`, `campaign_*`).
- [ ] New PostgreSQL tables follow module prefix rules:
	- CRM module tables use `crm_*`.
	- CDP module tables use `cdp_*`.
	- System tables use `sys_*`.
- [ ] New environment variables for this epic use `CRM_ZALO_*` plus provider-specific standard names.

### B. PostgreSQL Data Tables Checklist

- [ ] Create `crm_zalo_templates` table with message payload, variables, approvals, metadata, and `tenant_id`.
- [ ] Create `crm_zalo_oa_accounts` table for account-level dispatch configuration.
- [ ] Create `crm_zalo_dispatch_logs` table for message-level delivery lifecycle.
- [ ] Create `crm_zalo_suppression` table for opt-out/failure-based suppression state.
- [ ] Alter `crm_campaign` to add `segment_id`, `zalo_template_id`, `oa_account_id`, approval fields, and AI planning fields.
- [ ] Create `crm_campaign_content_items` relation table if not already available.
- [ ] Create `crm_zalo_sync_runs` audit table.
- [ ] Add FK constraints for all cross-table relationships.
- [ ] Add unique constraints for idempotency-sensitive entities.
- [ ] Add tenant-aware indexes for query hot paths.
- [ ] Add or update RLS policies for all new tenant-scoped tables.
- [ ] Add forward and rollback migration scripts and verify both directions.

### C. Backend Tasks Checklist

- [ ] Implement `POST /api/v1/admin/zalo/sync-segment/{segment_id}` with optional dry-run.
- [ ] Recompute segment membership before sync and persist run metrics.
- [ ] Implement lifecycle routing logic exactly:
	- `customer` -> `crm_customer_contacts` and `crm_transactions`
	- `lead` -> `crm_lead` and `crm_lead_source`
	- non-customer and non-lead -> `crm_contact`
- [ ] Implement recipient eligibility filters (valid phone, consent, suppression).
- [ ] Implement idempotent upsert logic for sync targets and dispatch writes.
- [ ] Implement AI template draft generation with provider switch (`gemini` / `openai`).
- [ ] Implement template review APIs (`approve`, `reject`, `edit`) and enforce approval requirement.
- [ ] Implement AI campaign draft creation with objective, strategy, schedule, and content linkage.
- [ ] Enforce campaign state machine so AI-created campaigns remain `Draft` until human approval.
- [ ] Replace `campaign_activation` placeholder with real execution flow.
- [ ] Replace `notification_engine` placeholder with real Zalo OA dispatch flow.
- [ ] Implement Zalo webhook ingestion, signature validation, and dedup handling.
- [ ] Implement suppression updates and performance rollup updates.
- [ ] Add E2E automation that follows simulator pattern.

### D. Required Environment Config Checklist

- [ ] Define AI provider selector:
	- `CRM_ZALO_AI_PROVIDER` (`openai` or `gemini`)
- [ ] Define OpenAI credentials/config when provider is OpenAI:
	- `OPENAI_API_KEY`
	- `OPENAI_MODEL`
- [ ] Define Gemini credentials/config when provider is Gemini:
	- `GEMINI_API_KEY`
	- `GEMINI_MODEL`
- [ ] Define Zalo OA credentials/config:
	- `CRM_ZALO_OA_APP_ID`
	- `CRM_ZALO_OA_APP_SECRET`
	- `CRM_ZALO_OA_ACCESS_TOKEN`
	- `CRM_ZALO_OA_REFRESH_TOKEN`
	- `CRM_ZALO_OA_API_BASE_URL`
- [ ] Define sender and compliance defaults:
	- `CRM_ZALO_BRAND_NAME`
	- `CRM_ZALO_TRACKING_BASE_URL`
	- `CRM_ZALO_UNSUBSCRIBE_BASE_URL`
- [ ] Define runtime controls:
	- `CRM_ZALO_BATCH_SIZE`
	- `CRM_ZALO_RATE_LIMIT_PER_SEC`
	- `CRM_ZALO_MAX_RETRIES`
	- `CRM_ZALO_RETRY_BACKOFF_MS`
- [ ] Define webhook security values:
	- `CRM_ZALO_WEBHOOK_SIGNING_SECRET`
- [ ] Ensure values exist in `.env.example` and active environment files used by services.

### E. Definition of Ready and Done Checklist

- [ ] Ready: all required schema migrations are reviewed before backend implementation starts.
- [ ] Ready: all required environment keys are documented and available in deployment manifests.
- [ ] Done: unit tests pass for services, repositories, and routers touched by this epic.
- [ ] Done: E2E workflow passes from segment sync to approved Zalo dispatch and feedback updates.
- [ ] Done: audit logs and tenant-isolation behavior are verified for all new flows.

## Out of Scope (v2.1+)

- Advanced conversation bot flows beyond campaign messaging.
- Multi-touch cross-channel attribution optimization loops.
- Autonomous send-time optimization without human override.
- Full modernization of unrelated placeholder services.

## Non-Functional Requirements (Beta Gates)

- Tenant isolation and RLS safety.
- SQL safety for generated filters and dynamic queries.
- Audit logging for sync, template generation, approval, dispatch, and webhook actions.
- Secrets management for AI and Zalo OA credentials.
- Retry, idempotency, and circuit-breaker controls for dispatch workflows.
