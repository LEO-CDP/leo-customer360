---
title: Analytics dashboard
page: "/analytics"
---

# Analytics dashboard

> **Vietnamese version:** [`analytics-vi.md`](analytics-vi.md)

The Analytics page is the second tab in the top navigation. It displays behavioral event volume from the S3/MinIO event lake (`GET /api/v1/events/` and `GET /api/v1/events/device-types`) alongside profile distribution across source systems and business domains. It is a read-only operational dashboard.

## What you can do here
- **Change the period**: use the **Period** selector in the header. Options: Last 3 days, Last 7 days, Last 30 days (default), Last 90 days, Last 120 days, Last 180 days.
- **Refresh**: select **Refresh** to reload all KPI cards and charts for the selected time window.

## Sections of the page
**KPI cards**
- **Total Events**: total behavioral events recorded in the selected period.
- **Active Profiles**: despite the label, this card currently displays the total master profile count in the selected period rather than only active ones.
- **Conversions**: always displays "—"; dedicated conversion aggregation is not wired to this card yet.
- **Master Profiles**: resolved master profiles (`cdp_master_profiles`) in the selected period.

**Charts**
- **Event Volume Over Time**: a line chart of daily UTC event totals across the selected period.
- **Events by Device Type**: a bar chart breaking down event volume by normalized device type (`desktop`, `mobile`, `tablet`, `unknown`) parsed from the HTTP `User-Agent`.
- **Profile Distribution Heatmap (Source System × Domain)**: a matrix of raw profile counts (`cdp_raw_profiles_stage`), with one row per `source_system` and one column per business `domain`; darker cells represent higher profile volume.
- **Event Activity Heatmap (Daily Event Volume)**: a calendar-style heatmap of daily event volume with a summary line (`"N events across M active days (last D days) · peak P/day"`) and a Less–More color legend.

If there are no events and no profile breakdown for the selected window, the page displays `"No analytics data available"`.

## Common questions
- **How many events happened in the period?** Check the **Total Events** KPI card or the **Event Volume Over Time** line chart.
- **Which device type do customers use most?** Check the **Events by Device Type** bar chart.
- **Which source system sends the most profiles?** Inspect the **Profile Distribution Heatmap (Source System × Domain)**.
- **Why is Conversions showing "—"?** Conversion event aggregation is not connected to this summary card yet, so it always displays `"—"`.
- **Why is Active Profiles the same number as Master Profiles?** The card is labelled **Active Profiles** in the UI but currently renders the total master profile count for the period.

