---
title: Campaign detail page
page: "/campaigns/:id"
---

# Campaign detail page

This page shows one campaign: its operational details, target audience, strategy, content plan, performance report, approval history and A/B experiments. Open it by selecting a campaign row on the Campaigns page. The header shows the campaign name, its status badge and its approval status.

## What you can do here
- **Go back**: "Back to campaigns".
- **Edit**: "Edit" opens the campaign editor.
- **Approve or reject**: "Approve" and "Reject" appear only when the approval status is InReview and you are a tenant admin. Reject asks for a reason.
- **Copy the segment ID**: "Copy ID" in the Target audience card.
- **Switch tabs**: Overview, Strategy, Content plan, Performance report, History and Experiments.
- **Refresh the report**: pick a start and end date, then "Refresh".
- **Create an experiment**: name it, choose a primary metric (Conversions, Revenue, ROAS or Conversion rate), add at least two variants with a target segment and traffic %, mark one control, allocate exactly 100%, then "Create experiment".
- **Add or remove variants**: "Add variant" and the remove button on each variant.
- **Change an experiment's status**: Draft, Running, Paused, Completed or Cancelled.
- **Pick a winning variant**: "Select" in the Winner column marks it the winner and completes the experiment.

## Tabs of the page
- **Overview**: "Campaign metadata" (read-only) with the Target audience card (segment name, tag, domain, profile count, Ready or Needs attention, Segment ID) and the fields Campaign code, Owner, Channel, Platform, Language, Schedule, Budget, Objective, Description, Keywords, Template reference, Created and Updated.
- **Strategy**: "Strategy and agent provenance" — the strategy summary, the AI plan and the agent provenance, all read-only.
- **Content plan**: a table of Position, Title, Type and Role.
- **Performance report**: a date range and "Refresh", a warning when data is limited, and a daily table of Date, Spend, Impressions, Clicks, Conversions and Revenue. The Spend, Impressions, Conversions and ROAS cards above the tabs always show the campaign totals.
- **History**: "Approval and audit history" — the review decisions and audit entries with their reasons and timestamps.
- **Experiments**: "A/B targeting experiments" — the create form and the list of experiments with their variants, allocation, Conversions, CVR, ROAS and winner.

## Common questions
- **Why can't I approve or reject?** The buttons only show for a tenant admin when the approval status is InReview.
- **What do the approval statuses mean?** Draft is not submitted, InReview is waiting for a reviewer, Approved is signed off and Rejected was sent back.
- **Why is the report showing lifetime totals?** When the single-campaign report is unavailable the page falls back to lifetime aggregate data and shows a warning.
- **Where do I see results?** The Performance report tab, plus the Spend, Impressions, Conversions and ROAS cards at the top.
- **Which variant is winning?** The Experiments tab shows Conversions, CVR and ROAS per variant; the winner is marked in the Winner column.
