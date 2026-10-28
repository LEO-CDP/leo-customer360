"""Tests for GET /ad-personalization/reports/comparison.

The rate, lift and small-sample rules live in the pure ``build_comparison``, so
they are tested directly; the endpoint is then tested with a fake repository
that feeds statistics rows through that same function. SQL is covered by
test_ad_personalization_repository.py (opt-in).
"""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.ads_database import get_ads_db
from core.repositories.ad_personalization_repository import (
    AdTenantNotFoundError,
    build_comparison,
)
from core.routers.ad_personalization_api import router

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
UNMAPPED = "33333333-3333-3333-3333-333333333333"
START = datetime(2026, 9, 20, tzinfo=timezone.utc)
END = datetime(2026, 10, 18, tzinfo=timezone.utc)


def stat(group, objective, impressions, views=0, clicks=0, conversions=0):
    return {
        "group_code": group,
        "objective": objective,
        "impressions": impressions,
        "views": views,
        "clicks": clicks,
        "conversions": conversions,
    }


def objective(report, name):
    return next(o for o in report["by_objective"] if o["objective"] == name)


# --------------------------------------------------------------------------
# build_comparison (pure)
# --------------------------------------------------------------------------


def test_each_objective_is_judged_on_its_own_metric():
    rows = [
        stat("personalised", "awareness", 10_000, views=6_000, clicks=100, conversions=5),
        stat("holdout", "awareness", 10_000, views=5_000, clicks=100, conversions=5),
        stat("personalised", "traffic", 10_000, views=6_000, clicks=300, conversions=5),
        stat("holdout", "traffic", 10_000, views=6_000, clicks=200, conversions=5),
        stat("personalised", "conversions", 10_000, views=6_000, clicks=300, conversions=60),
        stat("holdout", "conversions", 10_000, views=6_000, clicks=300, conversions=40),
    ]

    report = build_comparison(rows, START, END, None, min_sample=1000)

    awareness = objective(report, "awareness")
    assert (awareness["metric"], awareness["personalised_rate"], awareness["holdout_rate"]) == ("view_rate", 0.6, 0.5)
    assert awareness["lift"] == pytest.approx(0.2)
    traffic = objective(report, "traffic")
    assert (traffic["metric"], traffic["personalised_rate"], traffic["holdout_rate"]) == ("click_through_rate", 0.03, 0.02)
    assert traffic["lift"] == pytest.approx(0.5)
    conversions = objective(report, "conversions")
    assert (conversions["metric"], conversions["lift"]) == ("conversion_rate", pytest.approx(0.5))
    assert not any(o["insufficient_data"] for o in report["by_objective"])


@pytest.mark.parametrize(
    "name, metric",
    [
        ("reach", "view_rate"),
        ("conversion", "conversion_rate"),
        ("retargeting", "click_through_rate"),
        ("unspecified", "click_through_rate"),
    ],
)
def test_aliases_and_unknown_objectives_map_to_a_metric(name, metric):
    rows = [stat("personalised", name, 5_000, 1, 1, 1), stat("holdout", name, 5_000, 1, 1, 1)]
    assert objective(build_comparison(rows, START, END, None), name)["metric"] == metric


def test_a_small_group_reports_insufficient_data_not_a_rate():
    rows = [
        stat("personalised", "traffic", 50_000, clicks=1_500),
        stat("holdout", "traffic", 400, clicks=20),  # under the minimum
    ]

    entry = objective(build_comparison(rows, START, END, None, min_sample=1000), "traffic")

    assert entry == {
        "objective": "traffic",
        "metric": "click_through_rate",
        "personalised_rate": None,
        "holdout_rate": None,
        "lift": None,
        "insufficient_data": True,
    }


def test_a_missing_group_is_insufficient_data():
    rows = [stat("personalised", "traffic", 50_000, clicks=1_500)]
    assert objective(build_comparison(rows, START, END, None), "traffic")["insufficient_data"] is True


def test_a_zero_holdout_rate_has_no_lift_but_is_not_insufficient():
    rows = [stat("personalised", "traffic", 5_000, clicks=50), stat("holdout", "traffic", 5_000, clicks=0)]

    entry = objective(build_comparison(rows, START, END, None), "traffic")

    assert entry["lift"] is None
    assert entry["insufficient_data"] is False
    assert entry["holdout_rate"] == 0.0


