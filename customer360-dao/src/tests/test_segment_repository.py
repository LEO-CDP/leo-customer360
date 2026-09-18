"""Unit tests for segment matching query construction."""

import uuid
from types import SimpleNamespace
from typing import cast

from sqlalchemy.orm import Session

from leo_customer360_dao.repositories.segment_respository import SegmentRepository
from leo_customer360_dao.models.segmentation import CdpSegment


class _Result:
    def __init__(self, rows=None, count=None):
        self.rows = rows or []
        self.count = count

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.count


class _Session:
    def __init__(self, result):
        self.result = result
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return self.result


def _segment(tenant_id):
    return SimpleNamespace(
        segment_id=uuid.uuid4(),
        tenant_id=tenant_id,
        sql_rules="churn_risk_tier IN ('high', 'critical')",
    )


def test_count_matched_profiles_builds_tenant_scoped_query():
    tenant_id = uuid.uuid4()
    session = _Session(_Result(count=7))
    segment = _segment(tenant_id)
    repository = SegmentRepository(cast(Session, session))
    repository.get_segment = lambda segment_id: cast(CdpSegment, segment)

    assert repository.count_matched_profiles(segment.segment_id, segment.sql_rules) == 7

    sql, params = session.executed[0]
    assert "cdp_master_profiles" in sql
    assert "cdp_domain_profiles" in sql
    assert segment.sql_rules in sql
    assert params == {"tenant_id": str(tenant_id)}


def test_get_matched_profiles_builds_paged_query():
    tenant_id = uuid.uuid4()
    session = _Session(_Result(rows=[{"master_profile_id": str(uuid.uuid4())}]))
    segment = _segment(tenant_id)
    repository = SegmentRepository(cast(Session, session))
    repository.get_segment = lambda segment_id: cast(CdpSegment, segment)

    rows = repository.get_matched_profiles(segment.segment_id, segment.sql_rules, skip=5, limit=10)

    assert len(rows) == 1
    sql, params = session.executed[0]
    assert "ORDER BY created_at DESC" in sql
    assert params == {"tenant_id": str(tenant_id), "limit": 10, "skip": 5}