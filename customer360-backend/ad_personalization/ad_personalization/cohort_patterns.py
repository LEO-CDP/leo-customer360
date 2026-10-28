"""Anonymous similar-visitor cohort patterns (task T089).

Low-history visitors cannot be personalised from their own behaviour, so the ad
server falls back to what visitors LIKE them responded to. This job publishes
those responses, per tenant, into ``cohort_pattern``:

    cohort_key | ad_id | cohort_size | impressions | objective_events

Privacy rules (FR-016):

* Only eligible visitors contribute: they have a profile, their consent allows it
  (opt_in needs ``granted``, opt_out needs "not denied"), their events are
  unflagged and keyed, and they have no open erasure request.
* Visitors are grouped by cohort key IN MEMORY. Membership is never stored; only
  per-ad totals are.
* A row is written only when at least ``cohort_min_size`` DISTINCT visitors in the
  cohort were shown that ad (``AD_PERSONALIZATION_COHORT_MIN_SIZE``, default 50).
  This is applied per ad, not just per cohort, so a figure can never describe an
  ad that only a handful of people saw.
* Each tenant's rows are rewritten in one go, so a cohort that shrinks below the
  minimum, or whose visitors opt out or are erased, disappears at the next run.

The cohort key is defined by customer360-promotions/personalization/cohorts.py;
the code is duplicated on purpose (each code location deploys on its own) and
``contracts/cohort-key-test-vectors.json`` keeps the two identical.

The functions here never commit: the caller owns the transaction.
"""

from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

from psycopg2 import sql
from psycopg2.extras import execute_values

from ad_personalization.profile_aggregation import _consent_clause, profiling_tenants

logger = logging.getLogger(__name__)

MIN_SIZE_ENV = "AD_PERSONALIZATION_COHORT_MIN_SIZE"
DEFAULT_MIN_SIZE = 50
DEVICE_WINDOW_DAYS = 30
RESPONSE_WINDOW_DAYS = 14

# --- the cohort key (mirror of customer360-promotions personalization/cohorts.py) ---

DIMENSIONS = ("tag", "advertiser")
INTEREST_COUNT = 2
SCORE_DECIMALS = 3
UNKNOWN_DEVICE = "any"
NO_INTERESTS = "none"
KNOWN_DEVICES = ("mobile", "tablet", "desktop")


def top_interests(affinities: Optional[Mapping[str, Any]]) -> list[str]:
    scored: list[tuple[float, str]] = []
    for dimension in DIMENSIONS:
        for value, entry in ((affinities or {}).get(dimension) or {}).items():
            try:
                score = round(float(entry.get("score", 0.0)), SCORE_DECIMALS)
            except (AttributeError, TypeError, ValueError):
                continue
            if score > 0:
                scored.append((score, f"{dimension}:{value}"))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return sorted(name for _, name in scored[:INTEREST_COUNT])


def device_class(device_type: Optional[str]) -> str:
    device = (device_type or "").strip().lower()
    return device if device in KNOWN_DEVICES else UNKNOWN_DEVICE


def cohort_key(affinities: Optional[Mapping[str, Any]], device_type: Optional[str]) -> str:
    interests = "+".join(top_interests(affinities)) or NO_INTERESTS
    return f"{interests}@{device_class(device_type)}"


# --- objective measurement ---

_VIEW = {"awareness", "reach", "brand_awareness"}
_CONVERSION = {"conversions", "conversion"}


def objective_event_types(objective: Optional[str]) -> tuple[str, ...]:
    """The event types that count as a response to an ad with this campaign objective."""
    value = (objective or "").strip().lower()
    if value in _VIEW:
        return ("view",)
    if value in _CONVERSION:
        return ("conversion", "view_through_conversion")
    return ("click",)  # traffic, and anything else


def min_size_from_env() -> int:
    try:
        return max(int(os.environ.get(MIN_SIZE_ENV, DEFAULT_MIN_SIZE)), 1)
    except ValueError:
        return DEFAULT_MIN_SIZE


# --- the job ---


def _eligible_events(conn, schema: str, tenant_id: int, consent_mode: str, since: datetime):
    """Events (and profile affinities) of eligible visitors, ordered by visitor."""
    query = sql.SQL(
        """
        SELECT p.user_key_hash, p.affinities, e.event_type, e.ad_id, e.device_type,
               e.event_time, c.objective
        FROM {s}.visitor_behaviour_profile p
        LEFT JOIN {s}.consent_record cr
               ON cr.tenant_id = p.tenant_id AND cr.user_key_hash = p.user_key_hash
        LEFT JOIN {s}.ad_event e
               ON e.tenant_id = p.tenant_id AND e.user_key_hash = p.user_key_hash
              AND e.event_time >= %(since)s
              AND cardinality(e.flags) = 0
        LEFT JOIN {s}.ad a ON a.ad_id = e.ad_id AND a.tenant_id = e.tenant_id
        LEFT JOIN {s}.campaign c ON c.campaign_id = a.campaign_id AND c.tenant_id = a.tenant_id
        WHERE p.tenant_id = %(tenant_id)s
          AND {consent}
          AND NOT EXISTS (
              SELECT 1 FROM {s}.erasure_request er
              WHERE er.tenant_id = p.tenant_id
                AND er.user_key_hash = p.user_key_hash
                AND er.status <> 'completed'
          )
        ORDER BY p.user_key_hash
        """
    ).format(
        s=sql.Identifier(schema),
        consent=sql.SQL(_consent_clause(consent_mode).replace("c.status", "cr.status")),
    )
    with conn.cursor(name=f"adp_cohort_events_{tenant_id}") as cur:
        cur.itersize = 5000
        cur.execute(query, {"tenant_id": tenant_id, "since": since})
        yield from cur


