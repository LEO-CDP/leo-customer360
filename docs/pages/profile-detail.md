---
title: Customer profile page
page: "/profiles/:id"
---

# Customer profile page

> **Vietnamese version:** [`profile-detail-vi.md`](profile-detail-vi.md)

This page shows everything the platform knows about one unified customer (`cdp_master_profiles`): identity attributes, behavioral timeline, resolved AI persona (`cdp_customer_personas`), segment and IAB tags, linked raw records (`cdp_profile_links`), domain attributes (`cdp_domain_profiles`), and value/risk scores. Open it from the **Profiles** tab by selecting a row. All sections appear on one three-column screen.

## What you can do here
- **Edit the profile**: select **Edit profile** in the identity card to open **Edit Personal Information** (name, email, phone, date of birth, gender, domain, acquisition source, customer tier, KYC status, primary relationship, account status `Active`/`Inactive`/`Deleted`, company, address, and lifecycle stage `Prospect`, `Lead`, `Customer`, `VIP`, `Dormant`, `Churn risk`). Saving triggers an async Dagster persona recompute.
- **Add a domain attribute**: select **Add Attribute** in the **Domain Attributes** card (`PUT /api/v1/master-profiles/{id}/domain-attributes`); new JSONB keys are auto-registered in `cdp_profile_attributes` as segmentable.
- **Change the timeline range**: select `24 hours`, `7 days`, `30 days`, or `Custom` (From and To), then **Apply** or **Reset**.
- **Inspect an identity link**: select **View details** on a row in **Identity Graph Links** to inspect the raw profile and its `match_method` / `match_score`.
- **Filter personalized content**: filter **Personalized Items** by `All`, `News`, `Videos`, `Products`, or `Articles`.

## Sections of the page
**Left column**
- **Identity card**: `master_profile_id`, acquisition source, first/last seen, domain, tier, and **Edit profile**.
- **Customer Persona**: resolved AI persona with overall match score (out of 100), six component scores (`Behavior`, `Engagement`, `Financial`, `Loyalty`, `Relationship`, `Risk Factor`), confidence, **Next Best Action**, and **Recent Transitions** (or `"No persona computed yet"`).
- **Activation Channels & detail cards**: Identity Details, Communication Preferences (`communication_preferences` consent flags), Working Details, Address Details, Other Attributes, and **Identity Matching Data & Evidence** (`external_ids`, `device_ids`, `advertising_ids`, `cookie_ids`).

**Center column**
- **Profile Overview**: customer tier, account status, primary relationship, customer since, lifecycle stage, and KYC status.
- **Data source analytics**: event counts grouped by contributing data source.
- **Attributes & Segments**: `segmentation_tags`, IAB `audience_tags`, and top interest categories.
- **Engagement Summary & Cross-Channel Activity**: engagement score, logins, transactions, total/average spend, app/web sessions, and service contacts.
- **Domain Attributes**: domain-scoped key-value pairs in `cdp_domain_profiles.domain_attributes`.
- **Identity Graph Links**: raw records (`cdp_raw_profiles_stage`) merged into this master profile.
- **Timeline**: most-recent-first activity feed from the S3 master-profile event projection.

**Right column**
- **Scoring & Value**: lead score, churn risk, predictive CLV, historical CLV, profile completeness, and identity confidence.
- **Personalized Items**: recommended content and products for this customer.

## Common questions
- **Where do I change a customer's lifecycle stage or tier?** Select **Edit profile** in the identity card.
- **Why is there no persona?** Personas are computed asynchronously by `PersonaResolutionEngine`; `"No persona computed yet"` means it has not run for this customer yet.
- **Where do I see recent activity or value scores?** Check **Timeline** (center column) and **Scoring & Value** (right column).


