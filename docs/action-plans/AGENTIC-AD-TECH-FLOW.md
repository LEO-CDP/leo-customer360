# Agentic Ad Tech Marketing Execution Engine

## Summary

This plan defines the v2.0 Beta path for a complete marketer workflow:

1. Select a segment by `segment_id` from PostgreSQL and sync matched profiles into CRM routes and ad audience export.
2. Create ad creative drafts with AI agents (Gemini or OpenAI) and human review.
3. Create ad campaign drafts with AI strategy, budget, schedule, and creative/content linkage, then require human approval before run.

Final business outcome:

Segment -> CRM sync routing -> Ad audience export -> AI creative draft -> AI campaign draft -> Human approval -> Publish to ad channels -> Track -> Feedback into Customer 360.

## Verified Ground Truth (Repo Audit)

### Database schema reality (`database-init/database-schema.sql`)

- `cdp_segments` exists and supports segment selection by `segment_id`.
- `cdp_master_profiles` exists and carries `lifecycle_stage` plus multi-attribute profile context for audience targeting.
- CRM entities exist and can anchor channel-level campaigns:
	- `crm_campaign`
	- `crm_campaign_member`
	- `crm_campaign_performance_daily`
	- `crm_contact`
	- `crm_lead`
	- `crm_lead_source`
	- `crm_customer_contacts`
	- `crm_transactions`
- `cdp_content_items` exists and can be used as source content for ad creative variants.
- Raw ingestion tables already carry ad attribution fields (`campaign`, `adset`, `ad_name`, `ad_id`, UTM fields) for downstream performance analysis.

Schema gaps that must be addressed in this story:

- No dedicated CRM-level ad creative table exists.
- No dedicated table links CRM campaign to external ad platform campaign/adset/ad IDs.
- No dedicated segment audience snapshot/export table exists for deterministic ad activation runs.
- No dedicated ad webhook normalization table exists in the CRM/CDP schema.

### Ad Server reality (`ads-server/*`)

- `ads-server` is a real service with its own `leo_ads` schema, SQLAlchemy models, and repository/API tests.
- Domain models exist for campaign, creative, ad, placement, targeting, and tenant.
- Admin API and schema docs are present under `ads-server/static/admin` and `ads-server/sql-scripts`.
- Current gap for this epic: no proven orchestrated bridge from Customer 360 segment workflows into `leo_ads` activation and closed-loop attribution.

### Dagster service reality (`backend-system/*/dagster_defs.py`)

Implemented and active:

- `identity_resolution` (`identity_resolution_job` + sensor)
- `segmentation` (`segmentation_job` + change-driven polling sensor)
- `analytics` (`analytics_job` + hourly schedule)

Placeholders (log + sleep only):

- `campaign_activation`
- `data_synch`
- `email_engine`
- `scoring`
- `notification_engine`
- `personalization`

Implication: this story must convert `campaign_activation` and `data_synch` into real ad-tech orchestration jobs, while reusing existing `segmentation` and `analytics` services.

## Target Marketer Flow (Required)

1. Marketer selects `segment_id`.
2. System recomputes segment membership and freezes run snapshot.
3. System syncs matched profiles to CRM by lifecycle routing rules:
	 - Customer route: write/update `crm_customer_contacts` and `crm_transactions`.
	 - Lead route: write/update `crm_lead` and `crm_lead_source`.
	 - Contact route (not customer and not lead): write/update `crm_contact`.
4. System exports eligible audience to ad activation layer with deterministic snapshot IDs.
5. AI agent creates ad creative drafts (headline/body/CTA/media spec hints) using Gemini or OpenAI.
6. AI agent creates campaign draft in `crm_campaign` with objective, budget, `start_date`, `end_date`, and linked creative/content plan.
7. Human review/approval gate moves campaign from `Draft` to executable states.
8. Dispatch publishes to ad tech path (internal `ads-server` and/or configured platform connectors), then tracking and analytics feed Customer 360.

## Story-level Acceptance Criteria

- Segment can be selected by `segment_id` and recomputed before export.
- Routing rules are enforced exactly:
	- `customer` -> `crm_customer_contacts` + `crm_transactions`
	- `lead` -> `crm_lead` + `crm_lead_source`
	- neither `customer` nor `lead` -> `crm_contact`
