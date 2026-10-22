---
title: Overview dashboard
page: "/overview"
---

# Overview dashboard

> **Vietnamese version:** [`overview-vi.md`](overview-vi.md)

The Overview page is the first tab in the top navigation. It provides a tenant-wide picture of how customer data is ingested and resolved (`/api/v1/reporting/*`): how many raw records arrived in `cdp_raw_profiles_stage`, how many resolved into `cdp_master_profiles`, how far the Customer Identity Resolution (CIR) queue has progressed, and how well identity channels are covered. It is a read-only operational dashboard.

## What you can do here
- **Change the period**: use the **Period** selector at the top right. Options: Last 3 days, Last 7 days, Last 30 days, Last 90 days (default), Last 180 days, Last 365 days. Changing the period reloads every KPI card and chart on the page.

## Sections of the page
**KPI cards** (all counts are scoped to the selected period)
- **Raw Profiles**: inbound raw profile records (`cdp_raw_profiles_stage`) received from source systems.
- **Master Profiles**: unified master customer profiles (`cdp_master_profiles`) created in the period.
- **Duplicate Masters**: master profiles that consolidated two or more raw profile records (`linked_raw_profile_count >= 2`).
- **Processed**: raw profiles that finished identity resolution (`status_code = 3`).
- **In Progress**: raw profiles currently being processed (`status_code = 2`).
- **Pending**: newly staged raw profiles awaiting the next CIR batch (`status_code = 1`).

**Charts**
- **Profiles by Status**: a doughnut chart of raw profiles split by status (`processed`, `in progress`, `new`, `inactive`, `deleted`).
- **Profiles by Domain**: a bar chart comparing raw and master profile counts across each business domain (`retail`, `banking`, `real_estate`, `travel`, `media`, `education`).
- **Raw Profiles by Source System**: the top ten `source_system` producers by raw profile count, labelled with their domain.
- **Identity Graph Coverage**: the percentage of master profiles carrying each identity channel (`email`, `phone_number`, `device_ids`, `advertising_ids`, `cookie_ids`, `external_ids`, `national_id`).

## Common questions
- **How many unified customers do we have?** Check the **Master Profiles** KPI card for the selected period.
- **Is identity resolution still processing records?** Check **In Progress** and **Pending**, or inspect the **Profiles by Status** chart.
- **Where does our customer data come from?** Check the **Raw Profiles by Source System** chart.
- **How complete is our identity graph?** Inspect **Identity Graph Coverage** to see the fill rate of each identifier type.
- **Why do the numbers change when I switch the period?** Every KPI and chart is filtered to the selected lookback window; widen the period (up to 365 days) to view longer history.

