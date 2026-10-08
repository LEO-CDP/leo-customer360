---
title: Data sources page
page: "/datasources"
---

# Data sources page

This page is the catalog of the connectors that feed customer events into the platform: website tracking, server-side pulls, webhooks, file batches and mobile apps. It is opened from the Data Sources tab in the top navigation. Each row is one connector; open a row to see its configuration, volume and the integration snippet for its type.

## What you can do here
- **Search**: "Search name, slug, url, host…" filters the list as you type.
- **Filter by type**: All types, Web JS (Client-side), Data Connector API (Pull), Data Webhook API (Push), S3 File Connector (Batch), Mobile SDK.
- **Filter by status**: All status, Active, Inactive.
- **Refresh**: reloads the list.
- **Add Data Source**: opens the create form.
- **Read the table**: Data Source (name and slug), Type, Volume Metrics (Total, Daily Event, AVG event / profile), Mode (Direct or Indirect / 1P or 3P) and Status.
- **Open a connector**: click a row to open the detail modal.

### The create/edit form
- **Fields**: Name, Slug, Source Type, Status, Data Source URL, Thumbnail URL, Collect Directly, First-Party Data, Data Source Hosts (comma-separated) and Access Tokens & Credentials (JSON object). The URL, hosts and token labels change with the chosen source type.
- **Source Type options**: 1 - Web JavaScript Code (Client-side tracking), 2 - Data Connector API (Server-side Pull/Sync), 3 - Data Webhook API (Server-side Push), 4 - S3 File Connector (Batch File Processing), 5 - Mobile SDK Code (iOS/Android/Flutter tracking).
- **Save**: Save Data Source for a new connector or Save Changes when editing; Cancel closes. Delete is available when editing.

### The detail modal
- **Header**: name, slug, type, status and mode badges.
- **Volume**: Total Tracked Events, Average Daily Events and Avg Events / Profile.
- **Configuration**: Data Source URL, Thumbnail URL, Ingestion Hosts, Data Source ID, Tenant ID, Created At, Updated At and Access Tokens (JSON).
- **Integration panel** for the connector's type: a JavaScript tracking tag with Copy Web Tag (Web JS), a pull/sync endpoint (API Pull), a webhook example with Copy cURL (Webhook), a storage URI and supported formats (S3 File), or mobile code with Copy Mobile Code (Mobile SDK).
- **QR Code Ingestion & Tracking Details** (Web JS, or when a QR code exists): the QR image, Target URL, Tracking URL (with UTM) and Generated At.
- **Footer**: Delete, Edit Connector and Close.

## Common questions
- **How do I add a website tracking source?** Add Data Source, choose source type 1 (Web JavaScript Code), save, then open the row and copy the JavaScript tracking tag into your site.
- **What do the source types mean?** 1 web JavaScript, 2 server-side pull, 3 webhook push, 4 S3 file batch, 5 mobile SDK.
- **What does Mode "Direct / 1P" mean?** It reflects the Collect Directly and First-Party Data checkboxes.
- **Where do I get the tracking code or webhook example?** Open the connector; the detail modal shows the snippet for its type with a Copy button.
- **Why is a connector Inactive?** Its Status is Inactive; edit it to set Active.
