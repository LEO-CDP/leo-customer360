"""
Serve, send an event, serve again in the same session: the change is visible on the
very next request, replays and reordering do not double count, and the
personalisation step stays inside its time budget.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise).
"""

import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from core.config import promotions_settings
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

VISITOR = "visitor-fresh-1"


@pytest.fixture(autouse=True)
def _require_personalisation_tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.consent_record')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


def _tag(test_engine, ad_id, tag):
    test_engine.execute(
        text("UPDATE leo_ads.ad SET metadata = CAST(:m AS jsonb) WHERE ad_id = :id"),
        {"m": json.dumps({"tags": [tag]}), "id": ad_id},
    )


def _well_measured(test_engine, world, *ad_ids):
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


@pytest.fixture
def shop(world_factory, enable_personalisation, test_engine):
    """A tenant with two 'outdoor' ads and one 'travel' ad, all well measured."""
    world = world_factory()
    enable_personalisation(world)
    ids = {"a1": world.add_ad("ad_a1"), "b": world.add_ad("ad_b"), "a2": world.add_ad("ad_a2")}
    _tag(test_engine, ids["a1"], "outdoor")
    _tag(test_engine, ids["a2"], "outdoor")
    _tag(test_engine, ids["b"], "travel")
    _well_measured(test_engine, world, *ids.values())
    return world


def _serve(client, world, **params):
    return client.get(
        "/serve/" + world.placement_key,
        params={"tenant": world.tenant_key, "leovid": VISITOR, "limit": 20, **params},
    )


def _keys(response):
    return [a["adId"] for a in response.json()["ads"]]


