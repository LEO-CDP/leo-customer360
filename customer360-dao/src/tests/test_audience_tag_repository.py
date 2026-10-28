"""Unit tests for CdpAudienceTag, AudienceTagRepository, and segment->profile audience_tags sync."""

import unittest
import uuid
from types import SimpleNamespace
from typing import Any, Optional, cast

from sqlalchemy.orm import Session

from leo_customer360_dao.crud.segmentation import recompute_segment_membership
from leo_customer360_dao.models.identity import CdpMasterProfile
from leo_customer360_dao.models.segmentation import CdpAudienceTag, CdpSegment
from leo_customer360_dao.repositories.audience_tag_repository import AudienceTagRepository
from leo_customer360_dao.repositories.segment_respository import SegmentRepository
from leo_customer360_dao.schemas.segmentation import (
    AudienceTagCreate,
    SegmentCreate,
    build_audience_tag_name,
)


class _FakeResult:
    def __init__(self, rows: Optional[list[Any]] = None, scalar: Any = None, rowcount: int = 0):
        self._rows = list(rows or [])
        self._scalar = scalar
        self.rowcount = rowcount

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar


class _FakeSession:
    def __init__(self, results: Optional[list[_FakeResult]] = None):
        self.results = list(results or [])
        self.executed: list[tuple[str, Optional[dict[str, Any]]]] = []
        self.committed = False
        self.flushed = False
        self.rolled_back = False
        self.store: dict[str, CdpAudienceTag] = {}

    def get(self, model: Any, pk: Any) -> Any:
        if model is CdpAudienceTag:
            return self.store.get(str(pk))
        return None

    def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> _FakeResult:
        sql = str(stmt)
        self.executed.append((sql, params))
        if self.results:
            return self.results.pop(0)
        return _FakeResult()

    def add(self, obj: Any) -> None:
        if isinstance(obj, CdpAudienceTag):
            self.store[obj.tag_name] = obj

    def flush(self) -> None:
        self.flushed = True

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, obj: Any) -> None:
        pass


