"""
Visitor profile repository.

Responsible for leo_ads.visitor_behaviour_profile (a visitor's decayed responses
over ad features, rebuilt from events by the profile_aggregation Dagster job) and
leo_ads.user_serving_key (the hashed visitor key and its link to a master profile).

Persistence only: how affinity is scored is personalization/scoring.py.

``get_profile`` accepts several keys and merges them at READ time, summing the
scores per feature. That is how a known visitor's behaviour across devices is
combined without ever merging stored rows, so un-linking a key removes its
contribution cleanly.

Multi-tenancy:
    leo_ads has no row-level security. Every method takes a required ``tenant_id``
    and filters by it.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker


def merge_profiles(rows: Sequence[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Merge several profile rows into one view (None when there are none).

    Scores for the same feature are summed; the most recent ``last_at`` wins;
    counts add up; the latest dismissal time per ad wins.
    """
    if not rows:
        return None

    affinities: dict[str, dict[str, dict[str, Any]]] = {}
    dismissed: dict[str, str] = {}
    event_count = 0
    last_event_at = None
    updated_at = None

    for row in rows:
        event_count += int(row.get("event_count") or 0)
        row_updated = row.get("updated_at")
        # The OLDEST rebuild time: any key's events newer than that may be missing.
        if row_updated is not None and (updated_at is None or row_updated < updated_at):
            updated_at = row_updated
        row_last = row.get("last_event_at")
        if row_last is not None and (last_event_at is None or row_last > last_event_at):
            last_event_at = row_last

        for dimension, values in (row.get("affinities") or {}).items():
            bucket = affinities.setdefault(dimension, {})
            for value, entry in values.items():
                merged = bucket.get(value)
                if merged is None:
                    bucket[value] = dict(entry)
                    continue
                merged["score"] = float(merged.get("score", 0.0)) + float(entry.get("score", 0.0))
                merged["n"] = int(merged.get("n", 0)) + int(entry.get("n", 0))
                if (entry.get("last_at") or "") > (merged.get("last_at") or ""):
                    merged["last_at"] = entry.get("last_at")

        for ad_id, at in (row.get("recent_dismissed") or {}).items():
            if at > dismissed.get(ad_id, ""):
                dismissed[ad_id] = at

    return {
        "affinities": affinities,
        "recent_dismissed": dismissed,
        "event_count": event_count,
        "last_event_at": last_event_at,
        "updated_at": updated_at,
    }


class ProfileRepository:
    """Read, write and erase visitor profiles and serving keys."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def get_profile(
        self,
        tenant_id: int,
        user_keys: Sequence[bytes],
        timeout_ms: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """The merged profile for one visitor key, or several linked keys; None if none exist.

        ``timeout_ms`` bounds the query so a slow database cannot stall a serve request.
        """
        if not user_keys:
            return None

        with self._session_factory() as session:
            if timeout_ms:
                session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            rows = session.execute(
                text(
                    """
                    SELECT affinities, recent_dismissed, event_count, last_event_at, updated_at
                    FROM leo_ads.visitor_behaviour_profile
                    WHERE tenant_id = :tenant_id AND user_key_hash = ANY(:user_keys)
                    """
                ),
                {"tenant_id": tenant_id, "user_keys": [bytes(k) for k in user_keys]},
            ).mappings().all()

        return merge_profiles([dict(r) for r in rows])

    def upsert_profile(
        self,
        tenant_id: int,
        user_key: bytes,
        *,
        affinities: dict[str, Any],
        recent_dismissed: Optional[dict[str, str]] = None,
        event_count: int = 0,
    ) -> None:
        """Create or replace one visitor's profile (the batch job normally does this)."""
        with self._session_factory() as session:
            with session.begin():
                session.execute(
                    text(
                        """
                        INSERT INTO leo_ads.visitor_behaviour_profile
                            (tenant_id, user_key_hash, affinities, recent_dismissed,
                             event_count, last_event_at, profile_version, updated_at)
                        VALUES (:tenant_id, :user_key, CAST(:affinities AS jsonb),
                                CAST(:dismissed AS jsonb), :event_count, now(), 1, now())
                        ON CONFLICT (tenant_id, user_key_hash) DO UPDATE SET
                            affinities = EXCLUDED.affinities,
                            recent_dismissed = EXCLUDED.recent_dismissed,
                            event_count = EXCLUDED.event_count,
                            last_event_at = now(),
                            profile_version = leo_ads.visitor_behaviour_profile.profile_version + 1,
                            updated_at = now()
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "user_key": bytes(user_key),
                        "affinities": json.dumps(affinities),
                        "dismissed": json.dumps(recent_dismissed or {}),
                        "event_count": event_count,
                    },
                )

    def upsert_serving_key(
        self, tenant_id: int, user_key: bytes, profile_ref: Optional[str] = None
    ) -> None:
        """Record that a visitor key exists, optionally linking it to a master profile."""
        with self._session_factory() as session:
            with session.begin():
                session.execute(
                    text(
                        """
                        INSERT INTO leo_ads.user_serving_key
                            (user_key_hash, tenant_id, profile_ref,
                             first_seen_at, last_seen_at, updated_at)
                        VALUES (:user_key, :tenant_id, :profile_ref, now(), now(), now())
                        ON CONFLICT (user_key_hash) DO UPDATE SET
                            profile_ref = COALESCE(EXCLUDED.profile_ref,
                                                   leo_ads.user_serving_key.profile_ref),
                            last_seen_at = now(),
                            updated_at = now()
                         WHERE leo_ads.user_serving_key.tenant_id = EXCLUDED.tenant_id
                        """
                    ),
                    {
                        "user_key": bytes(user_key),
                        "tenant_id": tenant_id,
                        "profile_ref": profile_ref,
                    },
                )

    def linked_keys(self, tenant_id: int, user_key: bytes) -> list[bytes]:
        """Every key linked to the same master profile as ``user_key`` (itself included)."""
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT sibling.user_key_hash
                    FROM leo_ads.user_serving_key own
                    JOIN leo_ads.user_serving_key sibling
                      ON sibling.tenant_id = own.tenant_id
                     AND sibling.profile_ref = own.profile_ref
                    WHERE own.tenant_id = :tenant_id
                      AND own.user_key_hash = :user_key
                      AND own.profile_ref IS NOT NULL
                    """
                ),
                {"tenant_id": tenant_id, "user_key": bytes(user_key)},
            ).scalars().all()
        keys = [bytes(k) for k in rows]
        return keys or [bytes(user_key)]

    def delete_for_key(self, tenant_id: int, user_key: bytes) -> dict[str, int]:
        """Remove a visitor's profile and serving key (erasure). Returns row counts."""
        params = {"tenant_id": tenant_id, "user_key": bytes(user_key)}
        with self._session_factory() as session:
            with session.begin():
                profile = session.execute(
                    text(
                        "DELETE FROM leo_ads.visitor_behaviour_profile "
                        "WHERE tenant_id = :tenant_id AND user_key_hash = :user_key"
                    ),
                    params,
                ).rowcount
                serving_key = session.execute(
                    text(
                        "DELETE FROM leo_ads.user_serving_key "
                        "WHERE tenant_id = :tenant_id AND user_key_hash = :user_key"
                    ),
                    params,
                ).rowcount
        return {"visitor_behaviour_profile": profile, "user_serving_key": serving_key}
