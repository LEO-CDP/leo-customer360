import io
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import content_import
from leo_customer360_dao.config import settings
from leo_customer360_dao.schemas.content_import import ContentImportRecord

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OBJECT_KEY = f"content-imports/{TENANT_ID}/file.json"


def make_record(**overrides):
    values = {
        "domain": "retail",
        "item_type": "article",
        "title": "Shopping guide",
        "summary": "A summary",
        "image_url": None,
        "cta_label": "Read",
        "cta_url": "https://example.com/article",
        "segment_tags": ["retail", "guide"],
        "published_at": datetime(2026, 9, 15, tzinfo=timezone.utc),
        "status_code": 1,
    }
    values.update(overrides)
    return ContentImportRecord.model_validate(values)


class FakeCursor:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.executed.append((query, params))

    def fetchall(self):
        return [("retail",)]


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.cursor_instance


def test_load_content_records_validates_bucket_and_tenant_payload(monkeypatch):
    monkeypatch.setattr(settings, "content_import_s3_bucket", "content-imports")
    record = make_record()
    staged_payload = {
        "tenant_id": str(TENANT_ID),
        "records": [record.model_dump(mode="json")],
    }

    class FakeS3:
        def get_object(self, **_kwargs):
            return {"Body": io.BytesIO(json.dumps(staged_payload).encode())}

    loaded = content_import._load_content_records(
        tenant_id=TENANT_ID,
        bucket="content-imports",
        object_key=OBJECT_KEY,
        s3_client=FakeS3(),
    )

    assert loaded == [record]
    with pytest.raises(content_import.ContentImportError, match="outside the requested tenant"):
        content_import._load_content_records(
            tenant_id=TENANT_ID,
            bucket="content-imports",
            object_key="content-imports/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/file.json",
            s3_client=FakeS3(),
        )


def test_persist_content_is_tenant_scoped_and_retry_idempotent(monkeypatch):
    record = make_record()
    connection = FakeConnection()
    batches = []

    def capture_values(cursor, query, rows, **kwargs):
        batches.append((query, list(rows), kwargs))

    monkeypatch.setattr(content_import, "execute_values", capture_values)
    first = content_import.persist_content_records(
        tenant_id=TENANT_ID,
        object_key=OBJECT_KEY,
        records=[record],
        connection_factory=lambda: connection,
    )
    first_row = batches[0][1][0]
    second = content_import.persist_content_records(
        tenant_id=TENANT_ID,
        object_key=OBJECT_KEY,
        records=[record],
        connection_factory=lambda: connection,
    )
    second_row = batches[1][1][0]

    assert first == second == 1
    assert first_row[0] == second_row[0]
    assert isinstance(first_row[0], str)
    assert uuid.UUID(first_row[0])
    assert first_row[1] == str(TENANT_ID)
    assert first_row[2:5] == ("retail", "article", "Shopping guide")
    assert first_row[9] == ["retail", "guide"]
    assert first_row[10] == record.published_at
    assert batches[0][0] == content_import.CONTENT_UPSERT_SQL
    assert "ON CONFLICT (content_item_id)" in batches[0][0]
    assert "SELECT set_config('app.tenant_id', %s, true)" in connection.cursor_instance.executed[0][0]


def test_persist_content_rejects_inactive_domains_before_inserting(monkeypatch):
    record = make_record(domain="unknown")
    connection = FakeConnection()
    connection.cursor_instance.fetchall = lambda: []
    monkeypatch.setattr(
        content_import,
        "execute_values",
        lambda *_args, **_kwargs: pytest.fail("inactive content domain must not be written"),
    )

    with pytest.raises(content_import.ContentImportError, match="unknown content domain"):
        content_import.persist_content_records(
            tenant_id=TENANT_ID,
            object_key=OBJECT_KEY,
            records=[record],
            connection_factory=lambda: connection,
        )


def test_import_content_file_deletes_staging_object_after_success(monkeypatch):
    monkeypatch.setattr(settings, "content_import_s3_bucket", "content-imports")
    record = make_record()
    staged_payload = {
        "tenant_id": str(TENANT_ID),
        "records": [record.model_dump(mode="json")],
    }

    class FakeS3:
        deleted = None

        def get_object(self, **_kwargs):
            return {"Body": io.BytesIO(json.dumps(staged_payload).encode())}

        def delete_object(self, **kwargs):
            self.deleted = kwargs

    s3 = FakeS3()
    with patch.object(content_import, "persist_content_records", return_value=1) as persist:
        result = content_import.import_content_file(
            tenant_id=str(TENANT_ID),
            bucket="content-imports",
            object_key=OBJECT_KEY,
            s3_client=s3,
        )

    assert result == {"tenant_id": str(TENANT_ID), "content_items_imported": 1}
    persist.assert_called_once()
    assert s3.deleted == {"Bucket": "content-imports", "Key": OBJECT_KEY}