def compute_patterns(rows, now: datetime, min_size: int) -> list[tuple[str, int, int, int, int]]:
    """``(cohort_key, ad_id, cohort_size, impressions, objective_events)`` rows to publish.

    ``rows`` are the tuples from :func:`_eligible_events`, grouped by visitor.
    """
    response_since = now - timedelta(days=RESPONSE_WINDOW_DAYS)

    affinities: dict[bytes, Any] = {}
    devices: dict[bytes, Counter] = defaultdict(Counter)
    # per visitor, per ad: [impressions, objective events]
    responses: dict[bytes, dict[int, list[int]]] = defaultdict(dict)

    for key, profile, event_type, ad_id, device_type, event_time, objective in rows:
        key = bytes(key)
        affinities[key] = profile
        if event_type is None:
            continue
        devices[key][device_class(device_type)] += 1
        if event_time < response_since:
            continue
        counts = responses[key].setdefault(ad_id, [0, 0])
        if event_type == "impression":
            counts[0] += 1
        elif event_type in objective_event_types(objective):
            counts[1] += 1

    # Cohort membership exists only inside this function.
    cohort_of: dict[bytes, str] = {}
    for key, profile in affinities.items():
        known = {d: n for d, n in devices[key].items() if d != UNKNOWN_DEVICE}
        modal = max(sorted(known), key=lambda d: known[d]) if known else None
        cohort_of[key] = cohort_key(profile, modal)

    visitors: dict[tuple[str, int], int] = defaultdict(int)
    impressions: dict[tuple[str, int], int] = defaultdict(int)
    objective_events: dict[tuple[str, int], int] = defaultdict(int)
    for key, per_ad in responses.items():
        for ad_id, (shown, responded) in per_ad.items():
            if shown == 0:
                continue  # only visitors who were shown the ad describe how it did
            slot = (cohort_of[key], ad_id)
            visitors[slot] += 1
            impressions[slot] += shown
            objective_events[slot] += responded

    return sorted(
        (cohort, ad_id, visitors[(cohort, ad_id)], impressions[(cohort, ad_id)], objective_events[(cohort, ad_id)])
        for cohort, ad_id in visitors
        if visitors[(cohort, ad_id)] >= min_size
    )


def _rewrite(conn, schema: str, tenant_id: int, patterns: list, now: datetime) -> int:
    table = sql.Identifier(schema, "cohort_pattern")
    with conn.cursor() as cur:
        cur.execute(sql.SQL("DELETE FROM {} WHERE tenant_id = %s").format(table), (tenant_id,))
        if patterns:
            execute_values(
                cur,
                sql.SQL(
                    "INSERT INTO {} (tenant_id, cohort_key, ad_id, cohort_size, impressions, "
                    "objective_events, refreshed_at) VALUES %s"
                ).format(table),
                [(tenant_id, k, ad, size, imp, obj, now) for k, ad, size, imp, obj in patterns],
            )
    return len(patterns)


def refresh_cohort_patterns(
    conn,
    *,
    schema: str,
    now: Optional[datetime] = None,
    min_size: Optional[int] = None,
    log: Callable[[str], None] = logger.info,
) -> dict[int, dict[str, int]]:
    """Rebuild every enabled tenant's cohort patterns. Returns ``{tenant_id: {cohorts, patterns}}``."""
    now = now or datetime.now(timezone.utc)
    min_size = min_size if min_size is not None else min_size_from_env()
    since = now - timedelta(days=DEVICE_WINDOW_DAYS)

    summary: dict[int, dict[str, int]] = {}
    for tenant_id, consent_mode in profiling_tenants(conn, schema):
        rows = _eligible_events(conn, schema, tenant_id, consent_mode, since)
        patterns = compute_patterns(rows, now, min_size)
        written = _rewrite(conn, schema, tenant_id, patterns, now)
        summary[tenant_id] = {"cohorts": len({p[0] for p in patterns}), "patterns": written}
        log(f"cohort_patterns: tenant {tenant_id}: {summary[tenant_id]}")
    return summary
