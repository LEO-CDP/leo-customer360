"""Unit tests for core.crud.crm_sync.sync_segment_to_crm -- the segment-ID
driven CRM sync engine (SCRUM-94 / SUBTASK-02).

Uses a fake SQLAlchemy Session double that records every execute() call and
returns scripted member rows, so routing/idempotency/counts are asserted
without a real PostgreSQL instance. recompute_segment_membership is patched
out (it has its own tests in test_segmentation_crud.py)."""

import unittest
import uuid
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from core.crud import crm_sync
from core.crud.crm_sync import (
    ROUTE_CONTACT,
    ROUTE_CUSTOMER,
    ROUTE_LEAD,
    _deterministic_id,
    classify_route,
    sync_segment_to_crm,
)


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _Savepoint:
    """Context manager stand-in for Session.begin_nested(): re-raises the body
    error like a real savepoint (the engine catches it per-member)."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, member_rows: list[dict], fail_upsert_for: Optional[set] = None):
        self._member_rows = member_rows
        self._select_done = False
        self.fail_upsert_for = fail_upsert_for or set()
        self.executed: list[tuple[str, Optional[dict[str, Any]]]] = []
        self.added: list[Any] = []
        self.committed = 0
        self.rolled_back = 0
        self.nested = 0

    def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> _FakeResult:
        sql = str(stmt)
        self.executed.append((sql, params))
        # The keyset member SELECT is the only statement with this predicate.
        if "cdp_master_profiles.master_profile_id > CAST(:last_id AS uuid)" in sql:
            if self._select_done:
                return _FakeResult([])
            self._select_done = True
            return _FakeResult(list(self._member_rows))
        # Upsert INSERTs: optionally simulate a per-member DB error.
        if params and params.get("master_profile_id") in self.fail_upsert_for:
            raise RuntimeError("simulated upsert error")
        return _FakeResult([])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def refresh(self, obj: Any) -> None:  # pragma: no cover - unused
        pass

    def begin_nested(self) -> _Savepoint:
        self.nested += 1
        return _Savepoint()


def _segment(**overrides) -> SimpleNamespace:
    defaults = dict(
        segment_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        sql_rules="engagement_score > 0",
        segment_tag="tag",
        member_count=0,
        last_computed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _member(**overrides) -> dict:
    row = dict(
        master_profile_id=uuid.uuid4(),
        email="user@example.com",
        phone_number=None,
        first_name="Ada",
        last_name="Lovelace",
        full_name="Ada Lovelace",
        lifecycle_stage="contact",
        acquisition_source=None,
        preferred_channel=None,
        last_activity_at=None,
        engagement_score=None,
        persona_summary=None,
        attributes={},
    )
    row.update(overrides)
    return row


def _executed_sql(session: _FakeSession) -> str:
    return "\n".join(sql for sql, _ in session.executed)


class ClassifyRouteTests(unittest.TestCase):
    def test_customer_stage_only(self):
        self.assertEqual(classify_route("customer"), ROUTE_CUSTOMER)

    def test_lead_stage_only(self):
        self.assertEqual(classify_route("lead"), ROUTE_LEAD)

    def test_other_stages_route_to_contact(self):
        for stage in ("prospect", "vip", "dormant", "churn_risk", "", None, "CUSTOMERS"):
            self.assertEqual(classify_route(stage), ROUTE_CONTACT)

    def test_stage_is_case_insensitive_and_trimmed(self):
        self.assertEqual(classify_route("  Customer "), ROUTE_CUSTOMER)


class DeterministicIdTests(unittest.TestCase):
    def test_same_inputs_same_id(self):
        a = _deterministic_id("t", "lead", "m")
        b = _deterministic_id("t", "lead", "m")
        self.assertEqual(a, b)
        self.assertIsInstance(a, uuid.UUID)

    def test_different_inputs_differ(self):
        self.assertNotEqual(_deterministic_id("t", "lead", "m1"), _deterministic_id("t", "lead", "m2"))
        self.assertNotEqual(_deterministic_id("t", "lead", "m"), _deterministic_id("t", "contact", "m"))


class SyncValidationTests(unittest.TestCase):
    def test_raises_when_no_sql_rules(self):
        with self.assertRaises(ValueError):
            sync_segment_to_crm(_FakeSession([]), _segment(sql_rules=None), tenant_id=uuid.uuid4())

    def test_raises_for_unsafe_sql_rules(self):
        segment = _segment(sql_rules="1=1; DROP TABLE cdp_master_profiles;")
        with self.assertRaises(ValueError):
            sync_segment_to_crm(_FakeSession([]), segment, tenant_id=segment.tenant_id)


class SyncRoutingTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(crm_sync, "recompute_segment_membership", lambda db, seg: seg)
        self.mock_recompute = patcher.start()
        self.addCleanup(patcher.stop)

    def test_routes_members_by_lifecycle_stage(self):
        segment = _segment()
        members = [
            _member(lifecycle_stage="customer", preferred_channel="app", engagement_score=42),
            _member(lifecycle_stage="lead", acquisition_source="organic_search"),
            _member(lifecycle_stage="vip"),  # -> contact
            _member(lifecycle_stage="prospect"),  # -> contact
        ]
        session = _FakeSession(members)

        result = sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id)

        counts = result["route_counts"]
        self.assertEqual(counts["matched"], 4)
        self.assertEqual(counts["customer"], 1)
        self.assertEqual(counts["lead"], 1)
        self.assertEqual(counts["contact"], 2)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(counts["error"], 0)
        self.assertEqual(result["status"], "Completed")
        self.assertFalse(result["dry_run"])
        # An audit run row was added + committed.
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].status, "Completed")
        self.assertEqual(session.added[0].matched_count, 4)
        self.assertTrue(session.committed)

    def test_member_select_and_upserts_are_tenant_scoped(self):
        segment = _segment()
        session = _FakeSession([_member(lifecycle_stage="lead", acquisition_source="ref")])

        sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id)

        for sql, params in session.executed:
            if params is not None and "tenant_id" in params:
                self.assertEqual(params["tenant_id"], str(segment.tenant_id))

    def test_lead_without_identity_is_skipped(self):
        segment = _segment()
        member = _member(
            lifecycle_stage="lead",
            email=None,
            phone_number=None,
            first_name=None,
            last_name=None,
            full_name=None,
        )
        session = _FakeSession([member])

        counts = sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id)["route_counts"]

        self.assertEqual(counts["lead"], 0)
        self.assertEqual(counts["skipped"], 1)

    def test_customer_transactions_come_from_attributes_not_fabricated(self):
        segment = _segment()
        with_facts = _member(
            lifecycle_stage="customer",
            engagement_score=10,
            attributes={"transactions": [
                {"source_system": "POS", "source_transaction_id": "t1", "amount": 100},
                {"source_system": "POS", "source_transaction_id": "t2", "amount": 200},
            ]},
        )
        without_facts = _member(lifecycle_stage="customer", engagement_score=5, attributes={})
        session = _FakeSession([with_facts, without_facts])

        result = sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id)

        self.assertEqual(result["detail"]["transactions_written"], 2)
        # Both customers had an engagement signal -> two contact-log rows.
        self.assertEqual(result["detail"]["customer_contacts_written"], 2)

    def test_customer_contact_skipped_without_signal(self):
        segment = _segment()
        member = _member(
            lifecycle_stage="customer",
            preferred_channel=None,
            last_activity_at=None,
            engagement_score=None,
        )
        session = _FakeSession([member])

        result = sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id)

        self.assertEqual(result["route_counts"]["customer"], 1)
        self.assertEqual(result["detail"]["customer_contacts_written"], 0)

    def test_upserts_use_on_conflict_for_idempotency(self):
        segment = _segment()
        members = [
            _member(lifecycle_stage="customer", engagement_score=1,
                    attributes={"transactions": [{"source_system": "POS", "source_transaction_id": "x", "amount": 1}]}),
            _member(lifecycle_stage="lead", acquisition_source="ref"),
            _member(lifecycle_stage="prospect"),
        ]
        session = _FakeSession(members)

        sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id)

        sql = _executed_sql(session)
        for target in ("crm_customer_contacts", "crm_transactions", "crm_lead_source", "crm_lead", "crm_contact"):
            self.assertIn(target, sql)
        # Every target INSERT is an idempotent upsert (ON CONFLICT DO UPDATE):
        # one ON CONFLICT clause per INSERT INTO statement.
        self.assertEqual(sql.count("INSERT INTO"), sql.count("ON CONFLICT"))
        self.assertEqual(sql.count("INSERT INTO"), 5)

    def test_per_member_error_is_isolated_and_counted(self):
        segment = _segment()
        bad_id = uuid.uuid4()
        members = [
            _member(lifecycle_stage="customer", engagement_score=1),
            _member(master_profile_id=bad_id, lifecycle_stage="customer", engagement_score=1),
        ]
        # The customer route binds master_profile_id as a param, so the fake can
        # target one member's upsert; a raised error must not abort the run.
        session = _FakeSession(members, fail_upsert_for={str(bad_id)})

        counts = sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id)["route_counts"]

        self.assertEqual(counts["matched"], 2)
        self.assertEqual(counts["error"], 1)
        self.assertEqual(counts["customer"], 2)
        # The run still completes (per-member error isolated via savepoint).
        self.assertEqual(session.added[0].status, "Completed")


class SyncDryRunTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(crm_sync, "recompute_segment_membership")
        self.mock_recompute = patcher.start()
        self.addCleanup(patcher.stop)

    def test_dry_run_skips_recompute_and_writes_no_target_rows(self):
        segment = _segment()
        members = [
            _member(lifecycle_stage="customer", engagement_score=3,
                    attributes={"transactions": [{"source_system": "POS", "source_transaction_id": "d1", "amount": 9}]}),
            _member(lifecycle_stage="lead", acquisition_source="ref"),
        ]
        session = _FakeSession(members)

        result = sync_segment_to_crm(session, segment, tenant_id=segment.tenant_id, dry_run=True)

        self.mock_recompute.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["status"], "Completed")
        # Counts still computed in dry-run.
        self.assertEqual(result["route_counts"]["customer"], 1)
        self.assertEqual(result["route_counts"]["lead"], 1)
        self.assertEqual(result["detail"]["transactions_written"], 1)
        # No INSERT into any crm_* target table happened.
        self.assertNotIn("INSERT INTO", _executed_sql(session))
        self.assertFalse(result["detail"]["recomputed"])


if __name__ == "__main__":
    unittest.main()
