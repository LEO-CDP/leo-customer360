# LEO CDP v2.0 Beta - Omnichannel Agentic Marketing Readiness

## Document Purpose

This document consolidates and normalizes planning from three action-plan knowledge sources:

- `docs/action-plans/AGENTIC-EMAIL-MARKETING-FLOW.md`
- `docs/action-plans/AGENTIC-AD-TECH-FLOW.md`
- `docs/action-plans/AGENTIC-ZALO-MARKETING-FLOW.md`

It is updated to be realistic and code-accurate against the current repository state as of 2026-09-08.

## Scope and Ground Rules

- Product scope: Customer 360 + CRM + channel activation layers.
- Channels in scope: Email, Ad Tech, Zalo OA.
- Status labels:
  - DONE: implemented in code and usable now.
  - PARTIAL: scaffold exists but key business logic is missing.
  - BLOCKER: required capability not implemented.

## 1) Key Information Summary From the 3 Source Plans

### 1.1 Email Plan Summary

- Target flow: `segment_id` selection -> lifecycle-based CRM routing -> AI template draft -> AI campaign draft -> human approval -> dispatch -> tracking -> feedback loop.
- Data model direction:
  - `crm_email_templates`
  - campaign linkage (`segment_id`, `template_id`)
  - run/audit tables for deterministic sync and dispatch tracking.
- Orchestration direction:
  - convert `campaign_activation` and `email_engine` Dagster code locations from placeholders to real jobs.
- Governance direction:
  - AI outputs stay `Draft` until explicit human approval.

### 1.2 Ad Tech Plan Summary

- Target flow: `segment_id` selection -> CRM routing + deterministic audience export -> AI ad creative draft -> AI campaign draft -> human approval -> publish -> callback normalization -> feedback.
- Data model direction:
  - `crm_ad_creatives`
  - `crm_ad_campaign_platform_map`
  - `crm_segment_audience_exports`
  - ad sync run/audit tables.
- Integration direction:
  - bridge Customer 360 workflows into `ads-server` (`leo_ads` schema) with traceable external IDs.
- Governance direction:
  - AI-created campaign stays `Draft`; human approval required for activation.

### 1.3 Zalo OA Plan Summary

- Target flow: `segment_id` selection -> CRM routing + phone eligibility -> AI Zalo template draft -> AI campaign draft -> human approval -> OA dispatch -> webhook ingestion -> feedback.
- Data model direction:
  - `crm_zalo_templates`
  - `crm_zalo_oa_accounts`
  - `crm_zalo_dispatch_logs`
  - `crm_zalo_suppression`
  - Zalo sync run/audit tables.
- Orchestration direction:
  - convert `campaign_activation` and `notification_engine` placeholders to real Zalo execution jobs.
- Governance direction:
  - AI templates/campaigns are drafts until human approval.

## 2) Reality Check Against Current Repository Code

### 2.1 Implemented Baseline Capabilities

| Capability | Status | Code Evidence |
| :--- | :--- | :--- |
| CRM campaign entities and analytics endpoints | DONE | `customer360-api/core/routers/crm_api.py` (CRUD + `/campaigns/analytics`) |
| Segment matched profiles and recompute trigger | DONE | `customer360-api/core/routers/segment_api.py` (`/{segment_id}/matched-profiles`, `/{segment_id}/recompute`) |
| Generic event ingestion with dedup and identity resolution hints | DONE | `customer360-api/core/routers/events_api.py` (`/events`, `/events/bulk`) |
| Core CRM/CDP schema (campaign, lead, lead source, contact, transactions, content items) | DONE | `database-init/database-schema.sql` |
| Active Dagster jobs for identity, segmentation, analytics | DONE | `backend-system/identity_resolution/dagster_defs.py`, `backend-system/segmentation/dagster_defs.py`, `backend-system/analytics/dagster_defs.py` |
| Ad server runtime and data models (`leo_ads`) | DONE | `ads-server/core/application.py`, `ads-server/model/*.py`, `ads-server/tests/test_api.py` |
| Tracking analytics E2E smoke script | DONE | `all-data-simulator/run_tracking_analytics_e2e.sh` (recent run succeeded) |

### 2.2 Critical Gaps (Not Implemented Yet)

