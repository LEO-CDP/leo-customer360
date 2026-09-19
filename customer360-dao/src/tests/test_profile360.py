"""Focused tests for Customer 360 profile timeline aggregation."""

import uuid

from leo_customer360_dao.crud import profile360


class _UnusedSession:
    def execute(self, *_args, **_kwargs):  # pragma: no cover - defensive guard
        raise AssertionError("source-filtered timelines must not query unscoped CRM rows")


class _TenantSession:
    def execute(self, _statement):
        class _Result:
            def scalar_one_or_none(self):
                return uuid.uuid4()

        return _Result()


def test_timeline_passes_data_source_filter_and_excludes_unscoped_crm_rows(monkeypatch):
    master_profile_id = uuid.uuid4()
    data_source_id = uuid.uuid4()
    calls = {}

    def fake_profile_event_rows(_db, profile_id, *, days, limit, data_source_id=None):
        calls.update(
            profile_id=profile_id,
            days=days,
            limit=limit,
            data_source_id=data_source_id,
        )
        return [
            {
                "event_name": "page-view",
                "event_category": "GENERAL",
                "channel": "web",
                "event_value": None,
                "currency": None,
                "event_time": "2026-09-20T10:00:00Z",
            }
        ]

    monkeypatch.setattr(profile360, "_profile_event_rows", fake_profile_event_rows)

    result = profile360.get_timeline(
        _UnusedSession(),
        master_profile_id,
        limit=8,
        data_source_id=data_source_id,
    )

    assert calls == {
        "profile_id": master_profile_id,
        "days": profile360.settings.event_query_max_days,
        "limit": 8,
        "data_source_id": data_source_id,
    }
    assert len(result) == 1
    assert result[0]["kind"] == "event"


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
        _TenantSession(),
        uuid.uuid4(),
        days=30,
        limit=8,
        data_source_id=data_source_id,
    )

    assert calls["data_source_id"] == data_source_id