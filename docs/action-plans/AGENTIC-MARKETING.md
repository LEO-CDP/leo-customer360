# CRM and Agentic-AI Campaign Product Checklist

This checklist is the frontend delivery baseline for Email, Zalo OA, and Ad Tech campaigns. It is derived from `database-init/database-schema.sql` and the three agentic marketing action plans under `docs/action-plans/`.

Contract status used below:

- **Available**: implemented in the current schema/API and usable by the frontend.
- **Required**: defined by the product flow but must be delivered by backend/schema work before the frontend task can pass UAT.
- The frontend remains a presentation and orchestration client. Segmentation, eligibility, AI generation, approval enforcement, CRM routing, dispatch, retry, and tenant isolation remain server responsibilities.

## Product Overview Checklist

### Audience and CRM readiness

- [ ] Marketer can select one active `segment_id`, inspect its rules, size, freshness, and matched-profile sample.
- [ ] Segment membership is recomputed before activation and the UI polls the asynchronous run to completion.
- [ ] A dry run shows deterministic matched, customer, lead, contact, eligible, skipped, and error counts before any CRM write or channel export.
- [ ] Lifecycle routing is visible and enforced server-side: `customer` to customer contacts/transactions, `lead` to leads/lead source, and every other lifecycle stage to contacts.
- [ ] Audience snapshots and sync runs are immutable, auditable, retry-safe, tenant-scoped, and linked to the campaign.
- [ ] Channel eligibility explains exclusions without exposing raw secrets or unnecessary PII.

### Agentic campaign governance

- [ ] A marketer supplies the objective, audience, offer, language, brand constraints, dates, budget, and channel constraints.
- [ ] AI provider selection supports Gemini or OpenAI-compatible providers when enabled by the backend.
- [ ] AI output is always saved as a draft; the agent cannot approve, schedule, publish, or send.
- [ ] Generated strategy and content show provenance, model/provider, generation time, validation warnings, and editable variants.
- [ ] Template/creative and campaign approvals are separate human decisions with reviewer, timestamp, comment, and audit history.
- [ ] Rejection returns the asset to an editable state; material edits after approval require re-review.
- [ ] Only authorized human roles can approve, schedule, pause, resume, cancel, or retry activation.

### Activation and feedback

- [ ] Preflight validates approval, audience freshness, consent/suppression, account/sender configuration, content variables, dates, budget, and rate limits.
- [ ] Activation uses an explicit confirmation step and idempotency key; double-clicks cannot create duplicate runs.
- [ ] Campaign detail shows run state, progress, eligible/sent/delivered/failed counts, retries, and actionable errors.
- [ ] Email open/click/bounce/complaint, Zalo delivered/read/click/opt-out, and Ad impression/click/conversion/spend events are correlated and deduplicated server-side.
- [ ] Campaign analytics retain the existing KPI, trend, top-campaign, and performance-table experience, with channel-specific funnel metrics and date filters.
- [ ] Feedback updates Customer 360 touchpoints and downstream segment freshness without cross-tenant data exposure.

### Release gates

- [ ] All mutation APIs reject missing tenant context and enforce permissions server-side.
- [ ] Loading, empty, validation, partial-success, permission-denied, rate-limit, provider-outage, and retry states are designed and tested.
- [ ] Destructive or billable actions require confirmation and never rely on optimistic success alone.
- [ ] Keyboard navigation, focus management, labels, contrast, responsive layouts, and screen-reader status announcements pass accessibility review.
- [ ] Frontend contract tests cover all required response states; E2E UAT uses deterministic fixtures and mock channel providers.

## CRM Frontend TODO

### [ ] FE-CRM-01: Build the CRM audience sync workspace

**Problem definition:** Marketers can inspect segments, but cannot run and audit the required segment-to-CRM routing workflow before creating a campaign.

**Solution details:** Add an audience activation panel to segment detail with freshness, matched-profile sample, dry-run action, routing/eligibility summary, commit action, run progress, and run history. Show aggregate exclusion reasons; profile-level details must be permission-gated and mask PII.

**Required API:** **Available** `GET /segments/{segment_id}`, `GET /segments/{segment_id}/matched-profiles`, `GET /segments/{segment_id}/matched-profiles/count`, `POST /segments/{segment_id}/recompute`, and `GET /segments/admin/recompute-status/{run_id}`. **Required** `POST /api/v1/admin/crm/sync-segment/{segment_id}?dry_run=true|false`, `GET /api/v1/admin/crm/sync-runs/{sync_run_id}`, and `GET /api/v1/admin/crm/sync-runs?segment_id={segment_id}`.

