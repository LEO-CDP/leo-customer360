"""
GET /serve/{placement_ref}: backward compatibility, the new parameters, the
personalisation outcome and the response contract.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise). The
application runs on the rolled-back test transaction with in-memory hot state.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import text

from personalization.identity import visitor_key
from repository.profile_repository import ProfileRepository
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

VISITOR = "visitor-serve-1"


@pytest.fixture(autouse=True)
def _require_personalisation_tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.consent_record')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


def _serve(client, world, **params):
    return client.get(
        f"/serve/{world.placement_key}", params={"tenant": world.tenant_key, **params}
    )


def _ad_keys(response):
    return [ad["adId"] for ad in response.json()["ads"]]


def _tag(test_engine, ad_id, tag):
    test_engine.execute(
        text("UPDATE leo_ads.ad SET metadata = CAST(:m AS jsonb) WHERE ad_id = :id"),
        {"m": json.dumps({"tags": [tag]}), "id": ad_id},
    )


def _well_measured(test_engine, world, *ad_ids):
    """Tight statistics so exploration noise cannot outweigh a visitor's history."""
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for ad_id in ad_ids:
        test_engine.execute(
            text(
                "INSERT INTO leo_ads.ad_stat_hourly "
                "(tenant_id, ad_id, hour_start, group_code, impressions, views, clicks, conversions) "
                "VALUES (:t, :ad, :h, 'default', 100000, 50000, 2000, 200)"
            ),
            {"t": world.tenant_id, "ad": ad_id, "h": hour},
        )


def _likes(test_engine, world, leovid, tag, score=4.0):
    ProfileRepository(engine=test_engine).upsert_profile(
        world.tenant_id,
        visitor_key(world.tenant_id, leovid),
        affinities={
            "tag": {tag: {"score": score, "n": 5, "last_at": datetime.now(timezone.utc).isoformat()}}
        },
        event_count=5,
    )


# --------------------------------------------------------------------------
# Compatibility and contract
# --------------------------------------------------------------------------


def test_without_a_visitor_the_payload_is_unchanged_plus_request_id_and_outcome(
    serve_client, world_factory, contract_validator
):
    world = world_factory()
    world.add_ad("ad_one", score_weight=2.0)
    world.add_ad("ad_two", score_weight=1.0)

    response = _serve(serve_client, world)

    assert response.status_code == 200
    body = response.json()
    assert _ad_keys(response) == ["ad_one", "ad_two"]
    assert body["personalisation"]["applied"] is False
    UUID(body["request_id"])
    contract_validator("ServeResponse", body)


def test_every_new_parameter_is_accepted(serve_client, world_factory, contract_validator):
    world = world_factory()
    world.add_ad("ad_one")

    response = _serve(
        serve_client, world,
        leovid=VISITOR, session="s-1", site="site-1", consent="unknown", ctx="sports,news", lang="vi", limit=3,
    )

    assert response.status_code == 200
    contract_validator("ServeResponse", response.json())


@pytest.mark.parametrize("limit", [0, 21])
def test_limit_outside_the_contract_range_is_rejected(serve_client, world_factory, limit):
    world = world_factory()
    assert _serve(serve_client, world, limit=limit).status_code == 422


def test_invalid_consent_value_is_rejected(serve_client, world_factory):
    world = world_factory()
    assert _serve(serve_client, world, consent="maybe").status_code == 422


def test_unknown_tenant_returns_an_empty_200(serve_client, world_factory, contract_validator):
    world = world_factory()
    world.add_ad("ad_one")

    response = serve_client.get(f"/serve/{world.placement_key}", params={"tenant": "no-such-tenant"})

    assert response.status_code == 200
    assert response.json()["ads"] == []
    contract_validator("ServeResponse", response.json())


# --------------------------------------------------------------------------
# Personalisation outcome
# --------------------------------------------------------------------------


def test_visitor_with_history_is_ranked_by_their_history_and_the_decision_is_stored(
    serve_client, serve_app, test_engine, world_factory, enable_personalisation, contract_validator
):
    world = world_factory()
    enable_personalisation(world)
    sports = world.add_ad("ad_sports")
    travel = world.add_ad("ad_travel")
    _tag(test_engine, sports, "sports")
    _tag(test_engine, travel, "travel")
    _well_measured(test_engine, world, sports, travel)
    _likes(test_engine, world, VISITOR, "travel")

    response = _serve(serve_client, world, leovid=VISITOR)

    body = response.json()
    assert _ad_keys(response)[0] == "ad_travel"
    assert body["personalisation"] == {"applied": True, "group": "personalised", "reason": None}
    contract_validator("ServeResponse", body)

    serve_app.decision_writer.flush()
    row = test_engine.execute(
        text(
            "SELECT group_code, candidate_count, chosen, user_key_hash FROM leo_ads.serving_decision "
            "WHERE request_id = CAST(:r AS uuid)"
        ),
        {"r": body["request_id"]},
    ).one()
    assert row.group_code == "personalised"
    assert row.candidate_count == 2
    assert row.chosen[0]["ad_id"] == travel
    assert bytes(row.user_key_hash) == visitor_key(world.tenant_id, VISITOR)