| Capability Gap | Status | Code Evidence |
| :--- | :--- | :--- |
| Email-specific template, suppression, and dispatch schema from plan | BLOCKER | Missing in `database-init/database-schema.sql` |
| Ad-tech bridge tables (`crm_ad_creatives`, platform map, audience export, sync run) | BLOCKER | Missing in `database-init/database-schema.sql` |
| Zalo-specific tables (`crm_zalo_templates`, `crm_zalo_dispatch_logs`, etc.) | BLOCKER | Missing in `database-init/database-schema.sql` |
| Channel-specific sync endpoints (`/admin/crm/sync-segment/*`, `/admin/adtech/*`, `/admin/zalo/*`) | BLOCKER | Not present in `customer360-api/core/routers` |
| Channel-specific webhooks (ESP callbacks, ad callbacks, Zalo OA callbacks) | BLOCKER | Not present in `customer360-api/core/routers` |
| Campaign activation real orchestration | PARTIAL | `backend-system/campaign_activation/dagster_defs.py` is placeholder sleep job |
| Email execution pipeline | PARTIAL | `backend-system/email_engine/dagster_defs.py` is placeholder sleep job |
| Notification/Zalo execution pipeline | PARTIAL | `backend-system/notification_engine/dagster_defs.py` is placeholder sleep job |
| Data sync execution pipeline | PARTIAL | `backend-system/data_synch/dagster_defs.py` is placeholder sleep job |
| End-to-end channel suites (email, ad-tech, zalo) | BLOCKER | No channel-specific E2E suites yet in repo |

### 2.3 Accuracy Corrections Applied In This Consolidation

- Uses `crm_email_templates` naming (not `cdp_email_templates`).
- Treats channel-specific features as planned blockers, not implemented facts.
- Distinguishes real `ads-server` runtime capabilities from missing Customer360-to-AdTech orchestration.
- Keeps current truth that only identity resolution, segmentation, and analytics are fully active Dagster pipelines.

## 3) Consolidated Readiness Matrix (By Channel)

| Capability Domain | Email | Ad Tech | Zalo OA |
| :--- | :--- | :--- | :--- |
| Segment selection and recompute primitives | DONE | DONE | DONE |
| Lifecycle CRM routing engine by `segment_id` | BLOCKER | BLOCKER | BLOCKER |
| Channel template/creative entity model | BLOCKER | BLOCKER | BLOCKER |
| AI draft generation service | BLOCKER | BLOCKER | BLOCKER |
| Human approval gate state machine | BLOCKER | BLOCKER | BLOCKER |
| Dispatch execution pipeline | PARTIAL | PARTIAL | PARTIAL |
| Channel webhook/callback ingestion | BLOCKER | BLOCKER | BLOCKER |
| Closed-loop feedback to profiles and campaign metrics | PARTIAL | PARTIAL | PARTIAL |
| Deterministic channel E2E test suite | BLOCKER | BLOCKER | BLOCKER |

## 4) Realistic P0 Implementation Plan (Cross-Channel)

The following sequence is realistic for current code state and aligns all three channel plans.

### SUBTASK-01: Shared Schema Foundation

- Add missing channel tables and campaign linkage FKs:
  - Email: `crm_email_templates`
  - Ad Tech: `crm_ad_creatives`, `crm_ad_campaign_platform_map`, `crm_segment_audience_exports`
  - Zalo: `crm_zalo_templates`, `crm_zalo_oa_accounts`, `crm_zalo_dispatch_logs`, `crm_zalo_suppression`
- Add shared mapping/audit tables where needed:
  - `crm_campaign_content_items`
  - per-channel sync run tables.
- Add tenant-aware indexes and RLS policies.

### SUBTASK-02: Segment Routing + Eligibility Services

- Implement per-channel admin sync endpoints with dry-run support.
- Reuse existing segment recompute pattern before each sync/export.
- Enforce lifecycle routing exactly:
  - customer -> `crm_customer_contacts` + `crm_transactions`
  - lead -> `crm_lead` + `crm_lead_source`
  - others -> `crm_contact`
- Add channel eligibility filters (email consent, ad identifiers, zalo phone and suppression).

### SUBTASK-03: AI Draft Services (Templates/Creatives)

- Add provider switch for OpenAI/Gemini.
- Add draft persistence and revision metadata.
- Add output guardrails per channel policy.

### SUBTASK-04: Campaign Draft and Approval State Machine

- Extend campaign planning APIs for channel-specific linkage.
- Enforce `Draft -> InReview -> Approved` transitions.
- Block execution until human approval.

### SUBTASK-05: Execution Pipeline Modernization

- Replace placeholder Dagster logic in:
  - `campaign_activation`
  - `email_engine`
  - `notification_engine`
  - `data_synch`
- Ensure idempotent retries and audit logs.

### SUBTASK-06: Channel Event Ingestion and Webhooks

- Add channel callback/webhook routers:
  - email delivery/bounce/open/click
  - ad platform callbacks
  - zalo delivery/read/click/opt-out
- Normalize to canonical event structures with dedup.

### SUBTASK-07: Feedback and Performance Rollups

- Update profile touchpoint and attribution fields from channel events.
- Feed metrics into campaign performance reporting.
- Trigger segmentation refresh when relevant profile state changes.

### SUBTASK-08: E2E and CI Gates

