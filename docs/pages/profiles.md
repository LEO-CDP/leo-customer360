---
title: Master profiles page
page: "/profiles"
---

# Master profiles page

> **Vietnamese version:** [`profiles-vi.md`](profiles-vi.md)

This page lists every unified master customer profile (`cdp_master_profiles`) in the tenant — the golden records built by Customer Identity Resolution (CIR) from raw (`cdp_raw_profiles_stage`) and domain (`cdp_domain_profiles`) profiles. It is opened from the **Profiles** tab in the top navigation and serves as the primary customer lookup and exploration screen. Selecting any row opens that customer's 360° profile detail page.

## What you can do here
- **Search**: the search box (`"Search name, persona, email, phone..."`) filters profiles as you type; the clear button resets it.
- **Filter by date**: **From** and **To** filter by last activity or profile update date (`from_date`, `to_date`).
- **Filter by profile scope**: **Domain** (All domains or a specific business domain), **Data source** (`data_source_id`), and **Linked raw profiles** (`linked_raw_profile_count_min`).
- **Filter by identity signal**: **Anonymous ID** (`anonymous_id`, including injected visitor IDs such as `v_...`) and **Device fingerprint** (`device_fingerprint`), matched via active `cdp_profile_links` to `cdp_raw_profiles_stage`.
- **Filter by customer segmentation**: **Lifecycle** (`All lifecycle stages`, `Prospect`, `Lead`, `Customer`, `VIP`, `Dormant`, `Churn risk`), **Value tier** (`All value tiers`, `Low Value`, `Medium Value`, `High Value`), and **Churn risk** (`All churn risks`, `Low Risk`, `Medium Risk`, `High Risk`, `Critical Risk`).
- **Read the table**: Profile (display name / `persona_name`), Domain, Tier, Lifecycle, Churn Risk, Linked Profiles (`linked_raw_profile_count`), Total Events (`total_tracked_events`), and Last Activity / Update.
- **Open a customer**: click any row to navigate to `/profiles/:id`.
- **Page through results**: use **Previous** and **Next** pagination controls; the footer displays `"Showing X-Y of N profiles"`.

## Sections of the page
- **Header and filters**: free-text search box and the **Date range**, **Profile scope**, **Identity signals**, and **Customer segmentation** filter groups.
- **Profiles table**: paginated list of matching master profiles.
- **Footer**: result count and pagination buttons. If no records match, the table displays `"No profiles matched your filters"` with a **Clear search** link.

## Common questions
- **How do I find a specific customer?** Type their name, persona label, email, or phone number in the search box, or filter by **Anonymous ID** / **Device fingerprint**.
- **What does "Linked Profiles" mean?** The count of active raw profile records (`cdp_profile_links` with `status = 'ACTIVE'`) that identity resolution merged into this master profile.
- **Why does a profile show a persona name instead of a person's name?** When PII is SHA-256 hashed (`is_hashed = TRUE`) or a real name is unavailable, the platform displays the generated `persona_name` label.
- **How do I narrow down to VIP or high-churn-risk customers?** Use the **Lifecycle**, **Value tier**, and **Churn risk** dropdown filters.
- **Where do I inspect the full 360° view of a customer?** Click the customer's row to open `/profiles/:id`.