- Ad audience export is deterministic and idempotent per run.
- AI can produce ad creative drafts using Gemini or OpenAI.
- AI can produce campaign drafts with objective, schedule, budget, and creative/content plan.
- Campaigns created by AI remain `Draft` until explicit human approval.
- Activation is auditable, tenant-safe, retry-safe, and traceable to external platform identifiers.
- E2E automation follows simulator verification patterns and includes API + DB dual checks.

## Subtasks (P0 Sprint - 8 Blockers)

### SUBTASK-01: Schema and Migration Foundation for Ad Tech

Component: `database-init/migrations` + `ads-server/sql-scripts`  
Priority: P0 Blocker  
Depends on: none  
Blocks: SUBTASK-02..08  
Estimate: 5 pts

Description

Add missing relational structure for CRM-to-Ad-Tech campaign lifecycle and traceability.

Scope of Work

- Create `crm_ad_creatives` table with at least:
	- `creative_id`, `tenant_id`, `name`, `format`, `headline`, `body_text`, `cta_text`, `asset_url`, `metadata`, `status`, `created_by`, `approved_by`, `approved_at`.
- Create `crm_ad_campaign_platform_map` table with:
	- `campaign_id` FK -> `crm_campaign.campaign_id`
	- `platform_code`, `external_campaign_id`, `external_adset_id`, `external_ad_id`, `sync_status`, `last_synced_at`, `metadata`.
- Create `crm_segment_audience_exports` table with:
	- `export_id`, `tenant_id`, `segment_id`, `snapshot_at`, `total_members`, `eligible_members`, `export_status`, `metadata`.
- Extend `crm_campaign` with:
	- `segment_id` FK -> `cdp_segments.segment_id`
	- `ad_creative_id` FK -> `crm_ad_creatives.creative_id`
	- `approval_status` (`Draft`, `InReview`, `Approved`, `Rejected`)
	- `approved_by`, `approved_at`
	- `strategy_summary`, `budget_plan`, `ai_plan`.
- Add campaign-content relation table `crm_campaign_content_items` if not present.
- Add sync-run audit table `crm_ad_sync_runs` with per-route counts and publish results.
- Add/extend indexes and RLS policies for all new tenant-scoped tables.

Acceptance Criteria

- Migrations apply and rollback cleanly.
- New FKs enforce campaign, creative, and platform-map integrity.
- RLS policies exist for all new tenant-scoped tables.
- SQLAlchemy models and Pydantic schemas are updated where needed.

Definition of Done

Schema supports deterministic ad-tech workflow with CRM ownership and external mapping traceability.

### SUBTASK-02: Segment-ID Driven CRM Sync and Audience Export

Component: `customer360-api/core` + `backend-system/data_synch`  
Priority: P0 Blocker  
Depends on: SUBTASK-01  
Blocks: SUBTASK-03, SUBTASK-05, SUBTASK-08  
Estimate: 8 pts

Description

Implement selected-segment sync and audience export with strict lifecycle routing and eligibility controls.

Scope of Work

- API endpoint to trigger one-segment ad export run:
	- `POST /api/v1/admin/adtech/sync-segment/{segment_id}`
	- optional dry-run mode returning route and eligibility counts only.
- Recompute selected segment, resolve members from `cdp_master_profiles`, and persist export snapshot metadata.
- Enforce lifecycle routing into CRM tables:
	- Route A: `customer` -> `crm_customer_contacts` + `crm_transactions`
	- Route B: `lead` -> `crm_lead` + `crm_lead_source`
	- Route C: non-customer/non-lead -> `crm_contact`
- Apply ad-channel eligibility filters:
	- valid identifiers and channel permissions
	- suppression/opt-out exclusion
	- frequency cap pre-checks
- Persist export run record in `crm_segment_audience_exports` and `crm_ad_sync_runs`.
- Guarantee idempotent rerun behavior.

Acceptance Criteria

- One selected `segment_id` syncs and exports with deterministic counts.
- Routing rules match required behavior exactly.
- Export snapshots are immutable per run.
- Tenant isolation is enforced end to end.

Definition of Done

Marketer can select one segment and produce deterministic CRM sync plus ad audience export artifacts.

### SUBTASK-03: AI Ad Creative Authoring (Gemini/OpenAI)

Component: `customer360-api/core` + `ads-server`  
Priority: P0 Blocker  
Depends on: SUBTASK-01, SUBTASK-02  
Blocks: SUBTASK-04, SUBTASK-05, SUBTASK-08  
Estimate: 5 pts

Description

Create AI-assisted ad creative drafting with policy guardrails and human approval.

Scope of Work

- Add creative generation endpoint/service with provider selection:
	- Gemini provider
	- OpenAI-compatible provider
