"""Unit tests for GET /ad-personalization/decisions and /decisions/{request_id}
(core.routers.ad_personalization_api).

The repository is an in-memory fake that returns the same shapes as the real one
(the real shaping helper, ``explain_chosen``, is reused), so these cover HTTP
wiring: status codes, the contract shape, tenant scoping, and that a visitor key
is never returned in full. SQL is covered by test_ad_personalization_repository.py.
"""

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.ads_database import get_ads_db
from core.repositories.ad_personalization_repository import (
    AdTenantNotFoundError,
    explain_chosen,
    fingerprint,
)
from core.routers.ad_personalization_api import router

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
UNMAPPED = "33333333-3333-3333-3333-333333333333"

REQUEST_A = "7f3c2b1e-5d4a-4a7e-9b18-0c6d2f4e8a11"
REQUEST_B = "1a2b3c4d-0000-4000-8000-000000000001"
FULL_KEY = bytes.fromhex("a91f00112233445566778899aabbccdd")
WHEN = datetime(2026, 10, 18, 9, 30, tzinfo=timezone.utc)

ADS = {
    10: {"ad_id": 10, "ad_key": "ad_1001", "objective": "traffic"},
    11: {"ad_id": 11, "ad_key": "ad_1002", "objective": None},
}


class FakeRepository:
    """Decisions per ad tenant; the ad tenant is 1 for TENANT_A and 2 for TENANT_B."""

    tenants = {TENANT_A: 1, TENANT_B: 2}
    decisions: dict[int, dict[str, dict[str, Any]]] = {}
    calls: list[dict[str, Any]] = []

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.decisions = {
            1: {
                REQUEST_A: {
                    "request_id": REQUEST_A,
                    "decided_at": WHEN,
                    "group_code": "personalised",
                    "placement_key": "homepage_top",
                    "user_key_hash": FULL_KEY,
                    "candidate_count": 7,
                    "chosen": [
                        {
                            "ad_id": 10,
                            "rank": 1,
                            "score": 1.84,
                            "signals": [
                                {"name": "objective_rate_lift", "contribution": 1.3, "note": "click rate 2.6%"},
                                {"name": "visitor_affinity", "contribution": 1.25, "note": "clicked 3 ads"},
                                {"name": "made_up_signal", "contribution": 9.0, "note": "must not be shown"},
                            ],
                        },
                        {"ad_id": 11, "rank": 2, "score": 1.1, "signals": []},
                    ],
                },
                REQUEST_B: {
                    "request_id": REQUEST_B,
                    "decided_at": WHEN,
                    "group_code": "holdout",
                    "placement_key": None,
                    "user_key_hash": None,
                    "candidate_count": 2,
                    "chosen": [{"ad_id": 10, "rank": 2, "score": 1.0, "signals": []}],
                },
            },
            2: {},
        }

    def __init__(self, session):
        pass

    def resolve_ad_tenant_id(self, platform_tenant_id) -> int:
        try:
            return self.tenants[str(platform_tenant_id)]
        except KeyError:
            raise AdTenantNotFoundError("This tenant has no linked ad tenant")

    def get_decision(self, ad_tenant_id, request_id):
        stored = self.decisions[ad_tenant_id].get(str(request_id))
        if stored is None:
            return None
        return {
            "request_id": stored["request_id"],
            "decided_at": stored["decided_at"],
            "placement_key": stored["placement_key"],
            "group": stored["group_code"],
            "visitor_fingerprint": fingerprint(stored["user_key_hash"]),
            "candidate_count": stored["candidate_count"],
            "chosen": explain_chosen(stored["chosen"], ADS),
        }

    def list_decisions(self, ad_tenant_id, ad_key, *, date_from=None, date_to=None, group=None, limit=25):
        type(self).calls.append(
            {"tenant": ad_tenant_id, "ad_key": ad_key, "from": date_from, "to": date_to, "group": group, "limit": limit}
        )
        wanted = next((a["ad_id"] for a in ADS.values() if a["ad_key"] == ad_key), None)
        out = []
        for stored in self.decisions[ad_tenant_id].values():
            for entry in stored["chosen"]:
                if entry["ad_id"] == wanted and (group is None or stored["group_code"] == group):
                    out.append(
                        {
                            "request_id": stored["request_id"],
                            "decided_at": stored["decided_at"],
                            "group": stored["group_code"],
                            "rank": entry["rank"],
                        }
                    )
        return out[:limit]


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.tenant_id = request.headers.get("X-Tenant-Id", TENANT_A)
        request.state.user_id = "44444444-4444-4444-4444-444444444444"
        request.state.user = {"roles": [r for r in request.headers.get("X-Roles", "analyst").split(",") if r]}
        return await call_next(request)

    app.dependency_overrides[get_ads_db] = lambda: None
    return app


@pytest.fixture
def client():
    FakeRepository.reset()
    with patch("core.routers.ad_personalization_api.AdPersonalizationRepository", FakeRepository):
        yield TestClient(_app())


