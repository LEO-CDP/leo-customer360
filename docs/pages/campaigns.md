---
title: Campaigns page
page: "/campaigns"
---

# Campaigns page

> **Vietnamese version:** [`campaigns-vi.md`](campaigns-vi.md)

This page is the marketing campaign performance dashboard (`crm_campaign`). It lists every campaign in the tenant with its runtime status, approval state, spend, and conversion results, alongside headline KPIs and charts. Open it from the **Campaigns** tab in the top navigation. Selecting a row opens that campaign's detail page.

## What you can do here
- **Create a campaign**: select **Create campaign** to open the Campaign Editor in create mode.
- **Refresh / Search**: select **Refresh** to reload data, or search by campaign name or code.
- **Filter**: Status (`All Status`, `Active`, `Paused`, `Draft`, `Completed`), Channel (`Paid Search`, `Paid Social`, `Push Notification`, `Email`, `Video`), Platform (`Google`, `Meta`, `TikTok`, `Zalo`, `Adjust`, `YouTube`), and Objective (`App Install`, `Conversions`, `Leads`, `Awareness`, `Retention`).
- **Sort & Reset**: sort by `Spend`, `Impressions`, `Clicks`, `CTR`, `Conversions`, `CPA`, `Revenue`, or `ROAS` (`DESC` / `ASC`), or select **Reset** to clear all filters.
- **Chart controls**: change the spend trend range (`7d`, `30d`, `90d`) or switch the Top 5 Campaigns metric (`Conversions` or `ROAS`).
- **Open / Edit / Paginate**: click a row or **Details** to open the detail page, select **Edit** to open the editor, or select **Load more**.

## Campaign lifecycle

1. Select **Create campaign**, configure the segment and workflow in the editor, and save.
2. Open the campaign detail page and select **Submit for approval** (`Draft` → `InReview`).
3. If the pinned workflow version is `InReview` (AI-authored), a tenant admin reviews it first. User-saved workflows are already `Approved`; a tenant admin then approves the campaign.
4. A tenant admin selects **Start campaign** to submit `campaign_activation_job` to Dagster, marking the campaign `Running` and executing `campaign_workflow_job`.

Only tenant admins (`admin` or `root` / Super Admin) can approve workflows, approve/reject campaigns, or start a campaign.

## Sections of the page
- **KPI cards**: Campaigns, Spend, Impressions, Clicks, CTR, Conversions, CVR, Revenue, and ROAS.
- **Charts**: **Campaign Spend Trend** (`7d`, `30d`, `90d`) and **Top 5 Campaigns** (`Conversions` or `ROAS`).
- **Campaign Performance table**: columns Campaign, Status, Channel, Spend, Impr., CTR, Conv., CPA, and ROAS, with **Edit** and **Details** actions.

## Common questions
- **What do the campaign statuses mean?** `Draft` is not running yet, `Active` is configured/live, `Running` means the Dagster workflow is executing, `Paused` is halted temporarily, `Completed` is finished, and `Failed` indicates a runtime error.
- **What is the difference between status and approval state?** Status tracks operational execution (`Draft`, `Running`, `Paused`, `Completed`, `Failed`), while approval state tracks governance (`Draft`, `InReview`, `Approved`, `Rejected`).
- **Who can start a campaign?** Only a tenant admin (`admin` or `root`) after both the campaign and pinned workflow are `Approved`.
- **What does ROAS mean?** Return on Ad Spend (`Revenue / Spend`); higher is better.


