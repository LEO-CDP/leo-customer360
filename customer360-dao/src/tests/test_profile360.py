"""Focused tests for Customer 360 profile timeline aggregation."""

import uuid

import pytest

from leo_customer360_dao.crud import profile360
from leo_customer360_dao.repositories.event_query_repository import EventDataSourceError


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return []


class _TimelineSession:
    def __init__(self, tenant_id, source_id=None):
        self.tenant_id = tenant_id
        self.source_id = source_id
        self.calls = 0

    def execute(self, _statement, _params=None):
        self.calls += 1
        return _Result(self.tenant_id if self.calls == 1 else self.source_id)


def test_timeline_reads_projection_with_source_and_time_range(monkeypatch):
    master_profile_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    data_source_id = uuid.uuid4()
    raw_profile_id = uuid.uuid4()
    calls = {}

    class _Store:
        def __init__(self, _settings):
            pass

        def query(self, tenant, profile, **kwargs):
            calls.update(tenant=tenant, profile=profile, kwargs=kwargs)
            return [
                {
                    "event_id": "evt-page-view-1",
                    "event_name": "page-view",
                    "event_category": "GENERAL",
                    "event_time": "2026-09-20T10:00:00Z",
                    "data_source_id": str(data_source_id),
                    "raw_profile_id": str(raw_profile_id),
                    "source_system": "web_sdk",
                    "domain": "retail",
                    "device_type": "desktop",
                    "payload": {
                        "channel": "web",
                        "page_url": "https%3A%2F%2Fexample.test%2Fhome",
                        "page_title": "Home",
                        "referrer_url": "https://example.test/",
                        "event_data": {
                            "target": "article-link",
                            "nested": {"should_not_be_returned": True},
                        },
                    },
                }
            ]

    monkeypatch.setattr(profile360, "MasterProfileEventStore", _Store)
    session = _TimelineSession(tenant_id, data_source_id)

    result = profile360.get_timeline(
        session,
        master_profile_id,
        limit=8,
        data_source_id=data_source_id,
    )

    assert calls["tenant"] == tenant_id
    assert calls["profile"] == master_profile_id
    assert calls["kwargs"]["data_source_id"] == data_source_id
    assert result[0]["kind"] == "event"
    assert result[0]["channel"] == "web"
    assert result[0]["event_id"] == "evt-page-view-1"
    assert result[0]["source_system"] == "web_sdk"
    assert result[0]["domain"] == "retail"
    assert result[0]["device_type"] == "desktop"
    assert result[0]["page_title"] == "Home"
    assert result[0]["event_data"] == {"target": "article-link"}


def test_timeline_rejects_inactive_or_cross_tenant_source():
    master_profile_id = uuid.uuid4()
    session = _TimelineSession(uuid.uuid4(), None)

    with pytest.raises(EventDataSourceError):
        profile360.get_timeline(
            session,
            master_profile_id,
            data_source_id=uuid.uuid4(),
        )


def test_profile_event_rows_forwards_data_source_filter(monkeypatch):
    data_source_id = uuid.uuid4()
    calls = {}

    class _Repository:
        def __init__(self, _settings):
            pass

        def query(self, _db, _tenant_id, **kwargs):
            calls.update(kwargs)
            return []

    monkeypatch.setattr(profile360, "EventQueryRepository", _Repository)
    profile360._profile_event_rows(
        _TimelineSession(uuid.uuid4()),
        uuid.uuid4(),
        days=30,
        limit=8,
        data_source_id=data_source_id,
    )

    assert calls["data_source_id"] == data_source_id