def test_two_visitors_with_different_history_see_different_first_ads(
    serve_client, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    enable_personalisation(world)
    sports = world.add_ad("ad_sports")
    travel = world.add_ad("ad_travel")
    _tag(test_engine, sports, "sports")
    _tag(test_engine, travel, "travel")
    _well_measured(test_engine, world, sports, travel)
    _likes(test_engine, world, "fan", "sports")
    _likes(test_engine, world, "traveller", "travel")

    assert _ad_keys(_serve(serve_client, world, leovid="fan"))[0] == "ad_sports"
    assert _ad_keys(_serve(serve_client, world, leovid="traveller"))[0] == "ad_travel"


def test_visitor_with_no_history_gets_default_order(
    serve_client, world_factory, enable_personalisation
):
    world = world_factory()
    enable_personalisation(world)
    world.add_ad("ad_one", score_weight=2.0)
    world.add_ad("ad_two", score_weight=1.0)

    response = _serve(serve_client, world, leovid="brand-new")

    assert _ad_keys(response) == ["ad_one", "ad_two"]
    assert response.json()["personalisation"]["group"] == "default_no_history"


def test_personalisation_is_off_by_default(serve_client, world_factory):
    world = world_factory()
    world.add_ad("ad_one")

    response = _serve(serve_client, world, leovid=VISITOR)

    assert response.json()["personalisation"] == {
        "applied": False, "group": "default_disabled", "reason": "disabled",
    }


def test_denied_consent_is_recorded_and_stops_personalisation(
    serve_client, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    enable_personalisation(world)
    ad = world.add_ad("ad_one")
    _tag(test_engine, ad, "travel")
    _likes(test_engine, world, VISITOR, "travel")

    response = _serve(serve_client, world, leovid=VISITOR, consent="denied")

    assert response.json()["personalisation"]["group"] == "default_no_consent"
    status = test_engine.execute(
        text("SELECT status FROM leo_ads.consent_record WHERE tenant_id = :t AND user_key_hash = :k"),
        {"t": world.tenant_id, "k": visitor_key(world.tenant_id, VISITOR)},
    ).scalar_one()
    assert status == "denied"
    # And it sticks: later requests that report nothing are still not personalised.
    later = _serve(serve_client, world, leovid=VISITOR)
    assert later.json()["personalisation"]["group"] == "default_no_consent"


def test_opt_in_tenant_does_not_personalise_until_consent_is_granted(
    serve_client, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    enable_personalisation(world, consent_mode="opt_in")
    ad = world.add_ad("ad_one")
    _tag(test_engine, ad, "travel")
    _likes(test_engine, world, VISITOR, "travel")

    before = _serve(serve_client, world, leovid=VISITOR)
    after = _serve(serve_client, world, leovid=VISITOR, consent="granted")

    assert before.json()["personalisation"]["group"] == "default_no_consent"
    assert after.json()["personalisation"]["group"] == "personalised"


def test_holdout_everyone_gets_default_order_and_is_logged_as_holdout(
    serve_client, serve_app, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    enable_personalisation(world, holdout_pct=100.0)
    world.add_ad("ad_one", score_weight=2.0)
    world.add_ad("ad_two")

    response = _serve(serve_client, world, leovid=VISITOR)

    assert _ad_keys(response) == ["ad_one", "ad_two"]
    assert response.json()["personalisation"]["group"] == "holdout"
    serve_app.decision_writer.flush()
    group = test_engine.execute(
        text("SELECT group_code FROM leo_ads.serving_decision WHERE request_id = CAST(:r AS uuid)"),
        {"r": response.json()["request_id"]},
    ).scalar_one()
    assert group == "holdout"


def test_ineligible_ads_are_not_served(serve_client, world_factory):
    world = world_factory()
    paused = world.add_campaign("cmp_paused", status="paused")
    world.add_ad("ad_paused", campaign_id=paused)
    expired = world.add_campaign("cmp_expired", ends_at=datetime.now(timezone.utc) - timedelta(days=1))
    world.add_ad("ad_expired", campaign_id=expired)
    world.add_ad("ad_ok")

    assert _ad_keys(_serve(serve_client, world)) == ["ad_ok"]


def test_serving_still_works_when_redis_is_down(
    serve_client, hot_state, world_factory, enable_personalisation
):
    hot_state.available = False
    world = world_factory()
    enable_personalisation(world)
    world.add_ad("ad_one", frequency_cap=1)

    response = _serve(serve_client, world, leovid=VISITOR)

    assert response.status_code == 200
    assert _ad_keys(response) == ["ad_one"]


def test_a_transient_database_error_while_building_the_payload_is_retried_once(
    serve_client, serve_app, world_factory
):
    from sqlalchemy.exc import OperationalError

    world = world_factory()
    world.add_ad("ad_one")
    real = serve_app.ad_repository.build_serving_payloads
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("pool health check timed out"))
        return real(*args, **kwargs)

    serve_app.ad_repository.build_serving_payloads = flaky

    response = _serve(serve_client, world)

    assert response.status_code == 200
    assert _ad_keys(response) == ["ad_one"]
    assert calls["n"] == 2


def test_a_persistent_database_error_is_not_hidden(serve_client, serve_app, world_factory):
    from sqlalchemy.exc import OperationalError

    world = world_factory()
    world.add_ad("ad_one")

    def broken(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("database down"))

    serve_app.ad_repository.build_serving_payloads = broken

    assert _serve(serve_client, world).status_code == 500


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


def test_each_tenant_only_ever_sees_its_own_ads_and_history(
    serve_client, test_engine, world_factory, enable_personalisation
):
    first = world_factory()
    second = world_factory()
    for world in (first, second):
        enable_personalisation(world)
    a_ad = first.add_ad("ad_a")
    b_ad = second.add_ad("ad_b")
    _tag(test_engine, a_ad, "travel")
    _tag(test_engine, b_ad, "travel")
    # The same visitor id likes travel for tenant one only.
    _likes(test_engine, first, VISITOR, "travel")

    assert _ad_keys(_serve(serve_client, first, leovid=VISITOR)) == ["ad_a"]
    second_response = _serve(serve_client, second, leovid=VISITOR)
    assert _ad_keys(second_response) == ["ad_b"]
    # Tenant two sees no history for that visitor: their key differs per tenant.
    assert second_response.json()["personalisation"]["group"] == "default_no_history"