- Input contract includes:
	- segment context
	- objective and offer details
	- platform constraints (headline length, text length, CTA set)
	- language and locale
- AI output generates:
	- headline variants
	- primary text variants
	- CTA recommendations
	- optional media guidance metadata
- Save creative drafts in `crm_ad_creatives` with initial `Draft` status.
- Add review API: approve/reject/edit creative before campaign activation.
- Add safety checks: prohibited claims, policy keywords, length limits.

Acceptance Criteria

- Creative drafts can be generated through Gemini or OpenAI path.
- Draft creatives are persisted and versionable.
- Human approval is mandatory before campaign can reference an active creative.

Definition of Done

AI ad creative generation works with enforceable governance.

### SUBTASK-04: AI Campaign Strategy and Draft Creation (Ad Tech)

Component: `customer360-api/core` + `backend-system/campaign_orchestration`  
Priority: P0 Blocker  
Depends on: SUBTASK-01, SUBTASK-02, SUBTASK-03  
Blocks: SUBTASK-05, SUBTASK-08  
Estimate: 8 pts

Description

AI should generate ad campaign drafts with objective, budget, schedule, and creative/content linkage; human approval is required for execution.

Scope of Work

- Add campaign planning endpoint/service that takes:
	- `segment_id`
	- approved `ad_creative_id`
	- marketer objective
	- budget and date constraints
	- target platform(s)
- AI proposes:
	- campaign name and objective
	- strategy summary and KPI plan
	- schedule window (`start_date`, `end_date`)
	- recommended content set from `cdp_content_items`
	- platform rollout hints
- Create campaign row in `crm_campaign` with:
	- status `Draft`
	- approval status `InReview`
	- linked `segment_id`, `ad_creative_id`
- Persist content links in `crm_campaign_content_items`.
- Enforce state machine:
	- AI can create only `Draft`
	- only authorized human can approve and transition to `Scheduled`/`Running`

Acceptance Criteria

- AI campaign drafts include objective, schedule, budget, and creative/content plan.
- Draft campaigns cannot run without human approval.
- State transitions are validated and audited.

Definition of Done

AI can prepare complete ad campaign drafts while human approval remains a hard gate.

### SUBTASK-05: Activation Pipeline Modernization (Campaign + Data Sync)

Component: `backend-system/campaign_activation` + `backend-system/data_synch` + `ads-server`  
Priority: P0 Blocker  
Depends on: SUBTASK-02, SUBTASK-03, SUBTASK-04  
Blocks: SUBTASK-06, SUBTASK-08  
Estimate: 8 pts

Description

Replace placeholders with real orchestration for ad audience publication and campaign activation.

Scope of Work

- Replace `campaign_activation_job` placeholder with real orchestration:
	- validate approval status
	- load campaign + creative + audience export snapshot
	- publish activation request
- Replace `data_synch_job` placeholder with real sync workflow:
	- write/update audience payload and targeting contracts for ad channels
	- bridge to `ads-server` or provider connectors
	- persist `crm_ad_campaign_platform_map` states
- Keep integration points with existing real services:
	- use `segmentation_job` outputs for segment freshness
	- preserve compatibility with `analytics_job` downstream verification
- Keep other placeholder services out of this sprint scope unless directly required.

Acceptance Criteria

- `campaign_activation` and `data_synch` run real logic, not sleep placeholders.
- Activation runs are observable and retry-safe.
- Publish and sync paths are idempotent and audited.

Definition of Done

Ad-tech execution path is production-like and orchestrated through real jobs.

### SUBTASK-06: Ad Tracking and Platform Webhook Normalization

Component: `customer360-api/core/routers` + `data-tracking-api`  
Priority: P0 Blocker  
Depends on: SUBTASK-05  
Blocks: SUBTASK-07, SUBTASK-08  
Estimate: 5 pts

Description

Normalize ad platform events into CDP and CRM analytics workflows.

Scope of Work

- Add ingestion endpoints for ad event callbacks:
	- impressions
	- clicks
	- conversions
	- spend updates
- Normalize callbacks into canonical event contracts and map to `campaign_id` plus external IDs.
- Deduplicate repeated callbacks and enforce signature verification.
- Persist attribution-ready facts for analytics rollups.

Acceptance Criteria

- Ad callback events are captured with campaign/profile correlation when available.
- Duplicate callbacks do not inflate metrics.
- Signature checks and error handling are enforced.

Definition of Done

