---
title: Segments page
page: "/segments"
---

# Segments page

> **Vietnamese version:** [`segments-vi.md`](segments-vi.md)

This is the Audience Builder (`cdp_segments`): the catalog of saved customer audience segments. Each segment defines a validated rule tree (`json_rules` and `sql_rules`) over master profile attributes (`cdp_master_profiles`) and domain attributes (`cdp_domain_profiles.domain_attributes`), optionally mapped to standardized IAB Audience Taxonomy 1.1 tags (`audience_tags`). The list displays how many active master profiles currently match (`member_count`). Click a segment row to open its detail page.

## What you can do here
- **Search**: "Search segments by name…" filters the list as you type.
- **Filter**: **Domain** (All domains or a specific domain), **Status** (`All statuses`, `Active`, `Inactive`), **Source** (`All sources`, `Created by user`, `Created by AI agent`), and **Audience size** (`All audience sizes`, `No members`, `Has members`).
- **Clear filters**: the reset icon button clears the search box and all filter dropdowns.
- **Refresh**: calls `POST /api/v1/segments/admin/recompute-all` to recompute segment memberships for the caller's tenant via Dagster (`segmentation_job`) and update matched counts. When SSO is enabled (`SSO_LOGIN=true`), this action requires a tenant admin role (`admin` or `root`).
- **Create segment**: opens the segment editor modal.
- **Read the table**: Segment (name and `segment_tag`), Business Domain, Created By (`human` or `ai_agent`), Matched Profiles (`member_count`), Lifecycle (`Active` or `Inactive`), and Created On.
- **Open or edit**: click a row to open the segment detail page, or select the **Edit** action on the row.

### The segment editor
- **Fields**: Segment name, Segment tag (`segment_tag`), Domain, IAB Audience Tags (`audience_tags`), and Description.
- **Describe with AI**: type a plain-language audience description and select **Generate rules** (or **Update rules**). The API (`POST /api/v1/segments/from-description`) drafts a safe QueryBuilder rule tree and SQL fragment; select **Start over** to clear it. AI drafting never saves automatically.
- **Audience rules**: choose segmentable attributes (`GET /api/v1/segments/segmentable-profile-attributes`), operators, and values from the live catalog to build the rule tree.
- **Save**: select **Create segment** for a new segment or **Save changes** when editing; **Cancel** closes without saving. Creating or updating a segment automatically triggers an asynchronous Dagster recompute for that segment, while the background `segmentation_poll_sensor` (default every 60 seconds) keeps active segments in sync when profiles change.

## Sections of the page
- **Header and filters**: search box, the four filter dropdowns, Clear filters button, **Refresh**, and **Create segment**.
- **Segments table**: one row per saved segment.
- **Footer**: total segment count and a **Load more** button.

## Common questions
- **How do I create a segment?** Select **Create segment**, enter a name and tag, build the rules (manually or with **Describe with AI**), and select **Create segment**.
- **What is the difference between "Created by user" and "Created by AI agent"?** It records whether a human operator or the AI draft assistant generated the segment rules (`processed_by`).
- **Why is Matched Profiles 0?** Either no active profile matches the rules yet, or the asynchronous Dagster recompute has not finished; select **Refresh** to recompute.
- **Can I write segment rules in plain language?** Yes — use **Describe with AI** to generate the rule tree from natural language, then review and save.
- **How do custom attributes from external systems appear in the rule builder?** Any attribute in `cdp_profile_attributes` with `is_segmentable = TRUE` and `status = 'ACTIVE'` (including keys auto-registered from `cdp_domain_profiles.domain_attributes`) is automatically available in the attribute picker.

