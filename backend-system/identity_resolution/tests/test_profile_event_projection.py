"""Tests for master-profile S3 event projection."""

import gzip
import json
from io import BytesIO
from uuid import UUID

from identity_resolution.profile_event_projection import MasterProfileEventProjector


TENANT_ID = "11111111-1111-1111-1111-111111111111"
MASTER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SOURCE_ID = "22222222-2222-2222-2222-222222222222"


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _query, _params):
        return None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self, **_kwargs):
        return _Cursor(self.rows)


class _S3:
    def get_paginator(self, _name):
        class _Paginator:
            def paginate(self, **_kwargs):
                return [{"Contents": [{"Key": "events/2026-09-20-05/events.jsonl.gz"}]}]

        return _Paginator()

    def get_object(self, **_kwargs):
        events = [
            {
                "event_id": "old",
                "data_source_id": SOURCE_ID,
                "event_time": "2026-09-20T05:29:18Z",
                "identity": {"anonymous_id": "anon-1"},
                "payload": {"event_name": "page-view", "anonymous_id": "anon-1"},
            },
            {
                "event_id": "new",
                "data_source_id": SOURCE_ID,
                "event_time": "2026-09-20T05:30:18Z",
                "identity": {"anonymous_id": "anon-1"},
                "payload": {"event_name": "click", "anonymous_id": "anon-1"},
            },
        ]
        body = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
        return {"Body": BytesIO(gzip.compress(body))}


class _Store:
    def __init__(self):
        self.s3 = _S3()
        self.writes = []

    def put_events(self, tenant_id, master_profile_id, events):
        self.writes.append((tenant_id, master_profile_id, events))


def test_projector_merges_raw_events_and_orders_latest_first():
    store = _Store()
    connection = _Connection(
        [
            {
                "raw_profile_id": "raw-1",
                "master_profile_id": MASTER_ID,
                "data_source_id": SOURCE_ID,
                "external_customer_id": "anon-1",
                "email": None,
                "phone_number": None,
                "device_id": None,
                "advertising_id": None,
                "cookie_id": None,
                "session_id": None,
                "event_payload": {},
            }
        ]
    )

    MasterProfileEventProjector(connection, store=store).project_profiles(
        TENANT_ID, {MASTER_ID}
    )

    assert len(store.writes) == 1
    tenant_id, master_profile_id, events = store.writes[0]
    assert tenant_id == UUID(TENANT_ID)
    assert master_profile_id == UUID(MASTER_ID)
    assert [event["event_id"] for event in events] == ["new", "old"]
    assert all(event["master_profile_id"] == MASTER_ID for event in events)