Ad event feedback is reliably ingested and normalized.

### SUBTASK-07: Customer 360 Feedback and Ad Performance Rollups

Component: `backend-system/analytics` + `customer360-api`  
Priority: P0 Blocker  
Depends on: SUBTASK-06  
Blocks: SUBTASK-08  
Estimate: 5 pts

Description

Ensure ad engagement updates profile intelligence and campaign performance surfaces.

Scope of Work

- Update profile attribution touchpoints from ad events.
- Trigger segment refresh behavior after relevant engagement updates.
- Roll up ad metrics into `crm_campaign_performance_daily` and related views.
- Keep tenant-safe and auditable data changes.

Acceptance Criteria

- Profile attribution/touchpoint fields update correctly.
- Segment refresh can be triggered by relevant profile changes.
- Campaign performance views include ad event metrics.

Definition of Done

Ad engagement becomes actionable inside Customer 360 profile and campaign analytics.

### SUBTASK-08: End-to-End Automated Test Suite (Learn from Simulator)

Component: cross-cutting (`all-data-simulator`, `backend-system`, `customer360-api`, `ads-server`)  
Priority: P0 Blocker (Beta Gate)  
Depends on: SUBTASK-01..07  
Estimate: 8 pts

Description

Build a full E2E suite based on proven simulator patterns from:

- `all-data-simulator/web_user_simulator.py`
- `all-data-simulator/test_web_user_simulator.py`
- `all-data-simulator/run_tracking_analytics_e2e.sh`

Required E2E sequence

Select Segment ID -> Sync to CRM routes -> Export Ad Audience Snapshot -> Generate AI Creative Draft -> Generate AI Campaign Draft -> Human Approve -> Publish Activation -> Simulate Ad Callbacks -> Verify profile and performance metrics.

Scope of Work

Phase A - Harness baseline

- Reuse contract-check client pattern (request/response strict assertions).
- Reuse deterministic fixture strategy.
- Reuse bounded polling and timeout handling pattern.
- Reuse dual verification pattern (API result + direct PostgreSQL query).

Phase B - CRM routing and export assertions

- For one selected `segment_id`, assert lifecycle routing tables receive expected rows.
- Assert audience export snapshot counts and eligibility filters are deterministic.

Phase C - AI draft and approval assertions

- Assert AI creative is created as draft.
- Assert AI campaign is created as draft with objective, strategy, schedule, and budget.
- Assert campaign cannot activate until human approval is recorded.

Phase D - Activation and callback assertions

- Mock publish path writes one activation map per configured platform.
- Callback simulation updates event facts and dedup behavior.
- Idempotency and retry paths validated.

Phase E - Feedback and analytics assertions

- Verify profile attribution/timestamp updates and campaign metric updates.
- Validate API summary and direct DB values agree.

Phase F - CI release gate

- Required CI check for Beta.
- Separate fast PR smoke and full nightly profiles.
- Emit actionable failure artifacts.

Acceptance Criteria

- Full required sequence passes with deterministic fixtures.
- CRM routing and audience export behavior are verified exactly.
- AI draft + human approval gate is verified.
- Activation/callback/feedback loop is verified.
- CI gate is green and enforced for release.

Definition of Done

Objective evidence exists that the complete ad-tech workflow works end to end and follows repository-proven test patterns.

## Technical Execution Checklist (Epic Gate)

Use this checklist as the implementation tracker for all technical tasks in this epic.

### A. Task Naming Convention Checklist

- [ ] All sprint tasks use `SUBTASK-XX: <Action + Domain>` format.
- [ ] API route handlers use `verb_noun_scope` naming (example: `sync_segment_adtech`).
- [ ] Service methods use `action_domain_object` naming (example: `generate_ad_campaign_draft`).
- [ ] Dagster assets/ops/jobs use explicit domain prefixes (example: `ad_sync_*`, `ad_activate_*`, `campaign_*`).
- [ ] New PostgreSQL tables follow module prefix rules:
	- CRM module tables use `crm_*`.
	- CDP module tables use `cdp_*`.
	- System tables use `sys_*`.
	- Ad server schema tables remain under `leo_ads.*`.
- [ ] New environment variables for this epic use `CRM_ADTECH_*` plus existing `LEO_AD_*` conventions.

### B. PostgreSQL Data Tables Checklist

