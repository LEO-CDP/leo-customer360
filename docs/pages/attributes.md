---
title: Attribute catalog page
page: "/attributes"
---

# Attribute catalog page

This page is the catalog of profile attributes: the fields the platform knows about a customer, where each one comes from, and how it is used for identity resolution and segmentation. It is opened from the Attributes tab in the top navigation. You can add attributes and edit existing ones; there is no delete.

## What you can do here
- **Search**: "Search name, code, description…" filters the list as you type.
- **Filter by group**: All groups, then the groups present in the catalog (for example General, Identity, Identity Graph, Retail, Banking, Real Estate, Travel, Media, Education, Marketing, Lineage, Lifecycle).
- **Filter by domain**: All domains, Global, then the tenant's domains.
- **Filter by source**: All sources, Master Profile, Domain Profile, Raw Profile.
- **Add Attribute**: opens the create form.
- **Read the table**: Attribute (display name and internal code), Group, Domain, Source, Data Type, PII, Segmentable, CIR, Priority, Conversion and Status.
- **Edit a row**: use the Edit action; rows do not open a detail page.

### The add/edit form
- **Fields**: Attribute Code, Display Name, Description, Group, Source Table, Domain Scope, Data Type, Contains PII and Segmentable.
- **Group options**: General, Identity, Identity Graph, Retail, Banking, Real Estate, Travel, Media, Education, Marketing, Lineage, Lifecycle.
- **Source Table options**: Domain Profile, Master Profile, Raw Profile.
- **Domain Scope options**: All (Global), Retail, Banking, Real Estate, Travel, Media, Education.
- **Data Type options**: Text, Numeric, Boolean, Date, Timestamp, Array, JSONB.
- **Save**: Save Attribute for a new row or Save Changes when editing; Cancel closes. The Attribute Code cannot be changed after creation.

## Common questions
- **What does the PII flag do?** It marks the attribute as containing personal data; the catalog shows it as PII.
- **What does Segmentable mean?** Whether the attribute can be used to build segment rules.
- **What are CIR and Priority?** CIR marks an attribute used as an identity-resolution matching key; Priority is its matching rank.
- **What does the Conversion badge mean?** The attribute belongs to the lead-scoring group used for conversion.
- **How do I add a new attribute?** Add Attribute, set the code and display name, choose group, source, domain and data type, then save.
