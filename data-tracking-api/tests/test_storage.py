"""Tests for the database-free MinIO RAW/STATE storage flow."""

from datetime import datetime, timezone
from uuid import UUID

from botocore.exceptions import ClientError

from core.storage import S3ObjectStorage


SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_calls = []

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "missing"}},
                "HeadObject",
            )
        return {"Metadata": self.objects[(Bucket, Key)]["metadata"]}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            "body": kwargs["Body"],
            "metadata": kwargs.get("Metadata", {}),
        }


def _storage(client):
    storage = object.__new__(S3ObjectStorage)
    storage.client = client
    storage.auto_create_buckets = False
    storage.schema_version = 1
    storage.ingestion_version = "1.0"
    storage.max_object_size_bytes = 1024 * 1024
    storage.processed_prefix = "_processed"
    storage._known_buckets = {f"data-tracking-{SOURCE_ID}"}
    return storage


def test_processed_marker_prevents_rewriting_raw_object_on_retry():
    client = FakeS3()
    storage = _storage(client)
    body = b'{"schema_version":1}\n'
    received_at = datetime(2026, 9, 15, 14, 22, tzinfo=timezone.utc)

    first = storage.store_prebuilt_tracking_object(
        data_source_id=SOURCE_ID,
        bucket=f"data-tracking-{SOURCE_ID}",
        object_key="events/2026-09-15-14/batch.jsonl.gz",
        body=body,
        event_count=1,
        received_at=received_at,
    )
    second = storage.store_prebuilt_tracking_object(
        data_source_id=SOURCE_ID,
        bucket=first.bucket,
        object_key=first.object_key,
        body=body,
        event_count=1,
        received_at=received_at,
    )

    assert first.object_id == second.object_id
    assert len(client.put_calls) == 2
    assert client.put_calls[0]["Key"].startswith("events/")
    assert client.put_calls[1]["Key"].startswith("_processed/")