**Data model:** `cdp_segments`, `cdp_master_profiles`, `crm_segment_sync_runs`, `crm_customer_contacts`, `crm_transactions`, `crm_lead`, `crm_lead_source`, and `crm_contact`.

**Data attributes:** `segment_id`, `segment_name`, `segment_tag`, `member_count`, `last_computed_at`, `sync_run_id`, `status`, `dry_run`, `matched_count`, `customer_count`, `lead_count`, `contact_count`, `skipped_count`, `error_count`, `error_message`, `started_at`, and `finished_at`.

**Logical flow:** Select segment -> inspect/sample -> recompute -> poll -> dry run -> review routing and exclusions -> confirm sync -> poll run -> open audit result. Disable commit until recompute and dry run succeed.

**UAT description:** Given a deterministic segment containing customer, lead, and other lifecycle profiles, the marketer sees the expected three route counts, commits once, observes completion, and sees the same counts on rerun without duplicate CRM facts. Switching tenant cannot reveal or operate on the first tenant's run.

### [ ] FE-CRM-02: Add CRM entity drill-down from sync results

**Problem definition:** Aggregate sync counts provide no operational path to inspect the CRM records created or skipped by a run.

**Solution details:** Make each routing count open a filtered, paginated result drawer with record state, source profile link, lead source, last update, and skip/error reason. Reuse the shared table and profile routing patterns; do not permit direct correction of source profile data from this drawer.

**Required API:** **Available** generic CRUD reads for `GET /leads`, `GET /lead-sources`, and `GET /contacts`. **Required** `GET /api/v1/admin/crm/sync-runs/{sync_run_id}/results?route={route}&status={status}&skip={skip}&limit={limit}` plus a customer-route projection that joins contact/transaction facts without `SELECT *`.

**Data model:** `crm_segment_sync_runs`, `cdp_master_profiles`, `crm_customer_contacts`, `crm_transactions`, `crm_lead`, `crm_lead_source`, and `crm_contact`.

**Data attributes:** `sync_run_id`, `master_profile_id`, `route`, `record_id`, `full_name`, masked `email`, masked `phone`, `lead_source_id`, `source_system`, `result_status`, `skip_reason`, `error_code`, and `updated_at`.

**Logical flow:** Open completed run -> choose route/status -> load result page -> inspect record -> navigate to Customer 360 profile or CRM entity -> return with filters preserved.

**UAT description:** A user can reconcile every aggregate bucket with paginated detail, PII is masked for non-privileged roles, profile/entity links resolve correctly, and an empty or partially failed route has an explicit state.

## Marketing Campaign Frontend TODO List

### [ ] FE-CAM-01: Split Campaigns into Operations and Analytics views

**Problem definition:** The current `/campaigns` screen is a performance dashboard only; it cannot manage drafts, approvals, schedules, or activation states.

**Solution details:** Preserve the existing dashboard as an Analytics tab and add an Operations tab with campaign list, approval queue, channel/status filters, owner, audience, schedule, and primary next action. Add `/campaigns/:id` for an overview, audience, content, approval history, execution, and results workspace.

**Required API:** **Available** `GET /campaigns`, `GET /campaigns/{campaign_id}`, `GET /campaigns/analytics`, `/summary`, `/spend-trend`, and `/top`. **Required** campaign detail projection `GET /api/v1/campaigns/{campaign_id}/workspace` and `GET /api/v1/campaigns/approvals?status=InReview`.

**Data model:** `crm_campaign`, `crm_campaign_content_items`, `cdp_segments`, `crm_email_templates`, `crm_campaign_member`, `crm_campaign_performance_daily`, and `sys_audit_log`; channel tables join when present.

**Data attributes:** `campaign_id`, `campaign_code`, `name`, `description`, `status`, `approval_status`, `channel`, `platform`, `objective`, `segment_id`, `template_id`, `start_date`, `end_date`, `budget_amount`, `currency`, `user_id`, `approved_by`, `approved_at`, `strategy_summary`, `ai_plan`, and `created_at`.

**Logical flow:** Open Campaigns -> default to Operations -> filter/open campaign -> complete the next valid action -> view Analytics after activation. Route and filters remain stable on refresh/back navigation.

