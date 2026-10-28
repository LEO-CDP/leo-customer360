"""Unit tests for the /ad-personalization/erasure-requests endpoints.

The real repository is replaced with an in-memory fake and Redis with a fake
client, so these cover HTTP wiring only (roles, validation, status progression,
tenant handling, what is and is not stored). SQL behaviour is covered by the
opt-in tests in test_ad_personalization_repository.py.
"""

import fnmatch
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.ads_database import get_ads_db
from core.repositories.ad_personalization_repository import AdTenantNotFoundError
from core.routers.ad_personalization_api import router
from core.utils.ad_visitor_key import visitor_key

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
UNMAPPED = "33333333-3333-3333-3333-333333333333"
USER_ID = "44444444-4444-4444-4444-444444444444"
VISITOR = "0f8f2e0a-7a5c-4a7d-9c43-2f1f1c0d9b11"
URL = "/ad-personalization/erasure-requests"


class FakeRepository:
    tenants: dict[str, int] = {}
    requests: dict[int, dict[str, Any]] = {}
    profiles: dict[tuple[int, str], list[bytes]] = {}
    next_id = 1

    @classmethod
    def reset(cls) -> None:
        cls.tenants = {TENANT_A: 1, TENANT_B: 2}
        cls.requests = {}
        cls.profiles = {}
        cls.next_id = 1

    def __init__(self, session) -> None:
        self.session = session

    def resolve_ad_tenant_id(self, platform_tenant_id) -> int:
        try:
            return self.tenants[str(platform_tenant_id)]
        except KeyError:
            raise AdTenantNotFoundError("This tenant has no linked ad tenant")

    def profile_keys(self, ad_tenant_id, profile_ref):
        return list(self.profiles.get((ad_tenant_id, profile_ref), []))

    def create_erasure_request(self, ad_tenant_id, user_key, *, urgent, requested_by):
        erasure_id = FakeRepository.next_id
        FakeRepository.next_id += 1
        self.requests[erasure_id] = {
            "tenant_id": ad_tenant_id,
            "user_key_hash": bytes(user_key),
            "requested_by": requested_by,
            "erasure_id": erasure_id,
            "status": "pending",
            "urgent": urgent,
            "requested_at": datetime(2026, 10, 18, 9, 40, tzinfo=timezone.utc),
            "completed_at": None,
            "deleted_counts": None,
        }
        return self._public(self.requests[erasure_id])

    def mark_redis_purged(self, ad_tenant_id, erasure_id):
        row = self.requests.get(erasure_id)
        if row and row["tenant_id"] == ad_tenant_id and row["status"] == "pending":
            row["status"] = "redis_purged"
        return self.get_erasure_request(ad_tenant_id, erasure_id)

    def get_erasure_request(self, ad_tenant_id, erasure_id):
        row = self.requests.get(erasure_id)
        if row is None or row["tenant_id"] != ad_tenant_id:
            return None
        return self._public(row)

    @staticmethod
    def _public(row):
        return {k: row[k] for k in ("erasure_id", "status", "urgent", "requested_at", "completed_at", "deleted_counts")}


class FakeRedis:
    def __init__(self, keys=(), fail=False):
        self.keys = set(keys)
        self.fail = fail

    def scan_iter(self, match=None, count=None):
        if self.fail:
            raise ConnectionError("redis is down")
        return iter(sorted(k for k in self.keys if fnmatch.fnmatchcase(k, match)))

    def delete(self, *keys):
        self.keys -= set(keys)
        return len(keys)


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject_identity(request: Request, call_next):
        request.state.tenant_id = request.headers.get("X-Tenant-Id", TENANT_A)
        request.state.user_id = request.headers.get("X-User-Id", USER_ID)
        roles = request.headers.get("X-Roles", "tenant_admin")
        request.state.user = {"roles": [r.strip() for r in roles.split(",") if r.strip()]}
        return await call_next(request)

    app.dependency_overrides[get_ads_db] = lambda: None
    return app


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("AD_VISITOR_KEY_MASTER_SECRET", "erasure-test-secret")


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def client(redis):
    FakeRepository.reset()
    with patch("core.routers.ad_personalization_api.AdPersonalizationRepository", FakeRepository), patch(
        "core.routers.ad_personalization_api.ad_hot_state.get_hot_state_client", lambda: redis
    ):
        yield TestClient(_build_test_app())


def _key(tenant_id=1, leovid=VISITOR):
    return visitor_key(tenant_id, leovid)


def _redis_keys(tenant_id=1, leovid=VISITOR, what=("signals", "counter:shown:1:20261018")):
    return {f"c360ads:{tenant_id}:visitor:{_key(tenant_id, leovid).hex()}:{w}" for w in what}


# --------------------------------------------------------------------------
# Who may ask, and what must be supplied
# --------------------------------------------------------------------------


def test_a_non_admin_gets_403_and_nothing_is_recorded(client):
    with patch("core.auth.SSO_LOGIN", True):
        response = client.post(URL, json={"leovid": VISITOR}, headers={"X-Roles": "analyst"})
    assert response.status_code == 403
    assert FakeRepository.requests == {}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"urgent": True},
        {"leovid": VISITOR, "profile_ref": "master-1"},
        {"leovid": ""},
        {"leovid": "x" * 129},
        {"profile_ref": ""},
    ],
)
def test_neither_both_or_unusable_subjects_are_422(client, body):
    assert client.post(URL, json=body).status_code == 422
    assert FakeRepository.requests == {}


