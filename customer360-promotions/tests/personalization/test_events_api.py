"""
POST /events: the contract's acknowledgements, rejections and status codes.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise). The
buffered writers are not started; tests flush them by hand so the stored rows can
be inspected inside the rolled-back test transaction.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from personalization.buffered_writer import BufferedWriter
from personalization.eligibility import shown_counter_name
from personalization.identity import visitor_key
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

VISITOR = "visitor-events-1"
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


@pytest.fixture(autouse=True)
def _require_personalisation_tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.consent_record')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


def _event(ad_key, event_type="impression", request_id=None, *, seconds_ago=1, **extra):
    return {
        "event_type": event_type,
        "request_id": request_id or str(uuid4()),
        "ad_key": ad_key,
        "event_time": (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(),
        **extra,
    }


def _post(client, world, events, *, leovid=VISITOR, headers=None, **body):
    payload = {"tenant": world.tenant_key, "events": events, **body}
    if leovid is not None:
        payload["leovid"] = leovid
    return client.post("/events", json=payload, headers=headers or {})


def _stored(app, test_engine, world):
    app.event_writer.flush()
    return test_engine.execute(
        text(
            "SELECT event_type, user_key_hash, flags, payload, request_id, ad_id "
            "FROM leo_ads.ad_event WHERE tenant_id = :t ORDER BY event_time"
        ),
        {"t": world.tenant_id},
    ).mappings().all()


def _ad_id(test_engine, world, ad_key="ad_one"):
    return test_engine.execute(
        text("SELECT ad_id FROM leo_ads.ad WHERE tenant_id = :t AND ad_key = :k"),
        {"t": world.tenant_id, "k": ad_key},
    ).scalar_one()


@pytest.fixture
def world(world_factory, enable_personalisation):
    created = world_factory()
    created.add_ad("ad_one")
    enable_personalisation(created)
    return created


# --------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------


def test_a_valid_batch_is_accepted_with_202_and_stored(
    serve_client, serve_app, test_engine, world, contract_validator
):
    request_id = str(uuid4())
    events = [
        _event("ad_one", "impression", request_id),
        _event("ad_one", "view", request_id, dwell_ms=1800),
        _event("ad_one", "click", request_id, click_id="clk_1"),
    ]

    response = _post(serve_client, world, events, session_id=str(uuid4()), site="site-1")

    assert response.status_code == 202
    assert response.json() == {"accepted": 3, "rejected": []}
    contract_validator("EventAck", response.json())

    rows = _stored(serve_app, test_engine, world)
    assert [r["event_type"] for r in rows] == ["impression", "view", "click"]
    key = visitor_key(world.tenant_id, VISITOR)
    assert all(bytes(r["user_key_hash"]) == key for r in rows)
    assert all(r["flags"] == [] for r in rows)
    assert rows[1]["payload"]["dwell_ms"] == 1800
    assert rows[2]["payload"]["click_id"] == "clk_1"


def test_event_id_is_derived_so_a_replayed_batch_is_a_duplicate(serve_client, serve_app, test_engine, world):
    events = [_event("ad_one", "impression", str(uuid4()))]

    first = _post(serve_client, world, events)
    replay = _post(serve_client, world, events)

    assert first.json()["accepted"] == 1
    assert replay.json() == {"accepted": 0, "rejected": [{"index": 0, "reason": "duplicate"}]}
    assert len(_stored(serve_app, test_engine, world)) == 1


def test_a_given_event_id_is_used_and_deduplicated(serve_client, world):
    events = [_event("ad_one", "impression", event_id=str(uuid4()))]

    _post(serve_client, world, events)
    replay = _post(serve_client, world, events)

    assert replay.json()["rejected"] == [{"index": 0, "reason": "duplicate"}]


def test_an_impression_raises_the_visitors_frequency_counter(serve_client, hot_state, test_engine, world):
    key = visitor_key(world.tenant_id, VISITOR)
    name = shown_counter_name(_ad_id(test_engine, world), datetime.now(timezone.utc))

    _post(serve_client, world, [_event("ad_one", "impression")])
    _post(serve_client, world, [_event("ad_one", "impression")])

    assert hot_state.get_counters(world.tenant_id, key, [name])[name] == 2


# --------------------------------------------------------------------------
# Rejections
# --------------------------------------------------------------------------


def test_more_than_the_batch_maximum_is_a_400(serve_client, world):
    events = [_event("ad_one") for _ in range(51)]
    assert _post(serve_client, world, events).status_code == 400


def test_an_empty_batch_is_a_validation_error(serve_client, world):
    assert _post(serve_client, world, []).status_code == 422


def test_unknown_tenant_is_a_404(serve_client, world):
    response = serve_client.post("/events", json={"tenant": "nobody", "events": [_event("ad_one")]})
    assert response.status_code == 404


def test_unknown_ad_is_rejected_per_event_without_failing_the_batch(serve_client, world):
    response = _post(serve_client, world, [_event("no_such_ad"), _event("ad_one")])

    assert response.status_code == 202
    assert response.json() == {"accepted": 1, "rejected": [{"index": 0, "reason": "unknown_ad"}]}


def test_an_ad_of_another_tenant_is_unknown(serve_client, world_factory, enable_personalisation, world):
    other = world_factory()
    other.add_ad("ad_other_tenant")
    enable_personalisation(other)

    response = _post(serve_client, world, [_event("ad_other_tenant")])

    assert response.json()["rejected"] == [{"index": 0, "reason": "unknown_ad"}]


def test_view_below_the_dwell_threshold_is_rejected(serve_client, world):
    response = _post(
        serve_client, world,
        [_event("ad_one", "view", dwell_ms=200), _event("ad_one", "view"), _event("ad_one", "view", dwell_ms=1000)],
    )

    assert response.json() == {
        "accepted": 1,
        "rejected": [
            {"index": 0, "reason": "below_view_threshold"},
            {"index": 1, "reason": "below_view_threshold"},
        ],
    }


@pytest.mark.parametrize(
    "mutation, reason",
    [
        ({"event_type": "teleport"}, "invalid_event_type"),
        ({"request_id": "not-a-uuid"}, "invalid_request_id"),
        ({"event_time": "yesterday-ish"}, "invalid_time"),
        ({"event_time": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}, "invalid_time"),
        ({"event_time": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}, "invalid_time"),
    ],
)
def test_malformed_events_are_rejected_with_a_reason(serve_client, world, mutation, reason):
    response = _post(serve_client, world, [{**_event("ad_one"), **mutation}])

    assert response.status_code == 202
    assert response.json()["rejected"] == [{"index": 0, "reason": reason}]


# --------------------------------------------------------------------------
# Quality flags: stored, reported as rejected, excluded downstream
# --------------------------------------------------------------------------


def test_click_without_an_impression_is_flagged_orphan(serve_client, serve_app, test_engine, world):
    response = _post(serve_client, world, [_event("ad_one", "click")])

    assert response.json() == {"accepted": 0, "rejected": [{"index": 0, "reason": "orphan_click"}]}
    (row,) = _stored(serve_app, test_engine, world)
    assert row["flags"] == ["orphan_click"]


def test_click_after_an_impression_in_the_same_request_is_accepted(serve_client, world):
    request_id = str(uuid4())
    first = _post(serve_client, world, [_event("ad_one", "impression", request_id)])
    second = _post(serve_client, world, [_event("ad_one", "click", request_id)])

    assert first.json()["accepted"] == 1
    assert second.json() == {"accepted": 1, "rejected": []}


def test_click_and_impression_in_one_batch_is_not_an_orphan(serve_client, world):
    request_id = str(uuid4())
    response = _post(
        serve_client, world, [_event("ad_one", "impression", request_id), _event("ad_one", "click", request_id)]
    )
    assert response.json() == {"accepted": 2, "rejected": []}


def test_a_second_click_in_quick_succession_is_collapsed(serve_client, serve_app, test_engine, world):
    request_id = str(uuid4())
    _post(serve_client, world, [_event("ad_one", "impression", request_id)])

    response = _post(
        serve_client, world,
        [_event("ad_one", "click", request_id, event_id=str(uuid4())),
         _event("ad_one", "click", request_id, event_id=str(uuid4()))],
    )

    assert response.json() == {"accepted": 1, "rejected": [{"index": 1, "reason": "duplicate"}]}
    flags = [r["flags"] for r in _stored(serve_app, test_engine, world) if r["event_type"] == "click"]
    assert sorted(flags) == [[], ["duplicate"]]


def test_declared_bot_events_are_stored_flagged_and_reported(serve_client, serve_app, test_engine, world):
    response = _post(serve_client, world, [_event("ad_one", "impression")], headers={"User-Agent": BOT_UA})

    assert response.json() == {"accepted": 0, "rejected": [{"index": 0, "reason": "bot"}]}
    (row,) = _stored(serve_app, test_engine, world)
    assert row["flags"] == ["bot"]


def test_a_flagged_impression_does_not_count_towards_frequency(serve_client, hot_state, test_engine, world):
    key = visitor_key(world.tenant_id, VISITOR)
    name = shown_counter_name(_ad_id(test_engine, world), datetime.now(timezone.utc))

    _post(serve_client, world, [_event("ad_one", "impression")], headers={"User-Agent": BOT_UA})

    assert hot_state.get_counters(world.tenant_id, key, [name])[name] == 0


# --------------------------------------------------------------------------
# Visitors who may not be profiled
# --------------------------------------------------------------------------


def test_visitor_without_consent_is_stored_with_a_null_key_and_leaves_no_profile(
    serve_client, serve_app, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world, consent_mode="opt_in")  # no recorded choice: not eligible

    response = _post(serve_client, world, [_event("ad_one", "impression")])

    assert response.json()["accepted"] == 1
    (row,) = _stored(serve_app, test_engine, world)
    assert row["user_key_hash"] is None
    profiles = test_engine.execute(
        text("SELECT count(*) FROM leo_ads.visitor_behaviour_profile WHERE tenant_id = :t"),
        {"t": world.tenant_id},
    ).scalar_one()
    assert profiles == 0


def test_denied_visitor_is_stored_with_a_null_key(serve_client, serve_app, test_engine, world):
    serve_client.put("/consent", json={"tenant": world.tenant_key, "leovid": VISITOR, "status": "denied"})

    _post(serve_client, world, [_event("ad_one", "impression")])

    (row,) = _stored(serve_app, test_engine, world)
    assert row["user_key_hash"] is None


def test_personalisation_off_stores_events_without_a_key(
    serve_client, serve_app, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world, enabled=False)

    _post(serve_client, world, [_event("ad_one", "impression")])

    (row,) = _stored(serve_app, test_engine, world)
    assert row["user_key_hash"] is None


def test_events_without_a_visitor_id_are_stored_anonymously(serve_client, serve_app, test_engine, world):
    response = _post(serve_client, world, [_event("ad_one", "impression")], leovid=None)

    assert response.json()["accepted"] == 1
    (row,) = _stored(serve_app, test_engine, world)
    assert row["user_key_hash"] is None


# --------------------------------------------------------------------------
# Limits and back-pressure
# --------------------------------------------------------------------------


def test_over_the_per_minute_limit_is_a_429_with_retry_after(serve_client, world, monkeypatch):
    from core.config import promotions_settings

    monkeypatch.setattr(promotions_settings, "event_rate_limit_per_minute", 3)

    first = _post(serve_client, world, [_event("ad_one") for _ in range(3)])
    blocked = _post(serve_client, world, [_event("ad_one")])

    assert first.status_code == 202
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "60"


def test_a_full_buffer_is_a_503_with_retry_after_and_a_retry_is_not_a_duplicate(
    serve_client, serve_app, world
):
    serve_app.event_writer = BufferedWriter(
        serve_app.event_repository.insert_events, max_size=1, name="tiny", start=False
    )
    events = [_event("ad_one", "impression", str(uuid4())), _event("ad_one", "impression", str(uuid4()))]

    refused = _post(serve_client, world, events)

    assert refused.status_code == 503
    assert refused.headers["Retry-After"] == "5"

    serve_app.event_writer = BufferedWriter(
        serve_app.event_repository.insert_events, max_size=100, name="roomy", start=False
    )
    retried = _post(serve_client, world, events)
    assert retried.json() == {"accepted": 2, "rejected": []}


def test_events_still_work_when_redis_is_down(serve_client, serve_app, test_engine, hot_state, world):
    hot_state.available = False

    response = _post(serve_client, world, [_event("ad_one", "impression")])

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert len(_stored(serve_app, test_engine, world)) == 1


def test_the_database_still_drops_a_duplicate_when_redis_cannot(
    serve_client, serve_app, test_engine, hot_state, world
):
    hot_state.available = False
    events = [_event("ad_one", "impression", str(uuid4()))]

    _post(serve_client, world, events)
    _post(serve_client, world, events)

    assert len(_stored(serve_app, test_engine, world)) == 1
