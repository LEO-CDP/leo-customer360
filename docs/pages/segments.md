---
title: Segments page
page: "/segments"
---

# Segments page

This is the Audience Builder: the list of saved audience segments. Each segment is a set of rules over profile attributes, and the list shows how many master profiles currently match. Click a segment to open its detail page with the matched profiles.

## What you can do here
- **Search**: "Search segments by name…" filters the list as you type.
- **Filter**: Domain (All domains), Status (All statuses, Active, Inactive), Source (All sources, Created by user, Created by AI agent) and Audience size (All audience sizes, No members, Has members).
- **Clear filters**: the reset icon button clears the search and all filters.
- **Refresh**: recomputes segment memberships for the tenant and updates the matched counts. With single sign-on enabled this needs a tenant admin role.
- **Create segment**: opens the segment editor.
- **Read the table**: Segment, Business Domain, Created By, Matched Profiles, Lifecycle (shows Active or Inactive) and Created On.
- **Open or edit**: click a row to open the segment detail page, or use the Edit action on the row.

### The segment editor
- **Fields**: Segment name, Segment tag, Domain and Description.
- **Describe with AI**: type a plain-language description and press Generate rules (or Update rules). The AI drafts rules into the builder; use Start over to clear it. It never saves on its own.
- **Audience rules**: choose attributes and values from the live catalog to build the rule tree.
- **Save**: Create segment for a new one, or Save changes when editing; Cancel closes without saving.

## Sections of the page
- **Header and filters**: search, the four filters, Clear filters, Refresh and Create segment.
- **Segments table**: one row per segment.
- **Footer**: the count and a Load more button.

## Common questions
- **How do I create a segment?** Create segment, then name it and build the rules.
- **What is the difference between "Created by user" and "Created by AI agent"?** It records whether a person or the AI drafted the rules.
- **Why is Matched Profiles 0?** No profile matches the rules yet, or membership has not been recomputed; press Refresh.
- **Can I write the rules in plain language?** Yes — Describe with AI drafts them, but review the rules before saving.
- **How do I change an existing segment?** Open it and use Edit segment, or the Edit action in the list.
