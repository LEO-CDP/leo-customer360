---
title: Overview dashboard
page: "/overview"
---

# Overview dashboard

The Overview page is the first tab in the top navigation. It gives a tenant-wide picture of how customer data is collected and resolved: how many raw records arrived, how many became master profiles, how far processing has got, and how well identity channels are covered. It is a read-only dashboard; there is nothing to edit here.

## What you can do here
- **Change the period**: the "Period" selector at the top right. Options: Last 3 days, Last 7 days, Last 30 days, Last 90 days (default), Last 180 days, Last 365 days. Changing it reloads every KPI and chart on the page.

## Sections of the page
**KPI cards** (all counts are for the selected period)
- **Raw Profiles**: raw profile records received from source systems.
- **Master Profiles**: resolved master profiles created in the period.
- **Duplicate Masters**: master profiles that merged two or more raw records.
- **Processed**: raw profiles that finished processing.
- **In Progress**: raw profiles currently being processed.
- **Pending**: raw profiles that are new and not processed yet.

**Charts**
- **Profiles by Status**: doughnut of raw profiles split by status (processed, in progress, new, inactive, deleted).
- **Profiles by Domain**: bars comparing raw and master profile counts for each business domain.
- **Raw Profiles by Source System**: the ten source systems with the most raw profiles, labelled with their domain.
- **Identity Graph Coverage**: for each identity channel (email, phone, device ID, advertising ID, cookie ID, external ID, national ID), the share of master profiles that have it, as a percentage.

## Common questions
- **How many customers do we have?** Master Profiles, for the selected period.
- **Is data still being processed?** In Progress and Pending; the Profiles by Status chart shows the split.
- **Where does our data come from?** Raw Profiles by Source System.
- **How complete is identity matching?** Identity Graph Coverage.
- **Why do the numbers change when I change the period?** Every figure is filtered to the selected period; widen it to see more history.
