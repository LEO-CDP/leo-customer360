"""
The eligibility kill switch (c360_PROMOTION_ELIGIBILITY_ENFORCED): with it off,
delivery behaves as it did before this feature: no extra eligibility is applied,
whatever the catalogue holds. With it on (the default), ads that fail eligibility
are withheld.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from core.config import PromotionsSettings, promotions_settings
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(), reason="PostgreSQL with leo_ads schema required"
)


@pytest.fixture(autouse=True)
def _tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.consent_record')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


@pytest.fixture
def mixed_catalogue(world_factory):
    """One ad that is fine and three that eligibility would withhold."""
    world = world_factory()
    now = datetime.now(timezone.utc)
    paused = world.add_campaign("cmp_paused", status="paused")
    ended = world.add_campaign("cmp_ended", starts_at=now - timedelta(days=10), ends_at=now - timedelta(days=1))
    future = world.add_campaign("cmp_future", starts_at=now + timedelta(days=5))
    world.add_ad("ad_fine", score_weight=4.0)
    world.add_ad("ad_paused_campaign", score_weight=3.0, campaign_id=paused)
    world.add_ad("ad_ended_campaign", score_weight=2.0, campaign_id=ended)
    world.add_ad("ad_future_campaign", score_weight=1.0, campaign_id=future)
    return world


def _served(client, world):
    response = client.get(f"/serve/{world.placement_key}", params={"tenant": world.tenant_key, "limit": 20})
    return [a["adId"] for a in response.json()["ads"]]


def test_the_default_is_to_enforce_eligibility():
    assert PromotionsSettings.model_fields["eligibility_enforced"].default is True


def test_the_switch_is_read_from_its_documented_environment_variable(monkeypatch):
    monkeypatch.setenv("c360_PROMOTION_ELIGIBILITY_ENFORCED", "false")
    assert PromotionsSettings().eligibility_enforced is False
    monkeypatch.setenv("c360_PROMOTION_ELIGIBILITY_ENFORCED", "true")
    assert PromotionsSettings().eligibility_enforced is True


def test_enforced_withholds_ads_that_fail_eligibility(serve_client, mixed_catalogue):
    assert _served(serve_client, mixed_catalogue) == ["ad_fine"]


def test_switched_off_restores_the_previous_default_behaviour(
    serve_client, serve_app, mixed_catalogue, monkeypatch
):
    previous = [
        ad["adId"]
        for ad in serve_app.ad_repository.get_serving_ads(
            tenant_key=mixed_catalogue.tenant_key, placement_ref=mixed_catalogue.placement_key, limit=20
        )
    ]
    monkeypatch.setattr(promotions_settings, "eligibility_enforced", False)

    served = _served(serve_client, mixed_catalogue)

    assert served == previous
    assert set(served) >= {"ad_fine", "ad_paused_campaign"}  # nothing is withheld by the new rules


def test_switched_off_still_returns_a_request_id_and_outcome(serve_client, mixed_catalogue, monkeypatch):
    monkeypatch.setattr(promotions_settings, "eligibility_enforced", False)

    body = serve_client.get(
        f"/serve/{mixed_catalogue.placement_key}", params={"tenant": mixed_catalogue.tenant_key}
    ).json()

    assert body["request_id"]
    assert body["personalisation"]["applied"] is False


def test_switched_off_does_not_enforce_frequency_caps(serve_client, hot_state, world_factory, monkeypatch):
    from personalization.eligibility import shown_counter_name
    from personalization.identity import visitor_key

    world = world_factory()
    ad_id = world.add_ad("ad_capped", frequency_cap=1)
    key = visitor_key(world.tenant_id, "visitor")
    hot_state.incr_counter(world.tenant_id, key, shown_counter_name(ad_id, datetime.now(timezone.utc)), 3600, amount=5)
    monkeypatch.setattr(promotions_settings, "eligibility_enforced", False)

    response = serve_client.get(
        f"/serve/{world.placement_key}", params={"tenant": world.tenant_key, "leovid": "visitor"}
    )

    assert [a["adId"] for a in response.json()["ads"]] == ["ad_capped"]
