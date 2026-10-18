---
title: Campaigns page
page: "/campaigns"
---

# Campaigns page

This page is the marketing campaign performance dashboard. It lists every campaign in the tenant with its runtime status, approval state, spend and results, and shows headline KPIs and charts. Open it from the Campaigns tab in the top navigation. Selecting a row opens that campaign's detail page.

## What you can do here
- **Create a campaign**: "Create campaign" opens the campaign editor in create mode.
- **Refresh the dashboard**: "Refresh" reloads the KPIs, charts and table.
- **Search**: the search box filters by campaign name or code.
- **Filter**: Status (All Status, Active, Paused, Draft, Completed), Channel (All Channels, Paid Search, Paid Social, Push Notification, Email, Video), Platform (All Platforms, Google, Meta, TikTok, Zalo, Adjust, YouTube) and Objective (All Objectives, App Install, Conversions, Leads, Awareness, Retention).
- **Sort**: choose Sort: Spend, Impressions, Clicks, CTR, Conversions, CPA, Revenue or ROAS, then toggle the order button between DESC and ASC.
- **Reset**: "Reset" clears every filter and the sorting.
- **Change the spend trend range**: 7d, 30d or 90d.
- **Switch the top-campaigns metric**: Conversions or ROAS.
- **Open a campaign**: click a row or its "Details" action.
- **Edit a campaign**: the "Edit" action on a row opens the editor.
- **Load more**: "Load more" fetches the next page of rows.

## Campaign lifecycle

Campaign creation and campaign execution are separate steps:

1. Select **Create campaign**, complete the editor and save the campaign/workflow.
2. Open the campaign details and select **Submit for approval**. The campaign moves from `Draft` to `InReview`.
3. If the workflow is `InReview`, a tenant admin reviews it first. User-saved workflows are normally already `Approved`; the UI then presents campaign approval when the campaign is `InReview`.
4. A tenant admin selects **Start campaign**. The API submits `campaign_activation_job` to Dagster, which validates the campaign, marks it `Running` and starts the saved customer journey for the selected audience.

Regular users can create, edit and submit campaigns according to their permissions, but only tenant admins can approve workflow versions, approve/reject campaigns, or start a campaign. The `root` / Super Admin role is a tenant-admin role with the highest authority. The API is the final permission and state check.

## Sections of the page
- **KPI cards**: Campaigns, Spend, Impressions, Clicks, CTR, Conversions, CVR, Revenue and ROAS.
- **Campaign Spend Trend**: a line chart of daily spend for the chosen range.
- **Top 5 Campaigns**: a bar chart of the top campaigns by Conversions or ROAS.
- **Filters**: search, status, channel, platform, objective, sort and reset.
- **Campaign Performance table**: columns Campaign, Status, Channel, Spend, Impr., CTR, Conv., CPA and ROAS. Each row has Edit and Details actions. Open Details to access the approval and start controls. If nothing matches it says "No campaigns match your filters".

## Common questions
- **What do the campaign statuses mean?** Draft is not running yet, Active is configured/live according to the campaign record, Running means the activation workflow is executing, Paused is stopped temporarily, Completed is finished and Failed indicates runtime failure.
- **What is the difference between status and approval state?** Status describes operational execution (`Draft`, `Running`, `Paused`, `Completed`, or `Failed`). Approval state describes governance (`Draft`, `InReview`, `Approved`, or `Rejected`). A workflow has its own approval state, which may already be `Approved` when saved by a user.
- **Who can start a campaign?** Only a tenant admin can use Start campaign, and only after the campaign and its pinned workflow are Approved.
- **Is root allowed to start campaigns?** Yes. `root` / Super Admin is authorized as a tenant admin.
- **Where do I see one campaign's results?** Open its row; the detail page has a Performance report tab.
- **Why is the table empty?** No campaign matches the current filters; use Reset or widen the status filter.
- **How do I change a campaign?** Use the Edit action on its row.
- **What does ROAS mean?** Return on ad spend: revenue divided by spend, so higher is better.