class AudienceTagDaoTests(unittest.TestCase):
    def test_models_define_audience_tags_columns(self):
        self.assertIn("tag_name", CdpAudienceTag.__table__.columns)
        self.assertTrue(CdpAudienceTag.__table__.columns["tag_name"].primary_key)
        self.assertIn("audience_tags", CdpMasterProfile.__table__.columns)
        self.assertIn("audience_tags", CdpSegment.__table__.columns)

    def test_build_audience_tag_name_slugifies_tiers(self):
        tag_name = build_audience_tag_name(
            ["Demographic", "Age Range", "75+", None, "", None]
        )
        self.assertEqual(tag_name, "demographic/age-range/75-plus")

        tag_income = build_audience_tag_name(
            ["Demographic", "Household Data", "Household Income (USD)", "Less than $10,000"]
        )
        self.assertEqual(
            tag_income,
            "demographic/household-data/household-income-usd/less-than-usd-10-000",
        )

    def test_audience_tag_create_populates_tag_name_from_tiers(self):
        item = AudienceTagCreate(
            unique_id="3",
            parent_id="2",
            condensed_name="Demographic | Age Range | 18-20 |",
            tier_1="Demographic",
            tier_2="Age Range",
            tier_3="18-20",
        )
        self.assertEqual(item.tag_name, "demographic/age-range/18-20")

    def test_upsert_tag_executes_on_conflict_do_update_on_tag_name(self):
        session = _FakeSession()
        repo = AudienceTagRepository(cast(Session, session))
        payload = AudienceTagCreate(
            unique_id="3",
            parent_id="2",
            condensed_name="Demographic | Age Range | 18-20 |",
            tier_1="Demographic",
            tier_2="Age Range",
            tier_3="18-20",
        )

        tag = repo.upsert_tag(payload)

        self.assertEqual(tag.tag_name, "demographic/age-range/18-20")
        self.assertEqual(len(session.executed), 1)
        sql, _ = session.executed[0]
        self.assertIn("INSERT INTO", sql)
        self.assertIn("cdp_audience_tags", sql)
        self.assertIn("ON CONFLICT (tag_name) DO UPDATE", sql)
        self.assertTrue(session.committed)

    def test_bulk_upsert_tags_deduplicates_by_tag_name_and_batches(self):
        session = _FakeSession()
        repo = AudienceTagRepository(cast(Session, session))
        records = [
            AudienceTagCreate(
                unique_id="1",
                condensed_name="Demographic",
                tier_1="Demographic",
            ),
            AudienceTagCreate(
                unique_id="2",
                parent_id="1",
                condensed_name="Demographic | Age Range",
                tier_1="Demographic",
                tier_2="Age Range",
            ),
            # Duplicate tag_name should be deduplicated before insert
            AudienceTagCreate(
                tag_name="demographic/age-range",
                unique_id="2",
                parent_id="1",
                condensed_name="Demographic | Age Range Updated",
                tier_1="Demographic",
                tier_2="Age Range",
            ),
        ]

        count = repo.bulk_upsert_tags(records, batch_size=1)

        self.assertEqual(count, 2)
        self.assertEqual(len(session.executed), 2)
        self.assertTrue(session.committed)

    def test_list_tags_has_parent_filter_adds_parent_id_is_not_null(self):
        session = _FakeSession(results=[_FakeResult(rows=[])])
        repo = AudienceTagRepository(cast(Session, session))

        repo.list_tags(has_parent=True)

        sql, _ = session.executed[0]
        self.assertIn("parent_id IS NOT NULL", sql)

    def test_validate_tag_names_checks_active_tags_in_db(self):
        session = _FakeSession(
            results=[_FakeResult(rows=["demographic/age-range/18-20"])]
        )
        repo = AudienceTagRepository(cast(Session, session))

        valid = repo.validate_tag_names(
            ["  demographic/age-range/18-20 ", "demographic/age-range/18-20"]
        )
        self.assertEqual(valid, ["demographic/age-range/18-20"])

        session_missing = _FakeSession(
            results=[_FakeResult(rows=["demographic/age-range/18-20"])]
        )
        repo_missing = AudienceTagRepository(cast(Session, session_missing))
        with self.assertRaises(ValueError) as ctx:
            repo_missing.validate_tag_names(
                ["demographic/age-range/18-20", "interest/nonexistent"]
            )
        self.assertIn("interest/nonexistent", str(ctx.exception))

    def test_recompute_segment_membership_syncs_audience_tags_to_master_profiles(self):
        matched_id = str(uuid.uuid4())
        session = _FakeSession(
            results=[
                _FakeResult(rows=[(uuid.UUID(matched_id),)]),
                _FakeResult(),
                _FakeResult(),
                _FakeResult(),
                _FakeResult(),
            ]
        )
        segment = SimpleNamespace(
            tenant_id=uuid.uuid4(),
            segment_tag="gen_z_gamers",
            audience_tags=["demographic/age-range/18-20", "interest/video-gaming"],
            sql_rules="age < 25",
            member_count=0,
            last_computed_at=None,
        )

        recompute_segment_membership(cast(Session, session), cast(CdpSegment, segment))

        self.assertEqual(len(session.executed), 5)
        add_audience_sql, add_audience_params = session.executed[3]
        self.assertIn("SET audience_tags = ARRAY(", add_audience_sql)
        self.assertEqual(
            add_audience_params["audience_tags"],
            ["demographic/age-range/18-20", "interest/video-gaming"],
        )
        remove_audience_sql, remove_audience_params = session.executed[4]
        self.assertIn("SET audience_tags = ARRAY(", remove_audience_sql)
        self.assertEqual(remove_audience_params["segment_tag"], "gen_z_gamers")

    def test_segment_assign_audience_tags_validates_updates_and_recomputes(self):
        matched_id = str(uuid.uuid4())
        segment_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        segment = SimpleNamespace(
            segment_id=segment_id,
            tenant_id=tenant_id,
            segment_tag="young_investors",
            audience_tags=[],
            sql_rules="age < 30",
            member_count=0,
            last_computed_at=None,
        )
        session = _FakeSession(
            results=[
                _FakeResult(rows=["demographic/personal-finance"]),
                _FakeResult(rows=[(uuid.UUID(matched_id),)]),
                _FakeResult(),
                _FakeResult(),
                _FakeResult(),
                _FakeResult(),
            ]
        )
        repo = SegmentRepository(cast(Session, session))
        repo.get_segment = lambda sid: cast(CdpSegment, segment)
        repo._crud.update = lambda db, db_obj, obj_in: (
            setattr(db_obj, "audience_tags", obj_in["audience_tags"]) or db_obj
        )

        updated = repo.assign_audience_tags(
            segment_id,
            ["demographic/personal-finance"],
            recompute_profiles=True,
        )

        self.assertEqual(updated.audience_tags, ["demographic/personal-finance"])
        self.assertEqual(updated.member_count, 1)

    def test_segment_create_schema_normalizes_audience_tags(self):
        seg = SegmentCreate(
            tenant_id=uuid.uuid4(),
            segment_tag="affluent_travelers",
            audience_tags=[
                " purchase-intent/travel-and-tourism ",
                "demographic/personal-finance",
                "purchase-intent/travel-and-tourism",
            ],
            segment_name="Affluent Travelers",
        )
        self.assertEqual(
            seg.audience_tags,
            ["purchase-intent/travel-and-tourism", "demographic/personal-finance"],
        )


if __name__ == "__main__":
    unittest.main()
