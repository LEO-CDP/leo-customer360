"""Rebuild visitor behaviour profiles from ad events (task T048).

For every tenant that has personalisation switched on, each visitor's profile is
recomputed from their recent, unflagged events:

    affinities[dimension][value] = {score, n, last_at}
    score = sum over events of  weight(event_type) * 0.5 ** (age_days / half_life_days)

Dimensions are the ones in the weights contract (advertiser, ad_type, format,
tag), derived from the ad's creative exactly as customer360-promotions'
``personalization/features.py`` does (the code is duplicated on purpose: each
code location deploys on its own).

Who is profiled (FR-010): only visitors with an event carrying a visitor key
(events from visitors who were not eligible when they were recorded carry none),
whose current consent allows it: ``opt_in`` tenants need ``granted``; ``opt_out``
tenants need "not denied". A visitor who is no longer eligible, or whose events
have all aged out or been flagged, has their profile DELETED, so a profile never
outlives its basis.

Idempotent: with the same events and the same ``now`` the rebuild produces the
same rows. ``profile_version`` is incremented only when the content changed;
because scores decay with time, a later run normally does change it.

The functions here never commit: the caller owns the transaction.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, execute_values

logger = logging.getLogger(__name__)

WEIGHTS_ENV = "AD_PERSONALIZATION_WEIGHTS_FILE"
_DEFAULT_WEIGHTS_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs" / "ad-personalization" / "contracts" / "profile-event-weights.json"
)

# Used only when the contract file is not shipped with the deployment (for
# example inside a container image). A test pins this to the contract file.
BUILTIN_WEIGHTS: dict[str, Any] = {
    "version": 1,
    "dimensions": ["advertiser", "ad_type", "format", "tag"],
    "event_weights": {
        "impression": 0.0,
        "view": 0.2,
        "click": 1.0,
        "dismiss": -1.0,
        "conversion": 2.0,
        "view_through_conversion": 1.0,
    },
    "half_life_days": 14,
    "window_days": 30,
    "recent_dismissed_hours": 24,
    "max_tags": 10,
    "max_tag_length": 64,
}

SCORE_DECIMALS = 6


def load_weights(path: Optional[str] = None) -> dict[str, Any]:
    """The weights contract: ``path``, else $AD_PERSONALIZATION_WEIGHTS_FILE, else the repo copy."""
    candidate = Path(path or os.environ.get(WEIGHTS_ENV) or _DEFAULT_WEIGHTS_PATH)
    try:
        return json.loads(candidate.read_text())
    except FileNotFoundError:
        logger.warning("weights contract %s not found; using the built-in copy", candidate)
        return dict(BUILTIN_WEIGHTS)


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------


def _normalise_tags(raw: Any, limit: int, max_length: int) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    seen: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower()[:max_length]
        if tag and tag not in seen:
            seen.append(tag)
        if len(seen) >= limit:
            break
    return seen


def ad_features(row: dict[str, Any], weights: dict[str, Any]) -> list[tuple[str, str]]:
    """``(dimension, value)`` pairs for one event row (same derivation as the ad server)."""
    features: list[tuple[str, str]] = []
    if row.get("advertiser_id") is not None:
        features.append(("advertiser", str(row["advertiser_id"])))
    if row.get("ad_type"):
        features.append(("ad_type", str(row["ad_type"])))
    if row.get("format_code"):
        features.append(("format", str(row["format_code"])))

    limit, max_length = weights["max_tags"], weights["max_tag_length"]
    tags = _normalise_tags((row.get("ad_metadata") or {}).get("tags"), limit, max_length)
    if not tags:
        tags = _normalise_tags((row.get("content_payload") or {}).get("tags"), limit, max_length)
    features.extend(("tag", tag) for tag in tags)
    return features


@dataclass
class Profile:
    affinities: dict[str, dict[str, dict[str, Any]]]
    recent_dismissed: dict[str, str]
    event_count: int
    last_event_at: datetime


def build_profile(
    events: Iterable[dict[str, Any]], now: datetime, weights: dict[str, Any]
) -> Optional[Profile]:
    """One visitor's profile from their events (None when none qualify)."""
    half_life = float(weights["half_life_days"])
    event_weights = weights["event_weights"]
    dismissed_after = now - timedelta(hours=weights["recent_dismissed_hours"])

    scores: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    last_at: dict[tuple[str, str], datetime] = {}
    dismissed: dict[str, datetime] = {}
    event_count = 0
    last_event_at: Optional[datetime] = None

    for event in events:
        weight = event_weights.get(event["event_type"])
        if weight is None:
            continue
        at = event["event_time"]
        event_count += 1
        if last_event_at is None or at > last_event_at:
            last_event_at = at

        if event["event_type"] == "dismiss" and at >= dismissed_after:
            key = str(event["ad_id"])
            if key not in dismissed or at > dismissed[key]:
                dismissed[key] = at

        age_days = max((now - at).total_seconds() / 86_400.0, 0.0)
        decayed = weight * 0.5 ** (age_days / half_life)
        for feature in ad_features(event, weights):
            scores[feature] += decayed
            counts[feature] += 1
            if feature not in last_at or at > last_at[feature]:
                last_at[feature] = at

    if event_count == 0 or last_event_at is None:
        return None

    affinities: dict[str, dict[str, dict[str, Any]]] = {}
    for (dimension, value), score in sorted(scores.items()):
        affinities.setdefault(dimension, {})[value] = {
            "score": round(score, SCORE_DECIMALS),
            "n": counts[(dimension, value)],
            "last_at": last_at[(dimension, value)].astimezone(timezone.utc).isoformat(),
        }
    return Profile(
        affinities=affinities,
        recent_dismissed={k: v.astimezone(timezone.utc).isoformat() for k, v in sorted(dismissed.items())},
        event_count=event_count,
        last_event_at=last_event_at,
    )


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------


