"""Unit tests for IAB Audience Taxonomy TSV parsing, API endpoints, and segment audience_tags assignment."""

from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.routers.audience_tag_api import audience_tags_router
from core.routers.segment_api import segments_router
from core.utils.audience_taxonomy_utils import (
    DEFAULT_AUDIENCE_TAXONOMY_TSV_PATH,
    AudienceTaxonomyTsvError,
    load_audience_taxonomy_tsv_file,
    parse_audience_taxonomy_tsv,
)
from leo_customer360_dao.schemas.segmentation import AudienceTagCreate


class _InMemoryAudienceTagRepo:
    store: dict[str, SimpleNamespace] = {}

    def __init__(self, _db: Any = None) -> None:
        self.db = _db

    @classmethod
    def reset(cls) -> None:
        cls.store = {}

    def list_tags(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        tier_1: Optional[str] = None,
        has_parent: Optional[bool] = None,
        q: Optional[str] = None,
        **_kwargs: Any,
    ):
        items = list(self.store.values())
        if tier_1:
            items = [i for i in items if i.tier_1 == tier_1]
        if has_parent is True:
            items = [i for i in items if i.parent_id is not None and str(i.parent_id).strip() != ""]
        elif has_parent is False:
            items = [i for i in items if i.parent_id is None or str(i.parent_id).strip() == ""]
        if q:
            needle = q.lower()
            items = [
                i
                for i in items
                if needle in i.tag_name.lower() or needle in i.condensed_name.lower()
            ]
        return items[skip : skip + limit]

    def count_tags(
        self,
        *,
        tier_1: Optional[str] = None,
        has_parent: Optional[bool] = None,
        q: Optional[str] = None,
        **_kwargs: Any,
    ) -> int:
        return len(self.list_tags(skip=0, limit=100_000, tier_1=tier_1, has_parent=has_parent, q=q))

    def get_tag(self, tag_name: str) -> Optional[SimpleNamespace]:
        return self.store.get(tag_name.strip())

    def upsert_tag(self, record: AudienceTagCreate, *, commit: bool = True) -> SimpleNamespace:
        obj = SimpleNamespace(
            tag_name=record.tag_name,
            unique_id=record.unique_id,
            parent_id=record.parent_id,
            taxonomy_version=record.taxonomy_version,
            condensed_name=record.condensed_name,
            tier_1=record.tier_1,
            tier_2=record.tier_2,
            tier_3=record.tier_3,
            tier_4=record.tier_4,
            tier_5=record.tier_5,
            tier_6=record.tier_6,
            extension_notes=record.extension_notes,
            status_code=record.status_code,
            created_at=None,
            updated_at=None,
        )
        self.store[obj.tag_name] = obj
        return obj

    def bulk_upsert_tags(self, records: list[AudienceTagCreate], *, commit: bool = True, **_kwargs: Any) -> int:
        deduped = {r.tag_name: r for r in records}
        for r in deduped.values():
            self.upsert_tag(r, commit=commit)
        return len(deduped)

    def import_from_tsv_bytes(self, content: bytes, *, taxonomy_version: str = "1.1"):
        from leo_customer360_dao.schemas.segmentation import AudienceTagImportResult

        records = parse_audience_taxonomy_tsv(content, taxonomy_version=taxonomy_version)
        upserted = self.bulk_upsert_tags(records, commit=True)
        return AudienceTagImportResult(
            taxonomy_version=taxonomy_version,
            total_rows_parsed=len(records),
            upserted_count=upserted,
            sample_tag_names=[r.tag_name for r in records[:10] if r.tag_name],
        )

    def import_from_tsv_file(self, file_path: Any = None, *, taxonomy_version: str = "1.1"):
        from leo_customer360_dao.schemas.segmentation import AudienceTagImportResult

        records = load_audience_taxonomy_tsv_file(file_path, taxonomy_version=taxonomy_version)
        upserted = self.bulk_upsert_tags(records, commit=True)
        return AudienceTagImportResult(
            taxonomy_version=taxonomy_version,
            total_rows_parsed=len(records),
            upserted_count=upserted,
            sample_tag_names=[r.tag_name for r in records[:10] if r.tag_name],
        )

    def update_tag(self, tag_name: str, payload: Any) -> Optional[SimpleNamespace]:
        obj = self.get_tag(tag_name)
        if obj is None:
            return None
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(obj, k, v)
        return obj

    def delete_tag(self, tag_name: str) -> bool:
        return self.store.pop(tag_name.strip(), None) is not None

    def validate_tag_names(self, tag_names: list[str]) -> list[str]:
        missing = [t for t in tag_names if t not in self.store]
        if missing:
            raise ValueError("Unknown or inactive IAB audience tag_name(s): " + ", ".join(missing))
        return tag_names


