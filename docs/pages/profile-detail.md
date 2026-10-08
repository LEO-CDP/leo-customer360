---
title: Customer profile page
page: "/profiles/:id"
---

# Customer profile page

This page shows everything the platform knows about one customer: who they are, how they behave, which persona and segments they belong to, and a score for how valuable or at risk they are. It is opened from the Profiles tab by selecting a row. All sections are on one screen, in three columns, with no sub-tabs.

## What you can do here
- **Edit the profile**: the "Edit profile" button in the identity card opens "Edit Personal Information". Fields: full name, first name, last name, email, phone, date of birth, gender, domain, acquisition source, customer tier, KYC status, primary relationship, account status (Active, Inactive, Deleted), company, address and lifecycle stage (Prospect, Lead, Customer, VIP, Dormant, Churn risk). Save changes or Cancel.
- **Add a domain attribute**: "Add Attribute" in the Domain Attributes card.
- **Change the timeline range**: 24 hours, 7 days, 30 days or Custom (From and To), then Apply or Reset.
- **Inspect an identity link**: "View details" on a row of Identity Graph Links opens the raw profile detail with its match reason.
- **Filter personalized content**: All, News, Videos, Products or Articles in Personalized Items.
- Read the persona and its Next Best Action. These are shown for reading; they cannot be edited here.

## Sections of the page
**Left column**
- **Identity card**: master profile ID, acquisition source, first seen, last seen, domain and tier, plus the Edit profile button.
- **Customer Persona**: the persona resolved for this customer, marked AI-native, with an overall match score out of 100 and its parts (Behavior, Engagement, Financial, Loyalty, Relationship, Risk Factor), a confidence value, the Next Best Action, and Recent Transitions. If none exists it says "No persona computed yet" and explains that personas are resolved automatically by the AI pipeline.
- **Activation Channels** and detail cards: Identity Details, Communication Preferences, Working Details, Address Details, Other Attributes, and "Identity Matching Data & Evidence".

**Center column**
- **Profile Overview**: customer tier, account status, primary relationship, customer since, life stage, KYC status.
- **Data source analytics**: event activity by each contributing data source (data source name, total events).
- **Attributes & Segments**: segmentation tags and top interests by category (events count per category).
- **Engagement Summary**: engagement score, total logins, total transactions, total spent, average transaction, last interaction.
- **Cross-Channel Activity**: app sessions, web sessions, customer service contacts and transactions.
- **Domain Attributes**: extra attributes for the customer's business domain.
- **Identity Graph Links**: the raw records linked into this master profile and why they matched.
- **Timeline**: the customer's events over the chosen time range.

**Right column**
- **Scoring & Value**: lead score, churn risk, predictive CLV, historical CLV, profile completeness and identity confidence.
- **Personalized Items**: content recommended for this customer, filterable by type.

## Common questions
- **Where do I change a customer's lifecycle stage or tier?** Edit profile in the identity card.
- **Why is there no persona?** Personas are computed by the AI pipeline; "No persona computed yet" means it has not run for this customer yet.
- **Where do I see what a customer did recently?** The Timeline card; widen the range with the presets.
- **Where do I see how valuable or at risk a customer is?** Scoring & Value.
- **What does a field mean?** Field definitions are in the Attributes tab (attribute catalog).
