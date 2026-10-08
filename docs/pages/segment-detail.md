---
title: Segment detail page
page: "/segments/:id"
---

# Segment detail page

This page shows one audience segment: its metadata and the three workspaces for its rules, its members and its agent workflow. Open it by clicking a segment in the Segments list.

## What you can do here
- **Edit segment**: opens the segment editor to change the name, tag, domain, description and audience rules.
- **Refresh data**: recomputes this segment's membership and updates the audience size.
- **Switch workspace**: the tabs Audience SQL Query, Matched Profiles and Agent Workflow.

### Audience SQL Query
- Shows the generated SQL statement for the segment's rules, with a Copy SQL button.
- If the segment has no rules it shows "No SQL rules defined for this segment" and points you to Edit segment.

### Matched Profiles
- The master profiles that currently match the rules; click a row to open the customer profile page.
- Load more pages through the list. If none match it says "No profiles currently match this segment's rules".

### Agent Workflow
- **Add agent** adds a step; **Save workflow** stores the ordered list.
- Each step has an AI agent, a Run order (lowest runs first), a Step status toggle (Enabled or Paused), an optional Schedule override, and Agent configuration (JSON).
- For ranking/recommendation agents you can also pick Candidate content/products.
- Use the up/down arrows to reorder and Remove to delete a step.

## Tabs of the page
- **Metadata card**: segment name and tag, Active/Inactive and Human/AI Agent badges, description, Target Domain, Audience Size, Last Computed and Created At.
- **Audience SQL Query**: rules and generated SQL.
- **Matched Profiles**: people in this audience.
- **Agent Workflow**: ordered processing steps.

## Common questions
- **Why is the audience size 0?** No profile matches the rules yet, or membership has not been recomputed; check the rules and press Refresh data.
- **How do I recompute the audience?** Refresh data submits a recompute and updates the size when it finishes.
- **How do I change the rules?** Edit segment opens the rule builder.
- **What does Human vs AI Agent mean?** Whether a person or the AI drafted the segment's rules.
- **What is the Agent Workflow for?** It runs scoring, classification and enrichment agents in order for this audience.
