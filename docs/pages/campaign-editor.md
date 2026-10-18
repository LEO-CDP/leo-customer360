---
title: Campaign editor page
page: "/campaigns/:id/edit"
---

# Campaign editor page

This page creates a new campaign or edits an existing one. Open it with "Create campaign" on the Campaigns page or with the Edit action on a campaign row. The title shows "Create campaign" or "Edit campaign".

## What you can do here
- **Create a campaign**: "Create campaign" on the Campaigns page, fill in the fields and "Save campaign". A name, an active computed audience segment and a workflow with an Event trigger are required.
- **Edit a campaign**: open an existing campaign, change the fields or workflow, and select "Save campaign".
- **Go back or cancel**: "Back to campaigns" or "Cancel".
- **Choose an audience segment**: "Find segment" opens the "Choose audience segment" dialog; search by name or tag and pick one. "Change" replaces it and the clear button removes it. Only active, computed segments are listed.
- **Set campaign metadata**: Name, Campaign code, Operational status, Channel, Platform, Language, Description, Keywords (comma-separated), Start date, End date, Budget amount and Currency.
- **Set campaign planning fields**: Audience segment, Objective, Strategy summary and Planning constraints.
- **Design the customer journey**: use the workflow studio to define the Event trigger and journey actions for the selected audience. The saved workflow is read-only on the detail page.

## Sections of the page
- **Campaign metadata**: name, code, operational status, channel, platform, language, description, keywords, schedule and budget fields.
- **Campaign planning**: audience segment, objective, strategy summary and planning constraints. AI plan and agent provenance are read-only in this editor.
- **Customer journey workflow**: the workflow studio loads profiles from the selected audience for design and local simulation.
- **Footer**: "Cancel" and "Save campaign". Saving persists the campaign and pinned workflow; it does not approve or start the campaign.
- **Choose audience segment dialog**: search, results and "Cancel".

## After saving

The editor saves the campaign as a governed draft. Continue on the campaign detail page:

1. Select **Submit for approval** to move a complete `Draft` campaign to `InReview`.
2. If the pinned workflow version is `InReview`, a tenant admin, including `root` / Super Admin, reviews it on the campaign detail page. Workflows saved directly by a user in this editor are normally saved as `Approved`.
3. A tenant admin approves the campaign after the workflow is approved.
4. A tenant admin selects **Start campaign** to submit `campaign_activation_job` to Dagster.

The API enforces these transitions and tenant-admin permissions; hiding buttons in the UI is only a usability control.

## Common questions
- **How do I create a new campaign?** "Create campaign" on the Campaigns page opens this page empty; fill it in and save.
- **Why can't I save?** A name, an active computed audience segment and a workflow Event trigger are required; the end date cannot be before the start date.
- **Can I start the campaign from this page?** No. Saving only persists the draft. Submit, review, approval and Dagster activation are available on the campaign detail page.
- **What does Operational status mean?** Draft, Active, Paused and Completed can be selected in the editor. Running and Failed are runtime states controlled by activation and execution.
- **What happens to an approved campaign when I edit it?** A material change requires fresh campaign approval. Save the changes, then submit the campaign again from the detail page.
- **Where do I see the AI plan?** It is read-only on the campaign detail page's Strategy tab.
- **Why is there no Approve workflow button?** Workflow review is only needed for an `InReview` workflow version. A workflow saved by a user through this editor is normally already `Approved`; continue with campaign approval instead.
- **Which admin can approve and start a campaign?** A tenant admin or `root` / Super Admin can approve workflow/campaign states and start the Dagster activation run.