- [ ] Create `crm_ad_creatives` table with copy/assets metadata and approvals.
- [ ] Create `crm_ad_campaign_platform_map` table for external platform IDs and sync state.
- [ ] Create `crm_segment_audience_exports` table for immutable export snapshots.
- [ ] Alter `crm_campaign` to add `segment_id`, `ad_creative_id`, approval fields, and strategy/budget AI fields.
- [ ] Create `crm_campaign_content_items` relation table if not already available.
- [ ] Create `crm_ad_sync_runs` audit table.
- [ ] Add FK constraints for all cross-table relationships.
- [ ] Add unique constraints for idempotency-sensitive entities.
- [ ] Add tenant-aware indexes for query hot paths.
- [ ] Add or update RLS policies for all new tenant-scoped tables.
- [ ] Add forward and rollback migration scripts and verify both directions.

### C. Backend Tasks Checklist

- [ ] Implement `POST /api/v1/admin/adtech/sync-segment/{segment_id}` with optional dry-run.
- [ ] Recompute segment membership before export and persist snapshot/run metrics.
- [ ] Implement lifecycle routing logic exactly:
	- `customer` -> `crm_customer_contacts` and `crm_transactions`
	- `lead` -> `crm_lead` and `crm_lead_source`
	- non-customer and non-lead -> `crm_contact`
- [ ] Implement idempotent upsert logic for sync targets and export runs.
- [ ] Implement AI creative draft generation with provider switch (`gemini` / `openai`).
- [ ] Implement creative review APIs (`approve`, `reject`, `edit`) and enforce approval requirement.
- [ ] Implement AI campaign draft creation with objective, strategy, budget, schedule, and content linkage.
- [ ] Enforce campaign state machine so AI-created campaigns remain `Draft` until human approval.
- [ ] Replace `campaign_activation` placeholder with real activation execution flow.
- [ ] Replace `data_synch` placeholder with real audience publish flow.
- [ ] Implement platform callback ingestion and normalization.
- [ ] Implement performance rollup updates and attribution feedback writes.
- [ ] Add E2E automation that follows simulator pattern.

### D. Required Environment Config Checklist

- [ ] Define AI provider selector:
	- `CRM_ADTECH_AI_PROVIDER` (`openai` or `gemini`)
- [ ] Define OpenAI credentials/config when provider is OpenAI:
	- `OPENAI_API_KEY`
	- `OPENAI_MODEL`
- [ ] Define Gemini credentials/config when provider is Gemini:
	- `GEMINI_API_KEY`
	- `GEMINI_MODEL`
- [ ] Define ad-tech defaults:
	- `CRM_ADTECH_DEFAULT_PLATFORM`
	- `CRM_ADTECH_DEFAULT_OBJECTIVE`
	- `CRM_ADTECH_TRACKING_BASE_URL`
- [ ] Define runtime controls:
	- `CRM_ADTECH_BATCH_SIZE`
	- `CRM_ADTECH_RATE_LIMIT_PER_SEC`
	- `CRM_ADTECH_MAX_RETRIES`
	- `CRM_ADTECH_RETRY_BACKOFF_MS`
- [ ] Define callback/webhook security values:
	- `CRM_ADTECH_WEBHOOK_SIGNING_SECRET`
- [ ] Define ads-server connectivity values:
	- `LEO_AD_API_HOST`
	- `LEO_AD_API_PORT`
	- `LEO_AD_DB_HOST`
	- `LEO_AD_DB_PORT`
	- `LEO_AD_DB_NAME`
	- `LEO_AD_DB_USER`
	- `LEO_AD_DB_PASSWORD`
- [ ] Ensure values exist in `.env.example` and active environment files used by services.

### E. Definition of Ready and Done Checklist

- [ ] Ready: all required schema migrations are reviewed before backend implementation starts.
- [ ] Ready: all required environment keys are documented and available in deployment manifests.
- [ ] Done: unit tests pass for services, repositories, and routers touched by this epic.
- [ ] Done: E2E workflow passes from segment sync to approved campaign activation and feedback updates.
- [ ] Done: audit logs and tenant-isolation behavior are verified for all new flows.

## Out of Scope (v2.1+)

- Automated budget optimization loops.
- Autonomous bid strategy tuning.
- Multi-touch attribution beyond current rollup scope.
- Full modernization of unrelated placeholder services.

## Non-Functional Requirements (Beta Gates)

- Tenant isolation and RLS safety.
- SQL safety for generated filters and dynamic queries.
- Audit logging for sync, creative generation, approval, activation, and callback actions.
- Secrets management for AI and ad-platform credentials.
- Retry, idempotency, and circuit-breaker controls for publish/callback workflows.