# --------------------------------------------------------------------------
# One decision
# --------------------------------------------------------------------------


def test_a_decision_is_explained_in_the_contract_shape(client):
    response = client.get(f"/ad-personalization/decisions/{REQUEST_A}")

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == REQUEST_A
    assert body["group"] == "personalised"
    assert body["placement_key"] == "homepage_top"
    assert body["candidate_count"] == 7
    first = body["chosen"][0]
    assert (first["rank"], first["ad_key"], first["objective"], first["score"]) == (1, "ad_1001", "traffic", 1.84)
    assert [s["name"] for s in first["signals"]] == ["objective_rate_lift", "visitor_affinity"]
    assert first["signals"][0] == {"name": "objective_rate_lift", "contribution": 1.3, "note": "click rate 2.6%"}
    assert body["chosen"][1]["objective"] is None


def test_the_visitor_key_is_only_ever_a_short_fingerprint(client):
    body = client.get(f"/ad-personalization/decisions/{REQUEST_A}").json()

    assert body["visitor_fingerprint"] == "a91f0011"
    assert FULL_KEY.hex() not in client.get(f"/ad-personalization/decisions/{REQUEST_A}").text


def test_a_decision_without_a_visitor_key_has_no_fingerprint(client):
    body = client.get(f"/ad-personalization/decisions/{REQUEST_B}").json()
    assert body["visitor_fingerprint"] is None
    assert body["group"] == "holdout"


def test_an_unknown_request_id_is_404(client):
    assert client.get(f"/ad-personalization/decisions/{uuid.uuid4()}").status_code == 404


def test_another_tenants_decision_is_404_not_forbidden(client):
    assert client.get(f"/ad-personalization/decisions/{REQUEST_A}", headers={"X-Tenant-Id": TENANT_B}).status_code == 404


def test_a_request_id_that_is_not_a_uuid_is_rejected(client):
    assert client.get("/ad-personalization/decisions/not-a-uuid").status_code == 422


def test_an_unmapped_tenant_is_404(client):
    response = client.get(f"/ad-personalization/decisions/{REQUEST_A}", headers={"X-Tenant-Id": UNMAPPED})
    assert response.status_code == 404


def test_explanations_are_open_to_any_authenticated_role(client):
    with patch("core.auth.SSO_LOGIN", True):
        response = client.get(f"/ad-personalization/decisions/{REQUEST_A}", headers={"X-Roles": "marketer"})
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def test_decisions_that_served_an_ad_are_listed_with_its_rank(client):
    response = client.get("/ad-personalization/decisions", params={"ad_key": "ad_1001"})

    assert response.status_code == 200
    ranks = {d["request_id"]: d["rank"] for d in response.json()["decisions"]}
    assert ranks == {REQUEST_A: 1, REQUEST_B: 2}


def test_the_group_filter_is_applied(client):
    response = client.get("/ad-personalization/decisions", params={"ad_key": "ad_1001", "group": "holdout"})
    assert [d["request_id"] for d in response.json()["decisions"]] == [REQUEST_B]


def test_an_unknown_group_is_rejected(client):
    assert client.get("/ad-personalization/decisions", params={"ad_key": "ad_1001", "group": "vip"}).status_code == 422


def test_ad_key_is_required_and_limit_is_bounded(client):
    assert client.get("/ad-personalization/decisions").status_code == 422
    assert client.get("/ad-personalization/decisions", params={"ad_key": "x", "limit": 0}).status_code == 422
    assert client.get("/ad-personalization/decisions", params={"ad_key": "x", "limit": 101}).status_code == 422


def test_dates_without_a_zone_are_read_as_utc_and_a_reversed_range_is_422(client):
    ok = client.get(
        "/ad-personalization/decisions",
        params={"ad_key": "ad_1001", "from": "2026-10-01T00:00:00", "to": "2026-10-31T00:00:00Z"},
    )
    assert ok.status_code == 200
    seen = FakeRepository.calls[-1]
    assert seen["from"].tzinfo is not None and seen["to"].tzinfo is not None

    reversed_range = client.get(
        "/ad-personalization/decisions",
        params={"ad_key": "ad_1001", "from": "2026-11-01T00:00:00Z", "to": "2026-10-01T00:00:00Z"},
    )
    assert reversed_range.status_code == 422


def test_listing_never_crosses_tenants(client):
    response = client.get("/ad-personalization/decisions", params={"ad_key": "ad_1001"}, headers={"X-Tenant-Id": TENANT_B})
    assert response.json() == {"decisions": []}
    assert FakeRepository.calls[-1]["tenant"] == 2


def test_a_tenant_sent_as_a_parameter_is_ignored(client):
    response = client.get(
        "/ad-personalization/decisions",
        params={"ad_key": "ad_1001", "tenant": TENANT_A},
        headers={"X-Tenant-Id": TENANT_B},
    )
    assert response.json() == {"decisions": []}
