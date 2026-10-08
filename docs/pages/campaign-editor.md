---
title: Campaign editor page
page: "/campaigns/:id/edit"
---

# Campaign editor page

This page creates a new campaign or edits an existing one. Open it with "Create campaign" on the Campaigns page or with the Edit action on a campaign row. The title shows "Create campaign" or "Edit campaign".

## What you can do here
- **Create a campaign**: "Create campaign" on the Campaigns page, fill in the fields and "Save campaign". A name and an active audience segment are required.
- **Edit a campaign**: open an existing campaign, change the fields and "Save campaign".
- **Go back or cancel**: "Back to campaigns" or "Cancel".
- **Choose an audience segment**: "Find segment" opens the "Choose audience segment" dialog; search by name or tag and pick one. "Change" replaces it and the clear button removes it. Only active, computed segments are listed.
- **Set the campaign metadata**: Campaign code, Name, Operational status (Draft, Active, Paused, Completed), Channel, Platform, Language, Description, Keywords (comma-separated), Start date, End date, Budget amount and Currency.
- **Set the governed plan**: Audience segment, Template ID, Objective, Strategy summary and Planning constraints (labelled as used when requesting a new AI plan).

## Sections of the page
- **Campaign metadata**: ownership, channel, schedule and budget fields.
- **Governed campaign plan**: marked "Review required"; changes here are audited and may return an approved campaign to InReview. Holds the audience segment, Template ID, Objective, Strategy summary and Planning constraints. The AI plan and agent provenance are read-only.
- **Footer**: "Cancel" and "Save campaign".
- **Choose audience segment dialog**: search, results and "Cancel".

## Common questions
- **How do I create a new campaign?** "Create campaign" on the Campaigns page opens this page empty; fill it in and save.
- **Why can't I save?** A name is required, a new campaign needs an active audience segment, and the end date cannot be before the start date.
- **Why is the Template ID locked?** It is disabled when editing an existing campaign.
- **What happens to an approved campaign when I edit it?** Saving changes returns it to InReview for review.
- **Where do I see the AI plan?** It is read-only on the campaign detail page's Strategy tab.
