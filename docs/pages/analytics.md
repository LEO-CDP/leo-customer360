---
title: Analytics dashboard
page: "/analytics"
---

# Analytics dashboard

The Analytics page is the second tab in the top navigation. It shows how much customer activity there is over time and how profiles are spread across sources and domains. It is read-only.

## What you can do here
- **Change the period**: the "Period" selector. Options: Last 3 days, Last 7 days, Last 30 days (default), Last 90 days, Last 120 days, Last 180 days.
- **Refresh**: the "Refresh" button reloads the page with the current period.

## Sections of the page
**KPI cards**
- **Total Events**: all events in the selected period.
- **Active Profiles**: despite the label, this shows the number of master profiles in the selected period, not only active ones.
- **Conversions**: always shows "—"; conversion data is not available yet.
- **Master Profiles**: master profiles in the selected period.

**Charts**
- **Event Volume Over Time**: a line chart of daily event totals for the period.
- **Events by Device Type**: a bar chart of event totals per device type.
- **Profile Distribution Heatmap (Source System × Domain)**: a grid of raw profile counts, one row per source system and one column per domain; darker cells mean more profiles.
- **Event Activity Heatmap (Daily Event Volume)**: a calendar-style heatmap of daily event volume, with a summary line ("N events across M active days (last D days) · peak P/day") and a Less–More colour legend.

If there are no events and no profile breakdown for the period, the page shows "No analytics data available".

## Common questions
- **How many events happened?** Total Events, or the Event Volume Over Time chart.
- **Which device do customers use most?** Events by Device Type.
- **Which source system sends the most profiles?** Profile Distribution Heatmap.
- **Why is Conversions empty?** Conversion tracking is not available yet; the card always shows "—".
- **Why is Active Profiles the same as Master Profiles?** The card is labelled Active Profiles but shows the total master profile count.
