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

    def scalars(self):
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


def test_get_segmentable_attributes_has_deterministic_ui_order():
    attribute = SimpleNamespace(
        source_table="cdp_master_profiles",
        master_profile_column="customer_since",
        attribute_internal_code="customer_since",
        name="Customer Since",
        description="",
        attribute_group="LIFECYCLE",
        data_type="DATE",
        domain_scope="all",
        is_pii=False,
    )
    session = _Session(_Result(rows=[attribute]))
    repository = SegmentRepository(cast(Session, session))

    result = repository.get_segmentable_attributes()

    assert result[0]["field"] == "customer_since"
    sql, params = session.executed[0]
    order_by = sql[sql.index("ORDER BY ") :]
    assert "lower(" in order_by and "attribute_group" in order_by
    assert "display_order ASC NULLS LAST" in order_by
    assert "name" in order_by
    assert "attribute_internal_code" in order_by
    assert params is None


def test_get_segmentable_attributes_uses_master_column_type():
    attribute = SimpleNamespace(
        source_table="cdp_master_profiles, cdp_raw_profiles_stage",
        master_profile_column="latest_nps_score",
        attribute_internal_code="latest_nps_score",
        name="Latest NPS Score",
        description="",
        attribute_group="CX_SCORING",
        data_type="NUMERIC",
        domain_scope="all",
        is_pii=False,
    )
    session = _Session(_Result(rows=[attribute]))

    result = SegmentRepository(cast(Session, session)).get_segmentable_attributes()

    assert result[0]["field"] == "latest_nps_score"
    assert result[0]["data_type"] == "INTEGER"
    sql, _ = session.executed[0]
    assert "attribute_internal_code =" in sql
    assert "master_profile_column" in sql


def test_get_segmentable_attributes_casts_typed_domain_values():
    attribute = SimpleNamespace(
        source_table="cdp_domain_profiles",
        master_profile_column=None,
        attribute_internal_code="risk_score",
        name="Risk Score",
        description="",
        attribute_group="BANKING",
        data_type="NUMERIC",
        domain_scope="banking",
        is_pii=False,
    )
    session = _Session(_Result(rows=[attribute]))

    result = SegmentRepository(cast(Session, session)).get_segmentable_attributes()

    assert result[0]["field"] == "CAST(dp.domain_attributes->>'risk_score' AS NUMERIC)"
    assert result[0]["data_type"] == "NUMERIC"