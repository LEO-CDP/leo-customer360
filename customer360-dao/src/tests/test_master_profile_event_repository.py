"""Tests for master-profile S3 event projections."""

from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID

from botocore.exceptions import ClientError
from leo_customer360_dao.config import Settings
from leo_customer360_dao.repositories.master_profile_event_repository import MasterProfileEventStore


MASTER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_ID = UUID("22222222-2222-2222-2222-222222222222")


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_calls = []

    def head_bucket(self, **_kwargs):
        return None

    def create_bucket(self, **_kwargs):
        return None

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, **kwargs):
        if (kwargs["Bucket"], kwargs["Key"]) not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}},
                "GetObject",
            )
        body = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {"Body": BytesIO(body)}


def test_projection_writes_latest_first_and_queries_order_independent_range():
    s3 = FakeS3()
    store = MasterProfileEventStore(Settings(master_profile_s3_bucket="c360-master-profiles"), s3)
    store.put_events(
        TENANT_ID,
        MASTER_ID,
        [
            {"event_id": "old", "data_source_id": str(SOURCE_ID), "event_time": "2026-09-14T05:00:00Z"},
            {"event_id": "new", "data_source_id": str(SOURCE_ID), "event_time": "2026-09-20T05:00:00Z"},
        ],
    )

    rows = store.query(
        TENANT_ID,
        MASTER_ID,
        from_event_time=datetime(2026, 9, 20, 6, tzinfo=timezone.utc),
        to_event_time=datetime(2026, 9, 13, tzinfo=timezone.utc),
        data_source_id=SOURCE_ID,
    )

    assert [row["event_id"] for row in rows] == ["new", "old"]
    assert s3.put_calls[0]["Key"] == f"{MASTER_ID}.json"


def test_projection_returns_empty_when_file_is_missing():
    store = MasterProfileEventStore(Settings(master_profile_s3_bucket="c360-master-profiles"), FakeS3())

    assert store.query(TENANT_ID, MASTER_ID) == []
