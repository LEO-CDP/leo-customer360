---
title: Segment detail page
page: "/segments/:id"
---

# Segment detail page

> **Vietnamese version:** [`segment-detail-vi.md`](segment-detail-vi.md)

This page shows one saved audience segment (`cdp_segments`): its metadata card, assigned IAB audience tags, and three workspaces for its generated SQL query, matched master profiles, and per-segment AI agent workflow (`cdp_segment_agent_workflows`). Open it by clicking a segment row in the **Segments** list.

## What you can do here
- **Edit segment**: opens the segment editor modal to update the name, `segment_tag`, domain, description, IAB `audience_tags`, and rule tree.
- **Refresh data**: calls `POST /api/v1/segments/{segment_id}/recompute` to submit an asynchronous Dagster run (`segmentation_job`) that updates `member_count`, `last_computed_at`, and profile `segmentation_tags` / `audience_tags`.
- **Switch workspace**: toggle between the **Audience SQL Query**, **Matched Profiles**, and **Agent Workflow** tabs.

### Audience SQL Query
- Displays the validated SQL statement (`final_generated_sql` / `sql_rules`) executed against `cdp_master_profiles` and `cdp_domain_profiles`, with a **Copy SQL** button.
- If the segment has no rules defined, it displays `"No SQL rules defined for this segment"` and prompts you to select **Edit segment**.

### Matched Profiles
- Lists the active master profiles (`GET /api/v1/segments/{segment_id}/matched-profiles`) currently matching the segment's rules; click any row to open that customer's 360° profile page.
- Select **Load more** to paginate through members. If no profiles match, it displays `"No profiles currently match this segment's rules"`.

### Agent Workflow
- **Add agent** appends a processing step; **Save workflow** persists the ordered pipeline for this segment.
- Each step configures an AI agent (`agent_code` from `cdp_ai_agents`), a **Run order** (lowest runs first), a **Step status** toggle (`Enabled` or `Paused`), an optional **Schedule override** (cron), and **Agent configuration** (JSON).
- For ranking and recommendation agents, you can also select **Candidate content/products**.
- Use the up/down arrows to reorder steps and **Remove** to delete a step.

## Tabs of the page
- **Metadata card**: segment name and `segment_tag`, Active/Inactive and Human/AI Agent (`processed_by`) badges, IAB `audience_tags`, description, Target Domain, Audience Size (`member_count`), Last Computed timestamp, and Created At timestamp.
- **Audience SQL Query**: rule summary and generated SQL query.
- **Matched Profiles**: customer profiles in this audience.
- **Agent Workflow**: ordered AI enrichment, scoring, and recommendation steps bound to this segment.

## Common questions
- **Why is the audience size 0?** No profile matches the rules yet, or membership has not finished recomputing; verify the rules and select **Refresh data**.
- **How do I recompute the audience?** Select **Refresh data** to trigger a Dagster segmentation run and poll until `member_count` updates.
- **How do I change the segment rules?** Select **Edit segment** to open the visual QueryBuilder or AI rule generator.
- **What does Human vs AI Agent mean?** It indicates whether a human user or the AI segment draft assistant (`POST /api/v1/segments/from-description`) authored the rules (`processed_by`).
- **What is the Agent Workflow tab used for?** It schedules and runs scoring, classification, and recommendation AI agents in sequence over the members of this segment.

