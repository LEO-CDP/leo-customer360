"""
Consent end to end: opt-in and opt-out tenants, grant and denial, switching the
tenant's mode, and the immediate effect of a denial.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise).
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from personalization.identity import visitor_key
from repository.profile_repository import ProfileRepository
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

VISITOR = "visitor-consent-1"


@pytest.fixture(autouse=True)
def _require_tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.consent_record')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


@pytest.fixture
def shop(world_factory, test_engine):
    world = world_factory()
    ad = world.add_ad("ad_one")
    test_engine.execute(
        text("UPDATE leo_ads.ad SET metadata = CAST(:m AS jsonb) WHERE ad_id = :id"),
        {"m": json.dumps({"tags": ["travel"]}), "id": ad},
    )
    return world


def _history(test_engine, world, leovid=VISITOR, score=3.0):
    ProfileRepository(engine=test_engine).upsert_profile(
        world.tenant_id,
        visitor_key(world.tenant_id, leovid),
        affinities={"tag": {"travel": {"score": score, "n": 4, "last_at": datetime.now(timezone.utc).isoformat()}}},
        event_count=4,
    )


def _consent(client, world, status, leovid=VISITOR):
    response = client.put("/consent", json={"tenant": world.tenant_key, "leovid": leovid, "status": status})
    assert response.status_code == 204


def _group(client, world, leovid=VISITOR):
    return client.get(
        f"/serve/{world.placement_key}", params={"tenant": world.tenant_key, "leovid": leovid}
    ).json()["personalisation"]["group"]


def _event(request_id, event_type="impression"):
    return {
        "event_type": event_type, "request_id": request_id, "ad_key": "ad_one",
        "event_time": datetime.now(timezone.utc).isoformat(),
    }


def _stored_keys(serve_app, test_engine, world):
    serve_app.event_writer.flush()
    return test_engine.execute(
        text("SELECT user_key_hash FROM leo_ads.ad_event WHERE tenant_id = :t"), {"t": world.tenant_id}
    ).scalars().all()


# --------------------------------------------------------------------------
# Opt-in tenant
# --------------------------------------------------------------------------


def test_opt_in_with_no_record_is_not_personalised_and_no_key_is_stored(
    serve_client, serve_app, test_engine, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_in")
    _history(test_engine, shop)

    assert _group(serve_client, shop) == "default_no_consent"
    serve_client.post("/events", json={"tenant": shop.tenant_key, "leovid": VISITOR, "events": [_event(str(uuid4()))]})

    assert _stored_keys(serve_app, test_engine, shop) == [None]


def test_opt_in_granted_is_personalised_and_events_carry_the_key(
    serve_client, serve_app, test_engine, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_in")
    _history(test_engine, shop)
    _consent(serve_client, shop, "granted")

    assert _group(serve_client, shop) == "personalised"
    serve_client.post("/events", json={"tenant": shop.tenant_key, "leovid": VISITOR, "events": [_event(str(uuid4()))]})

    assert [bytes(k) for k in _stored_keys(serve_app, test_engine, shop)] == [visitor_key(shop.tenant_id, VISITOR)]


def test_denial_stops_personalisation_and_profile_feeding_and_leaves_an_audit_row(
    serve_client, serve_app, test_engine, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_in")
    _history(test_engine, shop)
    _consent(serve_client, shop, "granted")
    _consent(serve_client, shop, "denied")

    assert _group(serve_client, shop) == "default_no_consent"
    serve_client.post("/events", json={"tenant": shop.tenant_key, "leovid": VISITOR, "events": [_event(str(uuid4()))]})
    assert _stored_keys(serve_app, test_engine, shop) == [None]

    audit = test_engine.execute(
        text("SELECT status FROM leo_ads.consent_history WHERE tenant_id = :t AND user_key_hash = :k"),
        {"t": shop.tenant_id, "k": visitor_key(shop.tenant_id, VISITOR)},
    ).scalars().all()
    assert sorted(audit) == ["denied", "granted"]


# --------------------------------------------------------------------------
# Opt-out tenant
# --------------------------------------------------------------------------


def test_opt_out_with_no_record_is_personalised_until_denied(
    serve_client, test_engine, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_out")
    _history(test_engine, shop)

    assert _group(serve_client, shop) == "personalised"
    _consent(serve_client, shop, "denied")
    assert _group(serve_client, shop) == "default_no_consent"


# --------------------------------------------------------------------------
# Switching the tenant's mode
# --------------------------------------------------------------------------


def test_switching_mode_keeps_recorded_choices_and_unrecorded_visitors_follow_the_new_mode(
    serve_client, test_engine, hot_state, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_out")
    for who in ("granted-one", "denied-one", "silent-one"):
        _history(test_engine, shop, who)
    _consent(serve_client, shop, "granted", "granted-one")
    _consent(serve_client, shop, "denied", "denied-one")

    assert _group(serve_client, shop, "granted-one") == "personalised"
    assert _group(serve_client, shop, "denied-one") == "default_no_consent"
    assert _group(serve_client, shop, "silent-one") == "personalised"  # opt_out: no record is eligible

    enable_personalisation(shop, consent_mode="opt_in")
    hot_state.delete(shop.tenant_id, "settings")  # the settings cache expiring, as it would within a minute

    assert _group(serve_client, shop, "granted-one") == "personalised"  # recorded choices are kept
    assert _group(serve_client, shop, "denied-one") == "default_no_consent"
    assert _group(serve_client, shop, "silent-one") == "default_no_consent"  # follows the new mode


def test_a_profile_built_earlier_is_not_used_once_the_visitor_is_no_longer_eligible(
    serve_client, test_engine, hot_state, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_out")
    _history(test_engine, shop, "later-ineligible")
    assert _group(serve_client, shop, "later-ineligible") == "personalised"

    enable_personalisation(shop, consent_mode="opt_in")  # no record now means not eligible
    hot_state.delete(shop.tenant_id, "settings")

    # The history is still stored (the batch job removes it), but ranking never reads it.
    assert _group(serve_client, shop, "later-ineligible") == "default_no_consent"


# --------------------------------------------------------------------------
# An open erasure request behaves as a denial
# --------------------------------------------------------------------------


def _open_erasure(test_engine, world, status="pending"):
    test_engine.execute(
        text(
            "INSERT INTO leo_ads.erasure_request (tenant_id, user_key_hash, status) VALUES (:t, :k, :s)"
        ),
        {"t": world.tenant_id, "k": visitor_key(world.tenant_id, VISITOR), "s": status},
    )


@pytest.mark.parametrize("status", ["pending", "redis_purged", "failed"])
def test_an_open_erasure_request_stops_personalisation_even_if_the_site_reports_granted(
    serve_client, serve_app, test_engine, shop, enable_personalisation, status
):
    enable_personalisation(shop, consent_mode="opt_out")
    _history(test_engine, shop)
    assert _group(serve_client, shop) == "personalised"
    _open_erasure(test_engine, shop, status)

    response = serve_client.get(
        f"/serve/{shop.placement_key}",
        params={"tenant": shop.tenant_key, "leovid": VISITOR, "consent": "granted"},
    )

    assert response.json()["personalisation"]["group"] == "default_no_consent"
    serve_app.decision_writer.flush()
    keyed = test_engine.execute(
        text("SELECT count(*) FROM leo_ads.serving_decision WHERE tenant_id = :t AND user_key_hash IS NOT NULL "
             "AND decided_at > now() - interval '1 minute' AND group_code <> 'personalised'"),
        {"t": shop.tenant_id},
    ).scalar_one()
    assert keyed == 0  # nothing new carries the visitor's key while the erasure runs


def test_a_site_reported_grant_does_not_recreate_consent_for_a_visitor_being_erased(
    serve_client, test_engine, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_in")
    _open_erasure(test_engine, shop)

    serve_client.get(
        f"/serve/{shop.placement_key}",
        params={"tenant": shop.tenant_key, "leovid": VISITOR, "consent": "granted"},
    )

    rows = test_engine.execute(
        text("SELECT count(*) FROM leo_ads.consent_record WHERE tenant_id = :t"), {"t": shop.tenant_id}
    ).scalar_one()
    assert rows == 0


def test_events_during_an_open_erasure_are_stored_without_the_key(
    serve_client, serve_app, test_engine, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_out")
    _open_erasure(test_engine, shop)

    serve_client.post("/events", json={"tenant": shop.tenant_key, "leovid": VISITOR, "events": [_event(str(uuid4()))]})

    assert _stored_keys(serve_app, test_engine, shop) == [None]


def test_a_completed_erasure_no_longer_blocks_the_visitor(
    serve_client, test_engine, shop, enable_personalisation
):
    enable_personalisation(shop, consent_mode="opt_out")
    _history(test_engine, shop)
    test_engine.execute(
        text(
            "INSERT INTO leo_ads.erasure_request (tenant_id, user_key_hash, status, completed_at, deleted_counts) "
            "VALUES (:t, :k, 'completed', now(), '{}')"
        ),
        {"t": shop.tenant_id, "k": visitor_key(shop.tenant_id, VISITOR)},
    )

    assert _group(serve_client, shop) == "personalised"


# --------------------------------------------------------------------------
# Immediate effect of a denial
# --------------------------------------------------------------------------


def test_denial_purges_the_visitors_fresh_state_at_once(serve_client, hot_state, shop, enable_personalisation):
    enable_personalisation(shop, consent_mode="opt_in")
    _consent(serve_client, shop, "granted")
    key = visitor_key(shop.tenant_id, VISITOR)
    hot_state.apply_event(
        shop.tenant_id, key, {"event_id": "e1", "ad_id": 1, "event_type": "click", "at": 1.0}, [("tag", "travel")]
    )
    hot_state.incr_counter(shop.tenant_id, key, "shown:1:20261018", 100)
    other = visitor_key(shop.tenant_id, "someone-else")
    hot_state.apply_event(
        shop.tenant_id, other, {"event_id": "e2", "ad_id": 1, "event_type": "click", "at": 1.0}, [("tag", "travel")]
    )

    _consent(serve_client, shop, "denied")

    assert hot_state.get_signals(shop.tenant_id, key) == []
    assert hot_state.get_counters(shop.tenant_id, key, ["shown:1:20261018"]) == {"shown:1:20261018": 0}
    assert len(hot_state.get_signals(shop.tenant_id, other)) == 1  # nobody else is touched


def test_a_site_reported_denial_also_purges_fresh_state(serve_client, hot_state, shop, enable_personalisation):
    enable_personalisation(shop, consent_mode="opt_out")
    key = visitor_key(shop.tenant_id, VISITOR)
    hot_state.apply_event(
        shop.tenant_id, key, {"event_id": "e1", "ad_id": 1, "event_type": "click", "at": 1.0}, [("tag", "travel")]
    )

    serve_client.get(
        f"/serve/{shop.placement_key}",
        params={"tenant": shop.tenant_key, "leovid": VISITOR, "consent": "denied"},
    )

    assert hot_state.get_signals(shop.tenant_id, key) == []


def test_denial_still_succeeds_when_redis_is_down(serve_client, hot_state, shop):
    hot_state.available = False
    response = serve_client.put(
        "/consent", json={"tenant": shop.tenant_key, "leovid": VISITOR, "status": "denied"}
    )
    assert response.status_code == 204


def test_a_purge_leaves_rate_limit_counters_so_a_denial_cannot_reset_the_limit(hot_state):
    key = b"\x03" * 16
    hot_state.incr_counter(1, key, "rate:consent:5", 120)
    hot_state.incr_counter(1, key, "shown:1:20261018", 120)

    hot_state.purge_visitor(1, key)

    assert hot_state.get_counters(1, key, ["rate:consent:5", "shown:1:20261018"]) == {
        "rate:consent:5": 1,
        "shown:1:20261018": 0,
    }


def test_purge_only_touches_the_named_visitor_and_tenant(hot_state):
    first, second = b"\x01" * 16, b"\x02" * 16
    for tenant, key in ((1, first), (1, second), (2, first)):
        hot_state.apply_event(tenant, key, {"event_id": "e", "ad_id": 1, "event_type": "click", "at": 1.0}, [])
        hot_state.incr_counter(tenant, key, "n", 100)

    removed = hot_state.purge_visitor(1, first)

    assert removed == 2
    assert hot_state.get_signals(1, second) and hot_state.get_signals(2, first)