class AudienceTaxonomyTsvTests(unittest.TestCase):
    def test_real_audience_taxonomy_1_1_tsv_parses_all_1558_unique_tags(self):
        self.assertTrue(
            DEFAULT_AUDIENCE_TAXONOMY_TSV_PATH.is_file(),
            f"Expected real TSV at {DEFAULT_AUDIENCE_TAXONOMY_TSV_PATH}",
        )
        records = load_audience_taxonomy_tsv_file()

        self.assertEqual(len(records), 1558)
        tag_names = [r.tag_name for r in records]
        self.assertEqual(len(set(tag_names)), 1558)
        self.assertEqual(records[0].tag_name, "demographic")
        self.assertEqual(records[0].unique_id, "1")
        self.assertIsNone(records[0].parent_id)
        self.assertEqual(records[2].tag_name, "demographic/age-range/18-20")
        self.assertEqual(records[2].unique_id, "3")
        self.assertEqual(records[2].parent_id, "2")

    def test_parse_tsv_rejects_empty_or_missing_headers(self):
        with self.assertRaises(AudienceTaxonomyTsvError):
            parse_audience_taxonomy_tsv(b"")

        with self.assertRaises(AudienceTaxonomyTsvError):
            parse_audience_taxonomy_tsv(b"Unique ID\tParent ID\n1\t\n")


