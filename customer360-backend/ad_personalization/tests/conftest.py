"""Fixtures for the profile aggregation and ad statistics database tests.

These run against the real ``leo_ads`` schema (with personalization-schema.sql
applied) INSIDE a transaction that is always rolled back, so nothing persists.
Because they need a database, they are opt-in, like the partition tests:

    AD_PERSONALIZATION_ALLOW_DB_TESTS=1 DB_HOST=127.0.0.1 DB_PORT=55432 \
        DB_PASSWORD=... python -m pytest ad_personalization/tests

Never point them at a database you care about: ``refresh_ad_stats`` replaces
every ``ad_stat_hourly`` bucket in its window (rolled back, but still a lock).
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pytest
from psycopg2.extras import Json

from ad_personalization.db import connect

SCHEMA = "leo_ads"

requires_db = pytest.mark.skipif(
    os.environ.get("AD_PERSONALIZATION_ALLOW_DB_TESTS") != "1",
    reason="set AD_PERSONALIZATION_ALLOW_DB_TESTS=1 (and DB_* env) to run against a disposable database",
)


@dataclass
class Tenant:
    conn: object
    tenant_id: int
    creative_id: int
    placement_id: int

    def _one(self, query, params):
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()[0]

    def add_ad(self, ad_key: str, *, tags: Optional[list] = None, advertiser_id: Optional[int] = None) -> int:
        creative_id = self.creative_id
        if advertiser_id is not None:
            creative_id = self._one(
                f"INSERT INTO {SCHEMA}.creative (tenant_id, creative_key, ad_type, format_code, advertiser_id) "
                "VALUES (%s, %s, 'display', '300x250', %s) RETURNING creative_id",
                (self.tenant_id, "cr-" + uuid.uuid4().hex[:8], advertiser_id),
            )
        return self._one(
            f"INSERT INTO {SCHEMA}.ad (tenant_id, ad_key, creative_id, placement_id, status, metadata) "
            "VALUES (%s, %s, %s, %s, 'active', %s) RETURNING ad_id",
            (self.tenant_id, ad_key, creative_id, self.placement_id, Json({"tags": tags} if tags else {})),
        )

    def add_advertiser(self) -> int:
        return self._one(
            f"INSERT INTO {SCHEMA}.advertiser (tenant_id, advertiser_key, name) "
            "VALUES (%s, %s, 'adv') RETURNING advertiser_id",
            (self.tenant_id, "adv-" + uuid.uuid4().hex[:8]),
        )

    def settings(self, *, enabled: bool = True, consent_mode: str = "opt_in") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.personalization_setting "
                "(tenant_id, scope_type, scope_ref, enabled, consent_mode) VALUES (%s, 'tenant', '', %s, %s) "
                "ON CONFLICT (tenant_id, scope_type, scope_ref) DO UPDATE SET "
                "enabled = EXCLUDED.enabled, consent_mode = EXCLUDED.consent_mode",
                (self.tenant_id, enabled, consent_mode),
            )

    def consent(self, key: bytes, status: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.consent_record (tenant_id, user_key_hash, status) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, user_key_hash) DO UPDATE SET status = EXCLUDED.status",
                (self.tenant_id, key, status),
            )

    def event(
        self,
        ad_id: int,
        event_type: str,
        at: datetime,
        *,
        key: Optional[bytes] = None,
        flags: Optional[list] = None,
        request_id: Optional[str] = None,
    ) -> str:
        request_id = request_id or str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.ad_event "
                "(event_time, tenant_id, event_type, ad_id, placement_id, user_key_hash, request_id, flags) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (at, self.tenant_id, event_type, ad_id, self.placement_id, key, request_id, flags or []),
            )
        return request_id

    def decision(self, request_id: str, group_code: str, at: datetime) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.serving_decision (request_id, decided_at, tenant_id, group_code) "
                "VALUES (%s, %s, %s, %s)",
                (request_id, at, self.tenant_id, group_code),
            )

    def profiles(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT user_key_hash, affinities, recent_dismissed, event_count, profile_version "
                f"FROM {SCHEMA}.visitor_behaviour_profile WHERE tenant_id = %s",
                (self.tenant_id,),
            )
            return {
                bytes(r[0]): {"affinities": r[1], "dismissed": r[2], "events": r[3], "version": r[4]}
                for r in cur.fetchall()
            }

    def stats(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT ad_id, hour_start, group_code, impressions, views, clicks, conversions "
                f"FROM {SCHEMA}.ad_stat_hourly WHERE tenant_id = %s",
                (self.tenant_id,),
            )
            return {(r[0], r[1], r[2]): tuple(r[3:]) for r in cur.fetchall()}


@pytest.fixture
def db():
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT to_regclass('{SCHEMA}.consent_record')")
            if cur.fetchone()[0] is None:
                pytest.skip("personalization-schema.sql has not been applied to this database")
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def tenant_factory(db):
    def make() -> Tenant:
        with db.cursor() as cur:
            key = "t-" + uuid.uuid4().hex[:10]
            cur.execute(
                f"INSERT INTO {SCHEMA}.tenant (tenant_key, name) VALUES (%s, %s) RETURNING tenant_id", (key, key)
            )
            tenant_id = cur.fetchone()[0]
            cur.execute(
                f"INSERT INTO {SCHEMA}.placement (tenant_id, placement_key, name) VALUES (%s, 'p', 'p') "
                "RETURNING placement_id",
                (tenant_id,),
            )
            placement_id = cur.fetchone()[0]
            cur.execute(
                f"INSERT INTO {SCHEMA}.creative (tenant_id, creative_key, ad_type, format_code) "
                "VALUES (%s, %s, 'display', '300x250') RETURNING creative_id",
                (tenant_id, "cr-" + uuid.uuid4().hex[:8]),
            )
            creative_id = cur.fetchone()[0]
        return Tenant(db, tenant_id, creative_id, placement_id)

    return make


NOW = datetime(2026, 10, 18, 9, 30, tzinfo=timezone.utc)