**UAT description:** Draft and in-review campaigns are discoverable independently of campaigns with performance data; a user opens a campaign and sees consistent audience, content, governance, execution, and result state without breaking the existing analytics dashboard.

### [ ] FE-CAM-02: Build the guided agentic campaign composer

**Problem definition:** Campaign creation requires coordinated audience, channel, objective, content, schedule, and governance inputs, which generic campaign CRUD cannot safely collect.

**Solution details:** Build a resumable step flow: Brief -> Audience -> Channel -> AI content -> Strategy and schedule -> Review. Validate each step through the API, show audience freshness/eligibility, and save only drafts. The UI may select a provider but never receives provider credentials.

**Required API:** **Available** segment and content-item reads plus generic `POST/PATCH /campaigns`. **Required** `POST /api/v1/agentic/{channel}/content:generate`, `POST /api/v1/agentic/{channel}/campaigns:plan`, `POST /api/v1/campaigns/{campaign_id}/preflight`, and draft autosave support through validated campaign APIs.

**Data model:** `crm_campaign`, `cdp_segments`, `cdp_content_items`, `crm_campaign_content_items`, channel template/creative tables, and `sys_audit_log`.

**Data attributes:** `channel`, `segment_id`, `objective`, `offer`, `lang`, `locale`, `tone`, `brand_constraints`, `start_date`, `end_date`, `budget_amount`, `currency`, `provider`, `strategy_summary`, `ai_plan`, selected `content_item_id` values, validation warnings, and draft revision.

**Logical flow:** Create campaign -> enter brief -> select fresh segment -> choose one channel -> generate/edit channel asset -> request strategy -> edit dates/budget/content -> run preflight -> save Draft -> submit for review. Leaving and returning restores the latest server draft.

**UAT description:** A marketer can complete each channel path with valid inputs, recover a saved draft, correct field-level API errors, and submit an `InReview` campaign. No generated campaign can become Approved, Scheduled, or Running through this flow.

### [ ] FE-CAM-03: Implement human review, approval, and audit history

**Problem definition:** AI-generated assets and campaigns require a hard human gate, but the current frontend has no review queue or controlled state transitions.

**Solution details:** Add side-by-side brief/output review, warnings, content preview, audience/schedule/budget summary, reviewer comment, approve/reject actions, and immutable audit timeline. Hide actions without permission, but treat API authorization as authoritative. Material edits to approved content visibly invalidate approval.

**Required API:** **Required** `POST /api/v1/{asset_type}/{asset_id}/submit-review`, `/approve`, `/reject`; `POST /api/v1/campaigns/{campaign_id}/submit-review`, `/approve`, `/reject`; and `GET /api/v1/audit-log?entity_type={type}&entity_id={id}`. Responses must return allowed transitions and current revision to prevent stale approvals.

**Data model:** `crm_campaign`, `crm_email_templates`, future `crm_zalo_templates`, future `crm_ad_creatives`, `sys_user`, `sys_user_role`, and `sys_audit_log`.

**Data attributes:** `status`, `approval_status`, `revision`, `submitted_by`, `submitted_at`, `approved_by`, `approved_at`, `rejected_by`, `rejected_at`, `review_comment`, `action`, `before_data`, `after_data`, `request_id`, and `success`.

**Logical flow:** Open review queue -> inspect brief, asset, strategy, audience, and preflight -> approve or reject with comment -> API validates role/revision -> refresh allowed actions and timeline. Rejection returns to Draft; material edits require a new submission.

**UAT description:** An unauthorized user cannot approve through hidden controls or a direct request; an authorized reviewer can reject and later approve the corrected revision; stale simultaneous approval receives a conflict and refresh prompt; every transition appears in the audit timeline.

### [ ] FE-CAM-04: Build activation preflight and run monitoring

**Problem definition:** Scheduling or publishing can incur cost and contact customers, but there is no consolidated safety check or observable run status.

**Solution details:** Add a preflight checklist and typed confirmation before activation. Show immutable audience snapshot, approval state, schedule, sender/account/platform readiness, estimated recipients/budget, warnings/blockers, run progress, failure groups, and safe retry/cancel actions returned by the API.

