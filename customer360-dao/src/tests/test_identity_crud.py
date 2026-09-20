"""Unit tests for identity CRUD query filtering and raw-profile writes."""

import uuid

from leo_customer360_dao.crud.identity import list_master_profiles_page
from leo_customer360_dao.repositories.identity_repository import IdentityRepository


class _Rows:
    def all(self):
        return []

    def scalar_one(self):
        return 0


class _Session:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append(statement)
        return _Rows()


class _RepositorySession:
    def __init__(self, tenant_id):
        self.info = {"tenant_id": str(tenant_id)}
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _Rows()


def test_master_profiles_page_supports_days_filter():
    result = list_master_profiles_page(_Session(), days=30, page=1, page_size=25)

    assert result["items"] == []
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["total_pages"] == 1


def test_master_profiles_page_supports_data_source_filter():
    session = _Session()
    data_source_id = uuid.uuid4()
    result = list_master_profiles_page(
        session,
        tenant_id=uuid.uuid4(),
        data_source_id=data_source_id,
        page=1,
        page_size=25,
    )

    assert result["items"] == []
    assert result["pagination"]["total"] == 0
    rendered_sql = "\n".join(str(statement) for statement in session.statements)
    assert "cdp_raw_profiles_stage" in rendered_sql
    assert "data_source_id" in rendered_sql


def test_identity_repository_upserts_raw_profile_with_tenant_scope():
    tenant_id = uuid.uuid4()
    raw_profile_id = uuid.uuid4()
    session = _RepositorySession(tenant_id)

    result = IdentityRepository(session).upsert_raw_profile(
        {
            "raw_profile_id": raw_profile_id,
            "tenant_id": tenant_id,
            "domain": "retail",
            "source_system": "web",
            "data_source_analytics": {
                str(uuid.uuid4()): {
                    "page_views": 1,
                    "clicks": 1,
                    "click_through_rate": 1.0,
                    "total_tracked_events": 1,
                }
            },
            "email": "customer@example.test",
            "event_name": "page_view",
            "event_time": "2026-09-18T12:00:00Z",
            "event_payload": {"page": "/home"},
        }
    )

    assert result == 0
    rendered_statement = str(session.statement)
    assert "cdp_raw_profiles_stage" in rendered_statement
    assert "ON CONFLICT" in rendered_statement
    assert "status_code" in rendered_statement
    assert "data_source_analytics" in rendered_statement


def test_identity_repository_rejects_a_cross_tenant_raw_profile():
    session = _RepositorySession(uuid.uuid4())

    try:
        IdentityRepository(session).upsert_raw_profile(
            {
                "raw_profile_id": uuid.uuid4(),
                "tenant_id": uuid.uuid4(),
                "domain": "retail",
                "source_system": "web",
                "event_time": "2026-09-18T12:00:00Z",
            }
        )
    except ValueError as exc:
        assert str(exc) == "Raw profile tenant does not match the session tenant"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("cross-tenant raw profile was accepted")


def test_total_tracked_events_reads_persisted_source_analytics():
    from leo_customer360_dao.crud.identity import _total_tracked_events

    source_id = uuid.uuid4()
    analytics = {
        str(source_id): {"total_tracked_events": 7},
        str(uuid.uuid4()): {"total_tracked_events": 3},
    }

    assert _total_tracked_events(analytics) == 10
    assert _total_tracked_events(analytics, source_id) == 7