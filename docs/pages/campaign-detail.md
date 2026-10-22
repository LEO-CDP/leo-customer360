---
title: Campaign detail page
page: "/campaigns/:id"
---

# Campaign detail page

> **Vietnamese version:** [`campaign-detail-vi.md`](campaign-detail-vi.md)

This page shows one marketing campaign (`crm_campaign`): its operational metadata, target audience segment, AI strategy, pinned customer journey workflow (`crm_flow_version`), performance report, approval history, and A/B experiments. Open it by selecting a campaign row on the **Campaigns** page. The header shows the campaign name, runtime status, and approval status.

## What you can do here
- **Go back / Edit**: select **Back to campaigns** or **Edit** (opens the Campaign Editor).
- **Submit for approval**: moves a complete `Draft` campaign into `InReview`.
- **Review the workflow**: **Approve workflow** and **Reject workflow** appear only for a tenant admin (including `root` / Super Admin) when the pinned workflow version is `InReview`. User-saved workflows are normally already `Approved`; AI-authored versions start `InReview` and require review by a different human user.
- **Approve or reject the campaign**: **Approve campaign** and **Reject campaign** appear only for a tenant admin when the campaign is `InReview` and the workflow is `Approved`.
- **Start the campaign**: appears only for a tenant admin after both the workflow and campaign are `Approved`. It calls `POST /api/v1/admin/campaigns/{campaign_id}/activate` to submit `campaign_activation_job` to Dagster.
- **Copy the segment ID**: **Copy ID** in the Target audience card.
- **Switch tabs**: Overview, Strategy, Content plan, Performance report, History, and Experiments.
- **Refresh the report**: pick a start and end date, then select **Refresh**.
- **Manage A/B experiments**: enter a name, choose a primary metric (`Conversions`, `Revenue`, `ROAS`, or `Conversion rate`), add at least two variants with a segment and traffic %, mark one control, allocate `100%`, and select **Create experiment**. Change experiment status (`Draft`, `Running`, `Paused`, `Completed`, `Cancelled`) or select **Select** in the Winner column to complete it.

## Tabs of the page
- **Overview**: read-only **Campaign metadata**, **Target audience** card (segment name, tag, domain, profile count, `Ready` or `Needs attention`, Segment ID), saved customer journey workflow and its version/approval state, plus Campaign code, Owner, Channel, Platform, Language, Schedule, Budget, Objective, Description, Keywords, Created, and Updated.
- **Strategy**: **Strategy and agent provenance** — strategy summary, AI plan, and agent provenance (read-only).
- **Content plan**: table of Position, Title, Type, and Role.
- **Performance report**: date range, **Refresh**, daily table of Date, Spend, Impressions, Clicks, Conversions, and Revenue, plus top summary cards (Spend, Impressions, Conversions, ROAS).
- **History**: **Approval and audit history** — review decisions and `sys_audit_log` entries with reasons and timestamps.
- **Experiments**: **A/B targeting experiments** (`crm_campaign_experiments`) — create form and experiment list with variants, allocation, Conversions, CVR, ROAS, and winner.

## Common questions
- **Why can't I approve or reject?** Review buttons only appear for a tenant admin (`admin` or `root`) when the workflow or campaign state is `InReview`.
- **What do the approval states mean?** `Draft` is not submitted, `InReview` awaits review, `Approved` is signed off, and `Rejected` was sent back for changes.
- **What is the launch sequence?** Save the campaign → **Submit for approval** → approve workflow (if `InReview`) → **Approve campaign** → **Start campaign**.
- **What happens after Start campaign?** The campaign becomes `Running` and Dagster executes `campaign_workflow_job` per profile (Email, Zalo ZNS, SMS, Push, or Meta/Google/TikTok audience sync).
- **Why is the report showing lifetime totals?** When daily metrics are unavailable for the range, the page falls back to lifetime aggregate totals with a warning.


