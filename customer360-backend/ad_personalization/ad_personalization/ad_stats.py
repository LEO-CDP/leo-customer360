"""Hourly ad statistics from ad events (task T049).

Aggregates unflagged ``ad_event`` rows into ``ad_stat_hourly`` by tenant, ad,
hour and group:

* the group comes from the ``serving_decision`` of the same ``request_id``
  decided at most one day before the event (``personalised`` and ``holdout`` are
  kept; every other decision group, or no decision at all, is ``default``)
* ``impressions`` / ``views`` / ``clicks`` count their event types;
  ``conversions`` counts ``conversion`` and ``view_through_conversion``

Idempotent per hour: each run recomputes the trailing window (default 48 hours,
including the current hour) and REPLACES those buckets, so re-running, late
events and erasures all converge on what the events say. Every row is grouped by
``tenant_id``, so tenants never mix.

``refresh_ad_stats`` never commits: the caller owns the transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from psycopg2 import sql

DEFAULT_WINDOW_HOURS = 48


def floor_hour(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def refresh_ad_stats(
    conn,
    *,
    schema: str,
    now: Optional[datetime] = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> dict[str, int]:
    """Recompute the trailing ``window_hours`` of hourly stats. Returns row counts."""
    now = now or datetime.now(timezone.utc)
    end = floor_hour(now) + timedelta(hours=1)
    start = end - timedelta(hours=window_hours)
    params = {"start": start, "end": end}
    s = sql.Identifier(schema)

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "DELETE FROM {s}.ad_stat_hourly WHERE hour_start >= %(start)s AND hour_start < %(end)s"
            ).format(s=s),
            params,
        )
        deleted = cur.rowcount
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {s}.ad_stat_hourly
                    (tenant_id, ad_id, hour_start, group_code, placement_id, campaign_id,
                     impressions, views, clicks, conversions, updated_at)
                SELECT ev.tenant_id, ev.ad_id, date_trunc('hour', ev.event_time),
                       COALESCE(sd.group_code, 'default'),
                       max(ev.placement_id), max(ev.campaign_id),
                       count(*) FILTER (WHERE ev.event_type = 'impression'),
                       count(*) FILTER (WHERE ev.event_type = 'view'),
                       count(*) FILTER (WHERE ev.event_type = 'click'),
                       count(*) FILTER (WHERE ev.event_type IN ('conversion', 'view_through_conversion')),
                       now()
                FROM {s}.ad_event ev
                LEFT JOIN LATERAL (
                    SELECT CASE d.group_code
                               WHEN 'personalised' THEN 'personalised'
                               WHEN 'holdout' THEN 'holdout'
                               ELSE 'default'
                           END AS group_code
                    FROM {s}.serving_decision d
                    WHERE d.tenant_id = ev.tenant_id
                      AND d.request_id = ev.request_id
                      -- event_time is the visitor's own clock: allow for it running behind the
                      -- server (ingest accepts up to 10 minutes of skew), otherwise a device a
                      -- second slow would put its events in "default" and skew the comparison.
                      AND d.decided_at BETWEEN ev.event_time - interval '1 day'
                                           AND ev.event_time + interval '10 minutes'
                    ORDER BY d.decided_at DESC
                    LIMIT 1
                ) sd ON true
                WHERE ev.event_time >= %(start)s AND ev.event_time < %(end)s
                  AND ev.ad_id IS NOT NULL
                  AND cardinality(ev.flags) = 0
                GROUP BY ev.tenant_id, ev.ad_id, date_trunc('hour', ev.event_time),
                         COALESCE(sd.group_code, 'default')
                """
            ).format(s=s),
            params,
        )
        written = cur.rowcount
    return {"deleted": deleted, "written": written}
