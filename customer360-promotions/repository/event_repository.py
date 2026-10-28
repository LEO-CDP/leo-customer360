"""
Ad event and serving decision repository.

Writes leo_ads.ad_event (impression, view, click, dismiss, conversion) and
leo_ads.serving_decision (why each ad was chosen), and reads a visitor's recent
events. Persistence only: validation, quality flags and batching are done by the
callers (event ingest and the buffered writer).

Inserts are idempotent. ad_event's primary key is (event_time, event_id), so a
client retry that re-sends the same event id and time is dropped silently
(ON CONFLICT DO NOTHING) instead of being counted twice.

Multi-tenancy:
    leo_ads has no row-level security. Every event row carries its own
    ``tenant_id``, every read takes a required ``tenant_id`` and filters by it.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

_EVENT_COLUMNS = (
    "event_id",
    "event_time",
    "tenant_id",
    "event_type",
    "ad_id",
    "campaign_id",
    "creative_id",
    "placement_id",
    "user_key_hash",
    "request_id",
    "session_id",
    "device_type",
    "country_code",
    "page_url",
    "revenue_amount",
    "currency",
    "payload",
    "flags",
)


class EventRepository:
    """Insert, read and erase ad events and serving decisions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def insert_events(self, events: Sequence[Mapping[str, Any]]) -> int:
        """Insert events in one statement batch; returns how many rows were new.

        Each event needs ``event_id``, ``event_time``, ``tenant_id`` and
        ``event_type``; the other columns are optional. Duplicates (same time and
        id) are ignored.
        """
        if not events:
            return 0

        rows = []
        for event in events:
            row = {column: event.get(column) for column in _EVENT_COLUMNS}
            row["payload"] = json.dumps(event.get("payload") or {})
            row["flags"] = list(event.get("flags") or [])
            user_key = row["user_key_hash"]
            row["user_key_hash"] = bytes(user_key) if user_key is not None else None
            rows.append(row)

        with self._session_factory() as session:
            with session.begin():
                result = session.execute(
                    text(
                        """
                        INSERT INTO leo_ads.ad_event
                            (event_id, event_time, tenant_id, event_type, ad_id,
                             campaign_id, creative_id, placement_id, user_key_hash,
                             request_id, session_id, device_type, country_code,
                             page_url, revenue_amount, currency, payload, flags)
                        VALUES
                            (CAST(:event_id AS uuid), :event_time, :tenant_id, :event_type,
                             :ad_id, :campaign_id, :creative_id, :placement_id,
                             :user_key_hash, CAST(:request_id AS uuid),
                             CAST(:session_id AS uuid), :device_type, :country_code,
                             :page_url, :revenue_amount, :currency,
                             CAST(:payload AS jsonb), :flags)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    rows,
                )
                return max(result.rowcount, 0)

    def insert_decisions(self, decisions: Sequence[Mapping[str, Any]]) -> int:
        """Insert serving decisions; a repeated (request_id, decided_at) is ignored."""
        if not decisions:
            return 0

        rows = []
        for decision in decisions:
            user_key = decision.get("user_key_hash")
            rows.append(
                {
                    "request_id": decision["request_id"],
                    "decided_at": decision["decided_at"],
                    "tenant_id": decision["tenant_id"],
                    "user_key_hash": bytes(user_key) if user_key is not None else None,
                    "placement_id": decision.get("placement_id"),
                    "group_code": decision["group_code"],
                    "chosen": json.dumps(decision.get("chosen") or []),
                    "candidate_count": int(decision.get("candidate_count") or 0),
                }
            )

        with self._session_factory() as session:
            with session.begin():
                result = session.execute(
                    text(
                        """
                        INSERT INTO leo_ads.serving_decision
                            (request_id, decided_at, tenant_id, user_key_hash,
                             placement_id, group_code, chosen, candidate_count)
                        VALUES
                            (CAST(:request_id AS uuid), :decided_at, :tenant_id,
                             :user_key_hash, :placement_id, :group_code,
                             CAST(:chosen AS jsonb), :candidate_count)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    rows,
                )
                return max(result.rowcount, 0)

    def recent_events(
        self,
        tenant_id: int,
        user_keys: Sequence[bytes],
        *,
        since,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """A visitor's unflagged events since ``since``, newest first."""
        if not user_keys:
            return []

        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT event_id, event_time, event_type, ad_id, campaign_id,
                           placement_id, request_id, payload
                    FROM leo_ads.ad_event
                    WHERE tenant_id = :tenant_id
                      AND user_key_hash = ANY(:user_keys)
                      AND event_time >= :since
                      AND cardinality(flags) = 0
                    ORDER BY event_time DESC
                    LIMIT :limit
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "user_keys": [bytes(k) for k in user_keys],
                    "since": since,
                    "limit": int(limit),
                },
            ).mappings().all()
        return [dict(r) for r in rows]

    def find_click(self, tenant_id: int, click_id: str, *, days: int = 30) -> Optional[dict[str, Any]]:
        """The most recent unflagged click event carrying ``click_id`` (conversion attribution)."""
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT ad_id, campaign_id, creative_id, placement_id, user_key_hash, request_id
                    FROM leo_ads.ad_event
                    WHERE tenant_id = :tenant_id
                      AND event_type = 'click'
                      AND payload ->> 'click_id' = :click_id
                      AND event_time >= now() - make_interval(days => :days)
                      AND cardinality(flags) = 0
                    ORDER BY event_time DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "click_id": click_id, "days": days},
            ).mappings().first()
        return dict(row) if row else None

    def find_by_request(self, tenant_id: int, request_id: str, *, days: int = 30) -> Optional[dict[str, Any]]:
        """The click (else impression) of a serve request, newest first, for attribution."""
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT ad_id, campaign_id, creative_id, placement_id, user_key_hash, request_id
                    FROM leo_ads.ad_event
                    WHERE tenant_id = :tenant_id
                      AND request_id = CAST(:request_id AS uuid)
                      AND event_type IN ('click', 'impression')
                      AND event_time >= now() - make_interval(days => :days)
                      AND cardinality(flags) = 0
                    ORDER BY (event_type = 'click') DESC, event_time DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "request_id": request_id, "days": days},
            ).mappings().first()
        return dict(row) if row else None

    def event_exists(self, tenant_id: int, event_id: str, around, *, within_hours: int = 24) -> bool:
        """Whether an event id is already stored near ``around`` (the time bound keeps it a pruned scan)."""
        with self._session_factory() as session:
            return session.execute(
                text(
                    """
                    SELECT 1 FROM leo_ads.ad_event
                    WHERE tenant_id = :tenant_id
                      AND event_id = CAST(:event_id AS uuid)
                      AND event_time BETWEEN :around - make_interval(hours => :h)
                                         AND :around + make_interval(hours => :h)
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "event_id": event_id, "around": around, "h": within_hours},
            ).first() is not None

    def delete_for_key(self, tenant_id: int, user_key: bytes) -> dict[str, int]:
        """Remove a visitor's events and decisions (erasure). Returns row counts."""
        params = {"tenant_id": tenant_id, "user_key": bytes(user_key)}
        with self._session_factory() as session:
            with session.begin():
                events = session.execute(
                    text(
                        "DELETE FROM leo_ads.ad_event "
                        "WHERE tenant_id = :tenant_id AND user_key_hash = :user_key"
                    ),
                    params,
                ).rowcount
                decisions = session.execute(
                    text(
                        "DELETE FROM leo_ads.serving_decision "
                        "WHERE tenant_id = :tenant_id AND user_key_hash = :user_key"
                    ),
                    params,
                ).rowcount
        return {"ad_event": events, "serving_decision": decisions}