**Required API:** **Required** `POST /api/v1/campaigns/{campaign_id}/preflight`, `POST /api/v1/campaigns/{campaign_id}/activate` with `Idempotency-Key`, `GET /api/v1/campaigns/{campaign_id}/runs`, `GET /api/v1/campaign-runs/{run_id}`, and server-defined `/pause`, `/resume`, `/cancel`, or `/retry` actions.

**Data model:** `crm_campaign`, sync/export snapshot tables, channel dispatch/platform-map tables, `crm_campaign_performance_daily`, and `sys_audit_log`.

**Data attributes:** `run_id`, `campaign_id`, `snapshot_id`, `status`, `allowed_actions`, `total_count`, `processed_count`, `success_count`, `failure_count`, `retry_count`, `blockers`, `warnings`, `started_at`, `finished_at`, and `error_summary`.

**Logical flow:** Open approved campaign -> run preflight -> resolve blockers -> confirm schedule/activation -> submit once -> poll run -> inspect failures -> invoke only allowed recovery action -> transition to results.

**UAT description:** Activation is blocked for unapproved, stale-audience, invalid-date, missing-account, or invalid-content campaigns. Repeated submission with the same idempotency key creates one run, progress survives page reload, and retry does not resend successful recipients.

## Email Campaign Frontend TODO List

### [ ] FE-EMAIL-01: Build email template authoring and campaign delivery views

**Problem definition:** The schema contains governed email templates, but the UI and specialized API do not expose AI drafting, safe editing, preview, approval, eligibility, or delivery feedback.

**Solution details:** Add an email asset library and composer for subject, HTML/text variants, merge variables, desktop/mobile preview, test-data rendering, validation, AI variants, revision review, and campaign attachment. Add email preflight and delivery funnel panels with suppression reasons and provider errors.

**Required API:** **Required** `GET/POST/PATCH /api/v1/email/templates`, `POST /api/v1/email/templates:generate`, template review endpoints, `POST /api/v1/admin/crm/sync-segment/{segment_id}`, `POST /api/v1/email/templates/{template_id}/preview`, `POST /api/v1/email/templates/{template_id}/test-send`, campaign planning/activation APIs, and email delivery analytics/suppression summaries.

**Data model:** `crm_email_templates`, `crm_campaign`, `crm_campaign_content_items`, `crm_segment_sync_runs`, `cdp_master_profiles`, `cdp_content_items`, dispatch logs defined by the email backend, and `crm_campaign_performance_daily`.

**Data attributes:** `template_id`, `name`, `subject`, `html_body`, `text_body`, `variables`, `status`, `created_by`, `approved_by`, `approved_at`, `segment_id`, `communication_preferences.email_opt_in`, masked `email`, `provider_message_id`, delivery `status`, `sent_at`, `delivered_at`, `opened_at`, `clicked_at`, `bounce_type`, and `complaint_at`.

**Logical flow:** Select audience -> dry-run email eligibility -> generate or create template -> edit and preview with sample data -> validate unsubscribe/name variables and HTML -> submit/approve template -> plan/approve campaign -> test send -> preflight -> activate -> monitor sent/delivered/open/click/bounce/complaint.

**UAT description:** AI output is stored as Draft, unsafe HTML and missing `unsubscribe_url` block review, previews escape sample data correctly, only an approved template can pass campaign preflight, suppressed or opted-out profiles are excluded, duplicate callbacks do not inflate the displayed funnel, and test sends are clearly separated from production metrics.

## Zalo Campaign Frontend TODO List

### [ ] FE-ZALO-01: Build Zalo OA account, template, eligibility, and dispatch views

**Problem definition:** Zalo activation needs OA selection, phone/consent eligibility, governed short-message templates, and delivery/read feedback, but no Zalo schema or API currently exists.

**Solution details:** After backend foundation lands, add OA account health selection, Zalo template library/composer, variable and length validation, message preview, AI variants, approval, eligible-audience summary, and dispatch funnel. Never render access/refresh tokens in the browser.

**Required API:** **Required** `GET /api/v1/zalo/oa-accounts`, `GET/POST/PATCH /api/v1/zalo/templates`, `POST /api/v1/zalo/templates:generate`, template review endpoints, `POST /api/v1/admin/zalo/sync-segment/{segment_id}?dry_run=true|false`, Zalo campaign planning/activation APIs, `GET /api/v1/zalo/dispatches`, and suppression/performance summary APIs.

