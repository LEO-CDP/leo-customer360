---
title: Campaign detail page
page: "/campaigns/:id"
---

# Campaign detail page

This page shows one campaign: its operational details, target audience, strategy, saved customer journey, performance report, approval history and A/B experiments. Open it by selecting a campaign row on the Campaigns page. The header shows the campaign name, its runtime status and its approval status.

## What you can do here
- **Go back**: "Back to campaigns".
- **Edit**: "Edit" opens the campaign editor.
- **Submit for approval**: "Submit for approval" moves a complete `Draft` campaign into the `InReview` queue. This action is available to an authenticated campaign user.
- **Review the workflow**: "Approve workflow" and "Reject workflow" appear only for a tenant admin, including `root` / Super Admin, when the pinned workflow version is `InReview`. Reject asks for a reason. A workflow saved directly by a user in the editor is normally already `Approved`, so no workflow review is required for that version.
- **Approve or reject the campaign**: "Approve campaign" and "Reject campaign" appear only for a tenant admin when the campaign is `InReview`. The UI presents campaign approval after the workflow is approved.
- **Start the campaign**: "Start campaign" appears only for a tenant admin after both the workflow and campaign are `Approved`. It submits `POST /admin/campaigns/{campaign_id}/activate`; the API then submits `campaign_activation_job` to Dagster.
- **Copy the segment ID**: "Copy ID" in the Target audience card.
- **Switch tabs**: Overview, Strategy, Content plan, Performance report, History and Experiments.
- **Refresh the report**: pick a start and end date, then "Refresh".
- **Create an experiment**: name it, choose a primary metric (Conversions, Revenue, ROAS or Conversion rate), add at least two variants with a target segment and traffic %, mark one control, allocate exactly 100%, then "Create experiment".
- **Add or remove variants**: "Add variant" and the remove button on each variant.
- **Change an experiment's status**: Draft, Running, Paused, Completed or Cancelled.
- **Pick a winning variant**: "Select" in the Winner column marks it the winner and completes the experiment.

## Tabs of the page
- **Overview**: "Campaign metadata" (read-only) with the Target audience card (segment name, tag, domain, profile count, Ready or Needs attention, Segment ID), the saved customer journey workflow and its version/approval state, and the fields Campaign code, Owner, Channel, Platform, Language, Schedule, Budget, Objective, Description, Keywords, Created and Updated.
- **Strategy**: "Strategy and agent provenance" — the strategy summary, the AI plan and the agent provenance, all read-only.
- **Content plan**: a table of Position, Title, Type and Role.
- **Performance report**: a date range and "Refresh", a warning when data is limited, and a daily table of Date, Spend, Impressions, Clicks, Conversions and Revenue. The Spend, Impressions, Conversions and ROAS cards above the tabs always show the campaign totals.
- **History**: "Approval and audit history" — the review decisions and audit entries with their reasons and timestamps.
- **Experiments**: "A/B targeting experiments" — the create form and the list of experiments with their variants, allocation, Conversions, CVR, ROAS and winner.

## Common questions
- **Why can't I approve or reject?** The review buttons only show for a tenant admin when the relevant workflow or campaign approval state is `InReview`. If the workflow is already `Approved`, the page shows that state instead of an approval button. The API independently re-checks the tenant-admin role.
- **Does root count as an admin?** Yes. `root` / Super Admin is the highest tenant-admin role and can review, approve and start campaigns.
- **What do the approval states mean?** `Draft` is not submitted, `InReview` is waiting for review, `Approved` is signed off and `Rejected` was sent back for changes.
- **What is the launch sequence?** Save the campaign, select **Submit for approval**, approve the workflow if it is `InReview`, approve the campaign, then select **Start campaign**. The activation API re-checks tenant scope, admin access, segment, pinned workflow and approvals before submitting the Dagster run.
- **What happens after I select Start campaign?** The campaign becomes `Running` when the activation job validates it. The activation job submits the saved per-profile workflow to Dagster; monitor the run and dispatch results from the backend/Dagster UI.
- **Why is the report showing lifetime totals?** When the single-campaign report is unavailable the page falls back to lifetime aggregate data and shows a warning.
- **Where do I see results?** The Performance report tab, plus the Spend, Impressions, Conversions and ROAS cards at the top.
- **Which variant is winning?** The Experiments tab shows Conversions, CVR and ROAS per variant; the winner is marked in the Winner column.