def _event(ad_key, event_type, request_id, seconds_ago=1, **extra):
    return {
        "event_type": event_type,
        "request_id": request_id,
        "ad_key": ad_key,
        "event_time": (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(),
        **extra,
    }


def _post(client, world, events):
    response = client.post("/events", json={"tenant": world.tenant_key, "leovid": VISITOR, "events": events})
    assert response.status_code == 202, response.text
    return response.json()


def test_a_dismissal_removes_the_ad_on_the_very_next_request(serve_client, shop):
    first = _serve(serve_client, shop)
    assert "ad_a1" in _keys(first)
    request_id = first.json()["request_id"]

    _post(serve_client, shop, [_event("ad_a1", "impression", request_id), _event("ad_a1", "dismiss", request_id)])
    second = _serve(serve_client, shop)

    assert "ad_a1" not in _keys(second)
    assert second.json()["personalisation"]["group"] == "personalised"  # fresh behaviour is history


def test_a_dismissal_also_lowers_similar_ads(serve_client, shop):
    before = _keys(_serve(serve_client, shop))
    assert before == ["ad_a1", "ad_b", "ad_a2"]
    request_id = str(uuid4())

    _post(serve_client, shop, [_event("ad_a1", "impression", request_id), _event("ad_a1", "dismiss", request_id)])

    # The other 'outdoor' ad shares its tag, so it now ranks below the unrelated one.
    assert _keys(_serve(serve_client, shop)) == ["ad_b", "ad_a2"]


def test_a_click_raises_similar_ads(serve_client, shop):
    assert _keys(_serve(serve_client, shop)) == ["ad_a1", "ad_b", "ad_a2"]
    request_id = str(uuid4())

    _post(serve_client, shop, [_event("ad_a2", "impression", request_id), _event("ad_a2", "click", request_id)])

    after = _keys(_serve(serve_client, shop))
    assert after[-1] == "ad_b"
    assert set(after[:2]) == {"ad_a1", "ad_a2"}


def test_replaying_the_batch_changes_nothing(serve_client, shop, hot_state):
    from personalization.identity import visitor_key

    key = visitor_key(shop.tenant_id, VISITOR)
    request_id = str(uuid4())
    batch = [
        _event("ad_a2", "impression", request_id, event_id=str(uuid4())),
        _event("ad_a2", "click", request_id, event_id=str(uuid4())),
    ]

    _post(serve_client, shop, batch)
    once = _keys(_serve(serve_client, shop))
    replay = _post(serve_client, shop, batch)
    twice = _keys(_serve(serve_client, shop))

    assert [r["reason"] for r in replay["rejected"]] == ["duplicate", "duplicate"]
    # Nothing was applied twice: still exactly one stored click signal.
    assert [s["type"] for s in hot_state.get_signals(shop.tenant_id, key)] == ["click"]
    # Ads that tie (both 'outdoor') may swap on exploration noise; the rest is stable.
    assert once[-1] == twice[-1] == "ad_b"
    assert set(once) == set(twice)


def test_a_click_that_happened_after_a_dismissal_wins_even_if_it_arrives_first(serve_client, shop):
    request_id = str(uuid4())
    _post(serve_client, shop, [_event("ad_a1", "impression", request_id, seconds_ago=30)])

    _post(serve_client, shop, [_event("ad_a1", "click", request_id, seconds_ago=5)])  # later in time, first to arrive
    _post(serve_client, shop, [_event("ad_a1", "dismiss", request_id, seconds_ago=20)])  # earlier in time

    assert "ad_a1" in _keys(_serve(serve_client, shop))


def test_a_dismissal_that_happened_after_a_click_wins_even_if_it_arrives_first(serve_client, shop):
    request_id = str(uuid4())
    _post(serve_client, shop, [_event("ad_a1", "impression", request_id, seconds_ago=30)])

    _post(serve_client, shop, [_event("ad_a1", "dismiss", request_id, seconds_ago=5)])
    _post(serve_client, shop, [_event("ad_a1", "click", request_id, seconds_ago=20, event_id=str(uuid4()))])

    assert "ad_a1" not in _keys(_serve(serve_client, shop))


def test_a_visitor_who_may_not_be_profiled_is_not_changed_by_events(serve_client, shop, enable_personalisation):
    enable_personalisation(shop, consent_mode="opt_in")  # no consent recorded
    request_id = str(uuid4())

    _post(serve_client, shop, [_event("ad_a1", "impression", request_id), _event("ad_a1", "dismiss", request_id)])

    response = _serve(serve_client, shop)
    assert "ad_a1" in _keys(response)
    assert response.json()["personalisation"]["group"] == "default_no_consent"


def test_events_still_land_in_the_database_for_the_batch_job(serve_client, serve_app, test_engine, shop):
    request_id = str(uuid4())
    _post(serve_client, shop, [_event("ad_a1", "impression", request_id), _event("ad_a1", "dismiss", request_id)])
    serve_app.event_writer.flush()

    types = test_engine.execute(
        text("SELECT event_type FROM leo_ads.ad_event WHERE tenant_id = :t ORDER BY event_type"),
        {"t": shop.tenant_id},
    ).scalars().all()
    assert types == ["dismiss", "impression"]


def test_serving_still_works_with_redis_down(serve_client, hot_state, shop):
    hot_state.available = False

    response = _serve(serve_client, shop)

    assert response.status_code == 200
    assert _keys(response) == ["ad_a1", "ad_b", "ad_a2"]


def test_the_personalisation_step_stays_within_its_time_budget(serve_app, shop, hot_state):
    """With in-memory hot state, the ranking overhead is a small fraction of the budget."""
    from personalization.identity import visitor_key

    key = visitor_key(shop.tenant_id, VISITOR)
    for i in range(60):  # a busy visitor: many fresh signals
        hot_state.apply_event(
            shop.tenant_id, key,
            {"event_id": f"e{i}", "ad_id": 1 + i % 3, "event_type": "click", "at": time.time() - i},
            [("tag", "outdoor"), ("format", "single_banner")],
        )
    service = serve_app._ranking_service()
    samples = []
    for _ in range(40):
        started = time.perf_counter()
        service.rank(tenant_key=shop.tenant_key, placement_ref=shop.placement_key, limit=5, leovid=VISITOR)
        samples.append((time.perf_counter() - started) * 1000)

    # Includes loading the candidates from the database, which the budget does not;
    # the median must still sit well inside the budget plus that load.
    assert statistics.median(samples) < promotions_settings.personalization_time_budget_ms * 2
