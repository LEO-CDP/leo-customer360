---
title: Content and products page
page: "/content-products"
---

# Content and products page

> **Vietnamese version:** [`content-products-vi.md`](content-products-vi.md)

This page is the catalog of tenant-owned content (`cdp_content_items`) and product records (`cdp_product_items`) used by personalized recommendations, promotions, and ranking AI agents. Open it from the **Content & Products** tab in the top navigation. It provides two workspaces: **Contents** (articles, news, and video items) and **Products** (product catalog records linked to generated customer-facing content when available).

## What you can do here
- **Switch workspace**: toggle between the **Contents** and **Products** tabs on the left.
- **Add item**: select **Add item** to open the create modal. Choose **Content** or **Product**, then complete the form fields.
- **Content fields**: Domain, Content type (`Article`, `News`, `Video`), Title (required), Summary, Image URL, CTA URL, CTA label, and Keywords / segment tags (comma-separated).
- **Product fields**: Domain, Product type, Title (required), Summary, Image URL, CTA URL, CTA label, Keywords / segment tags, plus **Product source details**: Source ID, Source type, Product ID type (`SKU`, `ISBN-13`, `TICKER`), Product ID (required), Original price, Sale price, Currency, and Extended attributes (JSON).
- **Import TSV**: select **Import TSV** to validate every row before staging the file to S3 and queuing a tenant-scoped Dagster import run (`content_import.py` / `product_import.py`). The file must be `.tsv`, up to **10 MiB** and at most **5,000 rows**. The import executes asynchronously and displays the Dagster run ID.
- **Search and filter**: filter by keyword, **Domain**, and **Content type** (for Contents) or keyword and **Domain** (for Products). Select **Clear filters** to reset.
- **Inspect or edit an item**: click a row to open its detail view or edit its fields.

## Common questions
- **How do I add a new content item?** Select **Add item**, choose **Content**, set the domain, content type, and title, then select **Save item**.
- **How do I import many items at once?** Select **Import TSV**, choose a `.tsv` file under 10 MiB (maximum 5,000 rows), and select **Validate & import**; every row is validated before the Dagster import job is queued.
- **What are Products?** Product catalog records (`cdp_product_items`) keyed by source and product identifier (`SKU`, `ISBN-13`, or `TICKER`), optionally linked to `cdp_content_items` for recommendation and campaign rendering.
- **How do I find an item quickly?** Use the search box in the active workspace and narrow by **Domain** or **Content type**.
