"""Unit tests for core.crud.segmentation.recompute_segment_membership --
the shared implementation behind POST /segments/{id}/recompute (see
tests/test_segment_router.py::SegmentRecomputeTests for the HTTP-level
tests, which mock this function out entirely).

Uses a fake SQLAlchemy Session double that records every execute() call
instead of a real PostgreSQL instance.
"""

import unittest
import uuid
from types import SimpleNamespace
from typing import Any, Optional

from core.crud.segmentation import recompute_segment_membership


class _FakeRowsResult:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Records every execute() call; the first SELECT-shaped statement
    returns the configured matched_ids, everything else (the two UPDATE
    statements) returns an empty result."""

    def __init__(self, matched_ids: list[str]):
        self._matched_ids = matched_ids
        self.executed: list[tuple[str, Optional[dict[str, Any]]]] = []
        self.committed = False
        self.refreshed: list[Any] = []

    def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> _FakeRowsResult:
        sql = str(stmt)
        self.executed.append((sql, params))
        if "SELECT master_profile_id" in sql:
            return _FakeRowsResult([(uuid.UUID(mid),) for mid in self._matched_ids])
        return _FakeRowsResult([])

    def add(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)


def _segment(**overrides) -> SimpleNamespace:
    defaults = dict(
        tenant_id=uuid.uuid4(),
        segment_tag="gen_z_shopper",
        sql_rules="age < 25",
        member_count=0,
        last_computed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class RecomputeSegmentMembershipTests(unittest.TestCase):
    def test_raises_when_no_sql_rules(self):
        segment = _segment(sql_rules=None)

        with self.assertRaises(ValueError):
            recompute_segment_membership(_FakeSession([]), segment)

    def test_raises_for_unsafe_sql_rules(self):
        segment = _segment(sql_rules="1=1; DROP TABLE cdp_master_profiles;")

        with self.assertRaises(ValueError):
            recompute_segment_membership(_FakeSession([]), segment)

    def test_updates_member_count_and_last_computed_at(self):
        segment = _segment()
        matched_id = str(uuid.uuid4())
        session = _FakeSession(matched_ids=[matched_id])

        result = recompute_segment_membership(session, segment)

        self.assertIs(result, segment)
        self.assertEqual(segment.member_count, 1)
        self.assertIsNotNone(segment.last_computed_at)
        self.assertTrue(session.committed)

    def test_zero_matches_sets_member_count_to_zero(self):
        segment = _segment(member_count=5)
        session = _FakeSession(matched_ids=[])

        recompute_segment_membership(session, segment)

        self.assertEqual(segment.member_count, 0)

    def test_executes_select_then_add_tag_then_remove_tag_statements(self):
        segment = _segment()
        matched_id = str(uuid.uuid4())
        session = _FakeSession(matched_ids=[matched_id])

        recompute_segment_membership(session, segment)

        self.assertEqual(len(session.executed), 3)

        select_sql, select_params = session.executed[0]
        self.assertIn("cdp_master_profiles", select_sql)
        self.assertIn("cdp_domain_profiles", select_sql)
        self.assertIn("age < 25", select_sql)
        self.assertEqual(select_params["tenant_id"], str(segment.tenant_id))

        add_sql, add_params = session.executed[1]
        self.assertIn("array_append", add_sql)
        self.assertEqual(add_params["matched_ids"], [matched_id])
        self.assertEqual(add_params["tag"], "gen_z_shopper")

        remove_sql, remove_params = session.executed[2]
        self.assertIn("array_remove", remove_sql)
        self.assertEqual(remove_params["matched_ids"], [matched_id])
        self.assertEqual(remove_params["tag"], "gen_z_shopper")


if __name__ == "__main__":
    unittest.main()
