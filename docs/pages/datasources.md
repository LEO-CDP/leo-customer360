---
title: Data sources page
page: "/datasources"
---

# Data sources page

> **Vietnamese version:** [`datasources-vi.md`](datasources-vi.md)

This page is the catalog of inbound data sources (`sys_data_source`) that feed behavioral events and raw customer profiles into the platform: website tracking, server-side pulls, HTTP webhooks, S3/MinIO file batches, and mobile SDKs. Open it from the **Data Sources** tab in the top navigation. Click a row to view its volume metrics, configuration, QR code, and integration snippet.

## What you can do here
- **Search**: "Search name, slug, url, host…" filters the list as you type.
- **Filter by type**: All types, Web JS (Client-side), Data Connector API (Pull), Data Webhook API (Push), S3 File Connector (Batch), Mobile SDK.
- **Filter by status**: All status, Active (`1`), Inactive (`0`).
- **Refresh / Add Data Source**: reload the list or open the create modal.
- **Read the table**: Data Source (name and `slug`), Type (`source_type`), Volume Metrics (`Total`, `Daily Event`, `AVG event / profile`), Mode (`Direct` / `Indirect`, `1P` / `3P`), and Status.
- **Open a connector**: click any row to open the detail modal.

### The create/edit form
- **Fields**: Name, Slug, Source Type (`1`–`5`), Status, Data Source URL, Thumbnail URL, Collect Directly (`is_collect_directly`), First-Party Data (`is_first_party_data`), Data Source Hosts (comma-separated), and Access Tokens & Credentials (JSON object in `access_tokens`).
- **Source Type options**:
  - `1` — Web JavaScript Code (Client-side tracking via `leo.proxy.js` & `leo.observer.js`)
  - `2` — Data Connector API (Server-side Pull/Sync)
  - `3` — Data Webhook API (Server-side Push to `POST /api/v1/tracking/logs`)
  - `4` — S3 File Connector (Batch CSV, JSONL, Parquet synchronization)
  - `5` — Mobile SDK Code (iOS / Android / Flutter tracking)
- **Save**: **Save Data Source** or **Save Changes**; **Cancel** closes. **Delete** is available when editing.

### The detail modal
- **Header & Volume**: name, slug, type, status, mode badges, Total Tracked Events, Average Daily Events, and Avg Events / Profile.
- **Configuration**: Data Source URL, Ingestion Hosts, Data Source ID (`data_source_id`), Tenant ID, timestamps, and Access Tokens (JSON).
- **Integration panel**: JavaScript tracking tag with **Copy Web Tag** (Type 1), pull/sync endpoint (Type 2), webhook example with **Copy cURL** (Type 3), S3 partition URI (`s3://data-tracking-<data_source_id>/...`) (Type 4), or mobile snippet with **Copy Mobile Code** (Type 5).
- **QR Code Ingestion & Tracking Details**: QR image, Target URL, Tracking URL (with UTM), and Generated At.
- **Footer**: **Delete**, **Edit Connector**, and **Close**.

## Common questions
- **How do I add a website tracking source?** Select **Add Data Source**, choose type `1 - Web JavaScript Code`, save, then open the row and select **Copy Web Tag**.
- **What do the 5 source types mean?** `1` = Web JS SDK, `2` = Server-side Pull, `3` = Webhook Push, `4` = S3 File Batch, `5` = Mobile SDK.
- **What does Mode "Direct / 1P" mean?** It reflects the **Collect Directly** and **First-Party Data** flags.
- **Where do I get the tracking script or webhook example?** Open the connector row; the detail modal renders the snippet with a copy button.