def test_group_totals_always_list_personalised_holdout_and_default():
    rows = [
        stat("personalised", "traffic", 100, 60, 5, 1),
        stat("personalised", "awareness", 50, 30, 1, 0),
        stat("default", "traffic", 7, 7, 7, 7),
        stat("surprise", "traffic", 999, 999, 999, 999),  # unknown group is ignored
    ]

    groups = {g["group"]: g for g in build_comparison(rows, START, END, "homepage_top")["groups"]}

    assert list(groups) == ["personalised", "holdout", "default"]
    assert (groups["personalised"]["impressions"], groups["personalised"]["views"]) == (150, 90)
    assert groups["holdout"]["impressions"] == 0
    assert groups["default"]["clicks"] == 7


def test_a_report_with_no_data_is_empty_but_well_formed():
    report = build_comparison([], START, END, None)
    assert report["by_objective"] == []
    assert all(g["impressions"] == 0 for g in report["groups"])


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


class FakeRepository:
    tenants = {TENANT_A: 1, TENANT_B: 2}
    rows: dict[int, list[dict[str, Any]]] = {}
    calls: list[dict[str, Any]] = []

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.rows = {
            1: [
                stat("personalised", "traffic", 20_000, views=10_000, clicks=520),
                stat("holdout", "traffic", 2_000, views=1_000, clicks=40),
            ],
            2: [stat("personalised", "traffic", 3_000, clicks=3_000)],
        }

    def __init__(self, session):
        pass

    def resolve_ad_tenant_id(self, platform_tenant_id) -> int:
        try:
            return self.tenants[str(platform_tenant_id)]
        except KeyError:
            raise AdTenantNotFoundError("This tenant has no linked ad tenant")

    def comparison_report(self, ad_tenant_id, date_from, date_to, *, placement_key=None, min_sample=1000):
        type(self).calls.append(
            {"tenant": ad_tenant_id, "from": date_from, "to": date_to, "placement_key": placement_key}
        )
        return build_comparison(self.rows[ad_tenant_id], date_from, date_to, placement_key, min_sample)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.tenant_id = request.headers.get("X-Tenant-Id", TENANT_A)
        request.state.user_id = "44444444-4444-4444-4444-444444444444"
        request.state.user = {"roles": ["analyst"]}
        return await call_next(request)

    app.dependency_overrides[get_ads_db] = lambda: None
    return app


@pytest.fixture
def client():
    FakeRepository.reset()
    with patch("core.routers.ad_personalization_api.AdPersonalizationRepository", FakeRepository):
        yield TestClient(_app())


PARAMS = {"from": "2026-09-20T00:00:00Z", "to": "2026-10-18T00:00:00Z"}


def test_the_report_has_the_contract_shape(client):
    response = client.get("/ad-personalization/reports/comparison", params=PARAMS)

    assert response.status_code == 200
    body = response.json()
    assert body["from"].startswith("2026-09-20") and body["to"].startswith("2026-10-18")
    assert body["placement_key"] is None
    assert [g["group"] for g in body["groups"]] == ["personalised", "holdout", "default"]
    traffic = body["by_objective"][0]
    assert traffic["metric"] == "click_through_rate"
    assert traffic["personalised_rate"] == pytest.approx(0.026)
    assert traffic["holdout_rate"] == pytest.approx(0.02)
    assert traffic["lift"] == pytest.approx(0.3)
    assert traffic["insufficient_data"] is False


def test_the_placement_filter_is_passed_through(client):
    client.get("/ad-personalization/reports/comparison", params={**PARAMS, "placement_key": "homepage_top"})
    assert FakeRepository.calls[-1]["placement_key"] == "homepage_top"


def test_the_report_only_uses_the_callers_tenant(client):
    body = client.get("/ad-personalization/reports/comparison", params=PARAMS, headers={"X-Tenant-Id": TENANT_B}).json()

    assert FakeRepository.calls[-1]["tenant"] == 2
    assert body["groups"][0]["impressions"] == 3_000
    assert body["by_objective"][0]["insufficient_data"] is True  # tenant B has no hold-out data


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-10-18T00:00:00Z", "to": "2026-09-20T00:00:00Z"},  # reversed
        {"from": "2026-09-20T00:00:00Z", "to": "2026-09-20T00:00:00Z"},  # empty
        {"from": "yesterday", "to": "2026-09-20T00:00:00Z"},
        {"to": "2026-09-20T00:00:00Z"},  # missing from
    ],
)
def test_an_invalid_or_reversed_range_is_422(client, params):
    assert client.get("/ad-personalization/reports/comparison", params=params).status_code == 422


def test_an_unmapped_tenant_is_404(client):
    response = client.get("/ad-personalization/reports/comparison", params=PARAMS, headers={"X-Tenant-Id": UNMAPPED})
    assert response.status_code == 404