**Data model:** **Required schema** `crm_zalo_templates`, `crm_zalo_oa_accounts`, `crm_zalo_dispatch_logs`, `crm_zalo_suppression`, and `crm_zalo_sync_runs`; shared `crm_campaign`, `crm_campaign_content_items`, `cdp_segments`, `cdp_master_profiles`, and `cdp_content_items`.

**Data attributes:** `oa_account_id`, `oa_name`, `oa_id`, account `status`, `template_id`, `message_text`, `variables`, CTA/short-link variants, template `status`, `phone_hash`, `communication_preferences`, `suppression_reason`, `provider_message_id`, dispatch `status`, `sent_at`, `delivered_at`, `read_at`, `error_code`, and aggregate click/opt-out counts.

**Logical flow:** Select segment -> recompute -> choose healthy OA -> dry-run phone/consent/suppression eligibility -> generate/edit/preview template -> human approve -> plan/approve campaign -> preflight -> activate -> monitor sent/delivered/read/click/failure/opt-out.

**UAT description:** An unhealthy OA, invalid phone audience, missing consent, suppressed phone hash, over-limit message, unapproved template, or unapproved campaign blocks activation. A mocked OA run displays correlated status updates, deduplicates repeated callbacks, and removes opted-out identities from a later dry run.

## Ad Tech Campaign Frontend TODO List

### [ ] FE-AD-01: Build ad audience export, creative studio, and platform activation views

**Problem definition:** The current dashboard reports ad-like metrics, but marketers cannot create an immutable audience export, govern AI creative, configure platform rollout, or trace CRM campaigns to external campaign/ad set/ad identifiers.

**Solution details:** After backend foundation lands, add platform and identifier-eligibility selection, dry-run/export snapshot summary, creative variants by platform constraints, asset URL/media-spec preview, budget/KPI plan, approval, platform publish mapping, and activation status. Reuse the existing analytics tab for aggregate outcomes and add impression-to-conversion details.

**Required API:** **Required** `POST /api/v1/admin/adtech/sync-segment/{segment_id}?dry_run=true|false`, `GET /api/v1/adtech/audience-exports/{export_id}`, `GET/POST/PATCH /api/v1/adtech/creatives`, `POST /api/v1/adtech/creatives:generate`, creative review endpoints, Ad Tech campaign planning/preflight/activation APIs, and `GET /api/v1/adtech/campaigns/{campaign_id}/platform-mappings`.

**Data model:** **Required schema** `crm_ad_creatives`, `crm_ad_campaign_platform_map`, `crm_segment_audience_exports`, and `crm_ad_sync_runs`; shared `crm_campaign`, `crm_campaign_content_items`, `cdp_segments`, `cdp_master_profiles`, `cdp_content_items`, `crm_campaign_performance_daily`, plus existing `leo_ads` campaign/creative/ad/placement/targeting entities behind service APIs.

**Data attributes:** `export_id`, `snapshot_at`, `total_members`, `eligible_members`, `export_status`, `creative_id`, `format`, `headline`, `body_text`, `cta_text`, `asset_url`, creative `status`, `platform_code`, `external_campaign_id`, `external_adset_id`, `external_ad_id`, `sync_status`, `last_synced_at`, `budget_amount`, `currency`, KPI plan, `impressions`, `clicks`, `conversions`, `spend`, and estimated revenue.

**Logical flow:** Select segment/platforms -> recompute and dry run -> create immutable export -> generate/edit platform-valid creative variants -> approve creative -> generate strategy, budget, KPI, schedule, and content plan -> approve campaign -> preflight -> publish -> inspect each platform mapping -> monitor impression/click/conversion/spend feedback.

**UAT description:** Export counts remain fixed after the source segment changes, a rerun creates or reuses snapshots according to the server idempotency contract, invalid platform copy or missing assets block review, only approved creative/campaign combinations publish, external IDs are traceable, duplicate callbacks do not inflate metrics, and one platform failure does not hide successful platform activations.

## Recommended Delivery Order

1. `FE-CAM-01` and the shared campaign workspace contracts.
2. `FE-CRM-01` and `FE-CRM-02` for trusted audience preparation.
3. `FE-CAM-02`, `FE-CAM-03`, and `FE-CAM-04` for the governed shared workflow.
4. `FE-EMAIL-01`, because its core schema is already present.
5. `FE-ZALO-01` after Zalo schema/API blockers are complete.
6. `FE-AD-01` after Ad Tech schema/API and the Customer 360-to-`ads-server` bridge are complete.