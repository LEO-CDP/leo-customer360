---
title: Content and products page
page: "/content-products"
---

# Content and products page

This page is the catalog of tenant-owned content and products used by personalized recommendations. It is opened from the Content & Products tab. It has two workspaces: Contents (news, video and article items shown to customers) and Products (product source records with generated customer-facing content when available).

## What you can do here
- **Switch workspace**: the two tabs Contents and Products on the left.
- **Add item**: "Add item" opens the create form. Choose the item type, Content or Product, then fill the fields.
- **Content fields**: Domain, Content type (Article, News, Video), Title (required), Summary, Image URL, CTA URL, CTA label, Keywords / segment tags (comma-separated).
- **Product fields**: Domain, Product type, Title (required), Summary, Image URL, CTA URL, CTA label, Keywords / segment tags, plus Product source details: Source ID, Source type, Product ID type (SKU, ISBN-13, TICKER), Product ID (required), Original price, Sale price, Currency, Extended attributes (JSON).
- **Import TSV**: "Import TSV" validates every row before queuing a tenant-scoped import job. The file must be .tsv, up to 10 MiB and 5,000 rows. The import runs asynchronously and shows the Dagster run ID.
- **Search and filter** (per workspace): search, Domain and Content type for contents; search and Domain for products. "Clear filters" resets.
- **See an item's details**: open an item to view its details.

## Common questions
- **How do I add a new content item?** Add item, choose Content, set the domain, type and a title, then Save item.
- **How do I import many items at once?** Import TSV, choose a .tsv file under 10 MiB (max 5,000 rows) and Validate & import; every row is checked before it is queued.
- **What are Products?** Product source records, linked to content, with generated customer-facing text where available.
- **How do I find something?** Use the search box in the workspace; filter by domain or content type.