def test_an_unknown_profile_reference_is_422(client):
    response = client.post(URL, json={"profile_ref": "no-such-profile"})
    assert response.status_code == 422
    assert FakeRepository.requests == {}


def test_an_unmapped_tenant_gets_404(client):
    assert client.post(URL, json={"leovid": VISITOR}, headers={"X-Tenant-Id": UNMAPPED}).status_code == 404


def test_without_a_key_secret_outside_development_it_is_a_503(client, monkeypatch):
    monkeypatch.delenv("AD_VISITOR_KEY_MASTER_SECRET")
    monkeypatch.delenv("c360_PROMOTION_VISITOR_KEY_MASTER_SECRET", raising=False)
    monkeypatch.setenv("c360_PROMOTION_ENVIRONMENT", "production")

    assert client.post(URL, json={"leovid": VISITOR}).status_code == 503
    assert FakeRepository.requests == {}


# --------------------------------------------------------------------------
# Success path
# --------------------------------------------------------------------------


def test_a_request_is_accepted_purges_redis_and_ends_redis_purged(client, redis):
    redis.keys = _redis_keys() | _redis_keys(leovid="someone-else")

    response = client.post(URL, json={"leovid": VISITOR, "urgent": True})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "redis_purged"
    assert body["redis_purged"] is True
    assert body["urgent"] is True
    assert body["completed_at"] is None
    assert body["deleted_counts"] is None
    assert redis.keys == _redis_keys(leovid="someone-else")  # only this visitor's keys went


def test_the_audit_row_holds_only_the_hashed_key(client):
    response = client.post(URL, json={"leovid": VISITOR})

    row = FakeRepository.requests[response.json()["erasure_id"]]
    assert row["user_key_hash"] == _key()
    assert row["requested_by"] == USER_ID
    assert VISITOR not in repr(row)
    assert VISITOR not in response.text


def test_the_default_is_not_urgent(client):
    assert client.post(URL, json={"leovid": VISITOR}).json()["urgent"] is False


def test_a_visitor_with_no_stored_data_is_still_accepted(client):
    response = client.post(URL, json={"leovid": "never-seen-before"})
    assert response.status_code == 202
    assert response.json()["status"] == "redis_purged"


def test_a_profile_reference_covers_every_linked_key(client, redis):
    first, second = _key(1, "device-1"), _key(1, "device-2")
    FakeRepository.profiles[(1, "master-1")] = [first, second]
    redis.keys = _redis_keys(leovid="device-1") | _redis_keys(leovid="device-2")

    response = client.post(URL, json={"profile_ref": "master-1"})

    assert response.status_code == 202
    assert len(FakeRepository.requests) == 2
    assert {r["user_key_hash"] for r in FakeRepository.requests.values()} == {first, second}
    assert all(r["status"] == "redis_purged" for r in FakeRepository.requests.values())
    assert redis.keys == set()


def test_a_profile_reference_of_another_tenant_is_unknown(client):
    FakeRepository.profiles[(2, "their-master")] = [_key(2, "x")]
    assert client.post(URL, json={"profile_ref": "their-master"}).status_code == 422


# --------------------------------------------------------------------------
# Redis trouble never loses the request
# --------------------------------------------------------------------------


def test_a_redis_failure_leaves_the_request_pending_and_says_so(client, redis):
    redis.fail = True

    response = client.post(URL, json={"leovid": VISITOR})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["redis_purged"] is False
    assert len(FakeRepository.requests) == 1  # the audit row exists; the job will delete the stored data


def test_no_redis_configured_also_leaves_it_pending(client):
    with patch("core.routers.ad_personalization_api.ad_hot_state.get_hot_state_client", lambda: None):
        body = client.post(URL, json={"leovid": VISITOR}).json()
    assert (body["status"], body["redis_purged"]) == ("pending", False)


# --------------------------------------------------------------------------
# Reading a request back
# --------------------------------------------------------------------------


def test_get_shows_the_status_as_it_progresses_and_the_counts_once_completed(client):
    erasure_id = client.post(URL, json={"leovid": VISITOR}).json()["erasure_id"]
    assert client.get(f"{URL}/{erasure_id}").json()["status"] == "redis_purged"

    row = FakeRepository.requests[erasure_id]
    row.update(
        status="completed",
        completed_at=datetime(2026, 10, 18, 11, 2, tzinfo=timezone.utc),
        deleted_counts={"ad_event": 318, "visitor_behaviour_profile": 1, "consent_record": 1, "consent_history": 2},
    )

    body = client.get(f"{URL}/{erasure_id}").json()
    assert body["status"] == "completed"
    assert body["completed_at"].startswith("2026-10-18T11:02")
    assert body["deleted_counts"]["ad_event"] == 318
    assert body["redis_purged"] is True


def test_another_tenants_request_looks_like_an_unknown_one(client):
    erasure_id = client.post(URL, json={"leovid": VISITOR}).json()["erasure_id"]

    assert client.get(f"{URL}/{erasure_id}", headers={"X-Tenant-Id": TENANT_B}).status_code == 404
    assert client.get(f"{URL}/99999").status_code == 404


def test_a_non_admin_may_read_a_request(client):
    erasure_id = client.post(URL, json={"leovid": VISITOR}).json()["erasure_id"]
    with patch("core.auth.SSO_LOGIN", True):
        response = client.get(f"{URL}/{erasure_id}", headers={"X-Roles": "analyst"})
    assert response.status_code == 200


def test_the_erasure_id_must_be_an_integer(client):
    assert client.get(f"{URL}/{uuid.uuid4()}").status_code == 422
