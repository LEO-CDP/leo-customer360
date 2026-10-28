---
title: Campaign editor page
page: "/campaigns/:id/edit"
---

# Campaign editor page

> **Vietnamese version:** [`campaign-editor-vi.md`](campaign-editor-vi.md)

This page creates a new marketing campaign or edits an existing one (`crm_campaign` and its pinned `crm_flow_version`). Open it with **Create campaign** on the Campaigns page or with the **Edit** action on a campaign row.

## What you can do here
- **Create or edit a campaign**: fill in the required fields and select **Save campaign**. A campaign name, an active computed audience segment (`is_active = TRUE`), and a workflow graph with an Event trigger are required.
- **Go back or cancel**: select **Back to campaigns** or **Cancel**.
- **Choose an audience segment**: select **Find segment** to open the **Choose audience segment** modal; search by name or tag and pick one. **Change** replaces it and the clear button removes it.
- **Set campaign metadata**: Name, Campaign code, Operational status, Channel, Platform, Language, Description, Keywords (comma-separated), Start date, End date, Budget amount, and Currency.
- **Set campaign planning fields**: Audience segment, Objective, Strategy summary, and Planning constraints.
- **Design the customer journey**: use the visual workflow studio (`workflow-core.js`) to configure the Event trigger, conditions, waits, and action nodes (`email`, `notification`, `push`, `update_profile`, `run_data_synch_job`).

## Sections of the page
- **Campaign metadata**: name, code, operational status, channel, platform, language, description, keywords, schedule, and budget fields.
- **Campaign planning**: audience segment, objective, strategy summary, and planning constraints. AI plan and agent provenance are read-only in this editor.
- **Customer journey workflow**: loads sample profiles from the selected audience segment for graph design and browser simulation.
- **Footer**: **Cancel** and **Save campaign**. Saving persists the draft and pinned workflow version; it does not approve or start the campaign.

## After saving

Continue on the campaign detail page:

1. Select **Submit for approval** to move a complete `Draft` campaign to `InReview`.
2. If the pinned workflow version is `InReview` (AI-authored), a tenant admin (`admin` or `root`) reviews it first. Workflows saved directly by a user in this editor are saved as `Approved`.
3. A tenant admin approves the campaign after the workflow is `Approved`.
4. A tenant admin selects **Start campaign** to submit `campaign_activation_job` to Dagster.

## Common questions
- **Why can't I save?** A name, an active computed audience segment, and a workflow Event trigger are required, and the end date cannot be before the start date.
- **Can I start the campaign from this page?** No. Submission, approval, and Dagster activation happen on the campaign detail page.
- **What does Operational status mean?** `Draft`, `Active`, `Paused`, and `Completed` can be selected in the editor; `Running` and `Failed` are runtime states.
- **What happens to an approved campaign when I edit it?** Any material edit returns the campaign for fresh review and approval before activation.
- **Where do I see the AI plan?** It is read-only on the campaign detail page's **Strategy** tab.
- **Which admin can approve and start a campaign?** Any tenant admin (`admin`) or `root` / Super Admin.