def profiling_tenants(conn, schema: str) -> list[tuple[int, str]]:
    """``(tenant_id, consent_mode)`` for tenants whose tenant-scope setting is enabled."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT tenant_id, consent_mode FROM {}.personalization_setting "
                "WHERE scope_type = 'tenant' AND enabled ORDER BY tenant_id"
            ).format(sql.Identifier(schema))
        )
        return [(int(t), m) for t, m in cur.fetchall()]


def _consent_clause(consent_mode: str) -> str:
    if consent_mode == "opt_out":
        return "c.status IS DISTINCT FROM 'denied'"
    return "c.status = 'granted'"  # opt_in, and anything unrecognised: fail closed


def _event_rows(conn, schema: str, tenant_id: int, consent_mode: str, since: datetime, types: list[str]):
    """Qualifying events for one tenant, ordered by visitor key (streamed)."""
    query = sql.SQL(
        """
        SELECT e.user_key_hash, e.event_type, e.event_time, e.ad_id,
               cr.advertiser_id, cr.ad_type, cr.format_code, cr.content_payload,
               a.metadata AS ad_metadata
        FROM {s}.ad_event e
        JOIN {s}.ad a ON a.ad_id = e.ad_id AND a.tenant_id = e.tenant_id
        JOIN {s}.creative cr ON cr.creative_id = a.creative_id AND cr.tenant_id = e.tenant_id
        LEFT JOIN {s}.consent_record c
               ON c.tenant_id = e.tenant_id AND c.user_key_hash = e.user_key_hash
        WHERE e.tenant_id = %(tenant_id)s
          AND e.event_time >= %(since)s
          AND e.user_key_hash IS NOT NULL
          AND cardinality(e.flags) = 0
          AND e.event_type = ANY(%(types)s)
          AND {consent}
          -- A visitor with an open erasure request (anything not yet completed,
          -- including a failed one awaiting retry) is never profiled, so their
          -- profile is not recreated between the request and the erasure job.
          AND NOT EXISTS (
              SELECT 1 FROM {s}.erasure_request er
              WHERE er.tenant_id = e.tenant_id
                AND er.user_key_hash = e.user_key_hash
                AND er.status <> 'completed'
          )
        ORDER BY e.user_key_hash, e.event_time
        """
    ).format(s=sql.Identifier(schema), consent=sql.SQL(_consent_clause(consent_mode)))
    with conn.cursor(name=f"adp_profile_events_{tenant_id}") as cur:
        cur.itersize = 5000
        cur.execute(query, {"tenant_id": tenant_id, "since": since, "types": types})
        for row in cur:
            yield {
                "user_key_hash": bytes(row[0]), "event_type": row[1], "event_time": row[2],
                "ad_id": row[3], "advertiser_id": row[4], "ad_type": row[5],
                "format_code": row[6], "content_payload": row[7], "ad_metadata": row[8],
            }


def _rebuild_tenant(
    conn, schema: str, tenant_id: int, consent_mode: str, now: datetime, weights: dict[str, Any]
) -> dict[str, int]:
    since = now - timedelta(days=weights["window_days"])
    types = sorted(weights["event_weights"])

    built: dict[bytes, Profile] = {}
    current_key: Optional[bytes] = None
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        if current_key is not None:
            profile = build_profile(batch, now, weights)
            if profile is not None:
                built[current_key] = profile

    for event in _event_rows(conn, schema, tenant_id, consent_mode, since, types):
        if event["user_key_hash"] != current_key:
            flush()
            current_key, batch = event["user_key_hash"], []
        batch.append(event)
    flush()

    table = sql.Identifier(schema, "visitor_behaviour_profile")
    with conn.cursor() as cur:
        if built:
            execute_values(
                cur,
                sql.SQL(
                    """
                    INSERT INTO {t} AS p
                        (tenant_id, user_key_hash, affinities, recent_dismissed,
                         event_count, last_event_at, profile_version, updated_at)
                    VALUES %s
                    ON CONFLICT (tenant_id, user_key_hash) DO UPDATE SET
                        affinities = EXCLUDED.affinities,
                        recent_dismissed = EXCLUDED.recent_dismissed,
                        event_count = EXCLUDED.event_count,
                        last_event_at = EXCLUDED.last_event_at,
                        profile_version = p.profile_version + 1,
                        -- The job's own start time, the same value an insert gets: the ad
                        -- server treats events older than this (less a margin) as already
                        -- inside the profile and counts only newer fresh signals on top.
                        updated_at = EXCLUDED.updated_at
                    WHERE (p.affinities, p.recent_dismissed, p.event_count, p.last_event_at)
                          IS DISTINCT FROM
                          (EXCLUDED.affinities, EXCLUDED.recent_dismissed,
                           EXCLUDED.event_count, EXCLUDED.last_event_at)
                    """
                ).format(t=table),
                [
                    (tenant_id, key, Json(p.affinities), Json(p.recent_dismissed),
                     p.event_count, p.last_event_at, 1, now)
                    for key, p in built.items()
                ],
            )
        # A profile with no qualifying basis any more must not survive.
        cur.execute(
            sql.SQL(
                "DELETE FROM {t} WHERE tenant_id = %s AND NOT (user_key_hash = ANY(%s))"
            ).format(t=table),
            (tenant_id, [psycopg2.Binary(k) for k in built]),
        )
        deleted = cur.rowcount
    return {"profiles": len(built), "deleted": deleted}


def rebuild_profiles(
    conn,
    *,
    schema: str,
    now: Optional[datetime] = None,
    weights: Optional[dict[str, Any]] = None,
    log: Callable[[str], None] = logger.info,
) -> dict[int, dict[str, int]]:
    """Rebuild every enabled tenant's profiles. Returns ``{tenant_id: {profiles, deleted}}``."""
    now = now or datetime.now(timezone.utc)
    weights = weights or load_weights()
    summary: dict[int, dict[str, int]] = {}
    for tenant_id, consent_mode in profiling_tenants(conn, schema):
        summary[tenant_id] = _rebuild_tenant(conn, schema, tenant_id, consent_mode, now, weights)
        log(f"profile_aggregation: tenant {tenant_id}: {summary[tenant_id]}")
    return summary