- Build deterministic E2E suites for all three channels.
- Reuse established simulator testing patterns from:
  - `all-data-simulator/web_user_simulator.py`
  - `all-data-simulator/test_web_user_simulator.py`
  - `all-data-simulator/run_tracking_analytics_e2e.sh`
- Add PR smoke + nightly full scenarios.

## 5) Unified Technical Checklist

### A. Naming Convention Checklist

- [ ] Tasks follow `SUBTASK-XX: <Action + Domain>`.
- [ ] API handlers follow `verb_noun_scope`.
- [ ] Service functions follow `action_domain_object`.
- [ ] Dagster ops/jobs use channel prefixes (`email_*`, `adtech_*`, `zalo_*`, `campaign_*`).
- [ ] Table naming is module-consistent:
  - CRM owned entities: `crm_*`
  - CDP entities: `cdp_*`
  - System entities: `sys_*`
  - Ad server native tables remain `leo_ads.*`

### B. PostgreSQL Tables Checklist

- [ ] Email tables are created (`crm_email_templates`, suppression, dispatch/audit where applicable).
- [ ] Ad-tech tables are created (`crm_ad_creatives`, platform map, audience export, sync runs).
- [ ] Zalo tables are created (`crm_zalo_templates`, accounts, dispatch logs, suppression, sync runs).
- [ ] `crm_campaign` is extended with channel linkage and approval metadata.
- [ ] Cross-table FKs and unique constraints are added for idempotency.
- [ ] Tenant indexes and RLS policies are updated for all new tables.
- [ ] Forward and rollback migrations are verified.

### C. Backend Tasks Checklist

- [ ] Segment sync/export endpoints are added for Email, Ad Tech, and Zalo.
- [ ] Lifecycle routing logic is implemented and tested.
- [ ] AI draft services are implemented for all channels.
- [ ] Campaign approval state machine is enforced server-side.
- [ ] Placeholder Dagster jobs are replaced with real workflows.
- [ ] Channel webhook/callback routers are implemented with dedup/signature validation.
- [ ] Feedback rollups update profiles and campaign analytics.
- [ ] Channel E2E suites are implemented and wired to CI.

### D. Required Environment Configuration Checklist

- [ ] Common AI provider keys:
  - `OPENAI_API_KEY`, `OPENAI_MODEL`
  - `GEMINI_API_KEY`, `GEMINI_MODEL`
- [ ] Email execution keys:
  - `CRM_EMAIL_AI_PROVIDER`
  - `CRM_EMAIL_FROM_NAME`, `CRM_EMAIL_FROM_ADDRESS`, `CRM_EMAIL_REPLY_TO`
  - `CRM_EMAIL_UNSUBSCRIBE_BASE_URL`, `CRM_EMAIL_TRACKING_BASE_URL`
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`
- [ ] Ad-tech execution keys:
  - `CRM_ADTECH_AI_PROVIDER`
  - `CRM_ADTECH_DEFAULT_PLATFORM`, `CRM_ADTECH_DEFAULT_OBJECTIVE`
  - `CRM_ADTECH_TRACKING_BASE_URL`, `CRM_ADTECH_WEBHOOK_SIGNING_SECRET`
  - Existing ad server envs remain authoritative in `ads-server/.env.example` (`LEO_AD_*`).
- [ ] Zalo execution keys:
  - `CRM_ZALO_AI_PROVIDER`
  - `CRM_ZALO_OA_APP_ID`, `CRM_ZALO_OA_APP_SECRET`
  - `CRM_ZALO_OA_ACCESS_TOKEN`, `CRM_ZALO_OA_REFRESH_TOKEN`
  - `CRM_ZALO_OA_API_BASE_URL`, `CRM_ZALO_WEBHOOK_SIGNING_SECRET`
  - `CRM_ZALO_TRACKING_BASE_URL`, `CRM_ZALO_UNSUBSCRIBE_BASE_URL`
- [ ] Keys are present in `.env.example` and runtime env files for each service.

### E. Definition of Ready and Done

- [ ] Ready: schema migration package reviewed and approved.
- [ ] Ready: env config contract documented per service/channel.
- [ ] Done: unit tests pass for all touched API, repository, and service modules.
- [ ] Done: E2E flows pass for Email, Ad Tech, and Zalo.
- [ ] Done: tenant isolation, auditability, and idempotency are verified.

## 6) Beta Launch Recommendation (Current State)

- Customer 360 foundation is strong enough for channel build-out.
- Omnichannel agentic marketing execution is not Beta-ready yet.
- Most channel execution capabilities remain blockers due to missing schema, missing APIs, and placeholder orchestration jobs.

Recommended launch posture:

1. Ship foundation-hardening updates continuously.
2. Gate channel Beta by per-channel E2E pass and approval-state enforcement.
3. Launch Email first, then Ad Tech, then Zalo, unless business priority requires different sequencing.