class AudienceTagApiTests(unittest.TestCase):
    def setUp(self) -> None:
        _InMemoryAudienceTagRepo.reset()
        self._cache_patcher = patch("core.cache.get_redis_client", return_value=None)
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

        self._repo_patcher = patch(
            "core.routers.audience_tag_api.AudienceTagRepository",
            _InMemoryAudienceTagRepo,
        )
        self._repo_patcher.start()
        self.addCleanup(self._repo_patcher.stop)

        app = FastAPI()
        app.include_router(audience_tags_router)
        app.include_router(segments_router)
        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        self.client = TestClient(app)

    def test_upload_tsv_and_upsert_by_tag_name(self):
        sample_tsv = (
            "\tUnique ID\tParent ID\tCondensed Name (1st, 2nd, Last Tier)\tTier 1\tTier 2\tTier 3\tTier 4\tTier 5\tTier 6\t*Extension Notes\n"
            "\t1\t\tDemographic\tDemographic\t\t\t\t\t\t\n"
            "\t2\t1\tDemographic | Age Range\tDemographic\tAge Range\t\t\t\t\t\n"
            "\t3\t2\tDemographic | Age Range | 18-20 |\tDemographic\tAge Range\t18-20\t\t\t\t\n"
        ).encode("utf-8")

        response = self.client.post(
            "/audience-tags/import/tsv",
            files={"file": ("audience_taxonomy1.1.tsv", sample_tsv, "text/tab-separated-values")},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_rows_parsed"], 3)
        self.assertEqual(body["upserted_count"], 3)
        self.assertIn("demographic/age-range/18-20", body["sample_tag_names"])

        # Fetch by slash-containing tag_name primary key
        get_resp = self.client.get("/audience-tags/demographic/age-range/18-20")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["unique_id"], "3")
        self.assertEqual(get_resp.json()["tier_3"], "18-20")

        # Upsert existing tag_name updates it in place
        upsert_resp = self.client.post(
            "/audience-tags/",
            json={
                "tag_name": "demographic/age-range/18-20",
                "unique_id": "3",
                "parent_id": "2",
                "condensed_name": "Demographic | Age Range | 18-20 (Updated)",
                "tier_1": "Demographic",
                "tier_2": "Age Range",
                "tier_3": "18-20",
            },
        )
        self.assertEqual(upsert_resp.status_code, 200)
        self.assertEqual(
            upsert_resp.json()["condensed_name"],
            "Demographic | Age Range | 18-20 (Updated)",
        )
        self.assertEqual(self.client.get("/audience-tags/count").json()["count"], 3)

        # Create/update segment with audience_tags automatically includes audience_tags in sql_rules & final_generated_sql
        from core.repositories.segment_repository import SegmentRepository

        tenant_id = uuid.uuid4()
        transformed = SegmentRepository.transform_create(
            {
                "tenant_id": tenant_id,
                "segment_name": "test audience tags",
                "segment_tag": "audiencetags",
                "audience_tags": [
                    "interest/books-and-literature/travel-books",
                    "interest/books-and-literature/young-adult-literature",
                    "interest/business-and-finance",
                ],
                "sql_rules": "(gender = 'male')",
            }
        )
        self.assertIn("audience_tags && ARRAY[", transformed["sql_rules"])
        self.assertIn("interest/books-and-literature/travel-books", transformed["sql_rules"])
        self.assertIn("audience_tags && ARRAY[", transformed["final_generated_sql"])
        children_resp = self.client.get("/audience-tags/?limit=2000&seed_if_empty=true&has_parent=true")
        self.assertEqual(children_resp.status_code, 200)
        child_tags = [item["tag_name"] for item in children_resp.json()]
        self.assertEqual(len(child_tags), 2)
        self.assertNotIn("demographic", child_tags)
        self.assertIn("demographic/age-range", child_tags)
        self.assertIn("demographic/age-range/18-20", child_tags)

    def test_seed_default_endpoint_loads_real_taxonomy_file(self):
        response = self.client.post("/audience-tags/seed-default")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_rows_parsed"], 1558)
        self.assertEqual(body["upserted_count"], 1558)

    def test_assign_audience_tags_to_segment_endpoint(self):
        segment_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        fake_segment = SimpleNamespace(
            segment_id=segment_id,
            tenant_id=tenant_id,
            user_id=None,
            domain="retail",
            segment_tag="gen_z_buyers",
            audience_tags=["demographic/age-range/18-20"],
            segment_name="Gen Z Buyers",
            description="Gen Z segment mapped to IAB taxonomy",
            json_rules={},
            sql_rules="age < 25",
            final_generated_sql=None,
            processed_by="human",
            is_active=True,
            member_count=12,
            last_computed_at=None,
            status_code=1,
            created_at=None,
            updated_at=None,
        )

        class _FakeSegRepo:
            def __init__(self, _db: Any) -> None:
                pass

            def get_segment(self, sid: uuid.UUID):
                return fake_segment if sid == segment_id else None

            def assign_audience_tags(
                self,
                sid: uuid.UUID,
                audience_tags: list[str],
                *,
                recompute_profiles: bool = True,
            ):
                fake_segment.audience_tags = audience_tags
                return fake_segment

        with patch("core.routers.segment_api.SegmentRepository", _FakeSegRepo):
            resp = self.client.post(
                f"/segments/{segment_id}/audience-tags",
                json={
                    "audience_tags": ["demographic/age-range/18-20", "interest/shopping"],
                    "recompute_profiles": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["audience_tags"],
            ["demographic/age-range/18-20", "interest/shopping"],
        )


if __name__ == "__main__":
    unittest.main()
