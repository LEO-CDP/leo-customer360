---
title: Attribute catalog page
page: "/attributes"
---

# Attribute catalog page

> **Vietnamese version:** [`attributes-vi.md`](attributes-vi.md)

This page is the metadata catalog of profile attributes (`cdp_profile_attributes`): the fields the platform knows about a customer, which table each field comes from, and how it is used by Customer Identity Resolution (CIR) and the Segment Builder. Open it from the **Attributes** tab in the top navigation. You can register new attributes and edit existing ones; catalog rows are not deleted from the UI.

## What you can do here
- **Search**: "Search name, code, description…" filters the catalog as you type.
- **Filter by group**: All groups, then the groups present in the catalog (for example `GENERAL`, `IDENTITY`, `IDENTITY_GRAPH`, `RETAIL`, `BANKING`, `REAL_ESTATE`, `TRAVEL`, `MEDIA`, `EDUCATION`, `MARKETING`, `LINEAGE`, `LIFECYCLE`).
- **Filter by domain**: All domains, Global (`all`), then the tenant's business domains.
- **Filter by source**: All sources, Master Profile (`cdp_master_profiles`), Domain Profile (`cdp_domain_profiles`), Raw Profile (`cdp_raw_profiles_stage`).
- **Add Attribute**: opens the create modal.
- **Read the table**: Attribute (display name and `attribute_internal_code`), Group, Domain, Source, Data Type, PII, Segmentable, CIR, Priority, Conversion, and Status.
- **Edit a row**: use the **Edit** action on a row; rows do not open a separate detail page.

### The add/edit form
- **Fields**: Attribute Code (`attribute_internal_code`), Display Name, Description, Group, Source Table, Domain Scope, Data Type, Contains PII (`is_pii`), and Segmentable (`is_segmentable`).
- **Group options**: General, Identity, Identity Graph, Retail, Banking, Real Estate, Travel, Media, Education, Marketing, Lineage, Lifecycle.
- **Source Table options**: Domain Profile (`cdp_domain_profiles`), Master Profile (`cdp_master_profiles`), Raw Profile (`cdp_raw_profiles_stage`).
- **Domain Scope options**: All (Global), Retail, Banking, Real Estate, Travel, Media, Education.
- **Data Type options**: Text, Numeric, Boolean, Date, Timestamp, Array, JSONB.
- **Save**: **Save Attribute** for a new row or **Save Changes** when editing; **Cancel** closes the modal. The **Attribute Code** cannot be changed after creation.

> **Note:** New JSONB keys written into `cdp_domain_profiles.domain_attributes` are also auto-registered into `cdp_profile_attributes` by the database trigger `trg_register_domain_profile_attributes` with `is_segmentable = TRUE`.

## Common questions
- **What does the PII flag do?** It marks the attribute (`is_pii = TRUE`) as containing personally identifiable information so operators and governance workflows can identify sensitive fields.
- **What does Segmentable mean?** When `is_segmentable = TRUE` and `status = 'ACTIVE'`, the attribute is exposed by `GET /api/v1/segments/segmentable-profile-attributes` for building audience segment rules.
- **What are CIR and Priority?** **CIR** (`is_identity_resolution = TRUE`) marks an attribute used by `CustomerIdentityResolver` as an identity-matching key (`exact`, `fuzzy_trgm`, or `fuzzy_dmetaphone`); **Priority** (`priority_rank`) defines its rank in identifier precedence.
- **What does the Conversion badge mean?** The attribute belongs to the lead/conversion scoring group.
- **How do I add a new attribute?** Select **Add Attribute**, set the internal code and display name, choose the group, source table, domain scope, and data type, then select **Save Attribute**.

