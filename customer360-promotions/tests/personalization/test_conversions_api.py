"""
POST /conversions: signature, replay window, duplicates, attribution and storage.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise). The
app runs on the rolled-back test transaction; the writer is flushed by hand.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from core.config import promotions_settings
from personalization.buffered_writer import BufferedWriter
from personalization.conversions import issue_click_id
from personalization.identity import visitor_key
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

SECRET = "test-conversion-secret"
VISITOR = "visitor-conv-1"


@pytest.fixture(autouse=True)
def _require_personalisation_tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.consent_record')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


@pytest.fixture
def world(world_factory, enable_personalisation, monkeypatch):
    created = world_factory()
    created.add_ad("ad_one")
    enable_personalisation(created)
    monkeypatch.setattr(promotions_settings, "conversion_secrets", {created.tenant_key: SECRET})
    return created


def _now(delta=0):
    return datetime.now(timezone.utc) + timedelta(seconds=delta)


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client, world, payload, *, secret=SECRET, timestamp=None, signature=None, tenant=None, headers=None):
    body = json.dumps(payload).encode()
    sent = {
        "X-Tenant-Key": tenant or world.tenant_key,
        "X-Timestamp": timestamp or _now().isoformat(),
        "X-Signature": signature if signature is not None else _sign(body, secret),
        "Content-Type": "application/json",
        **(headers or {}),
    }
    sent = {k: v for k, v in sent.items() if v is not None}
    return client.post("/conversions", content=body, headers=sent)


def _click(app, test_engine, world, click_id="clk_abc", *, key=None, flags=None):
    """Store the originating click (with a visitor key unless told otherwise)."""
    ad_id = test_engine.execute(
        text("SELECT ad_id FROM leo_ads.ad WHERE tenant_id = :t AND ad_key = 'ad_one'"), {"t": world.tenant_id}
    ).scalar_one()
    request_id = str(uuid4())
    app.event_repository.insert_events([{
        "event_id": str(uuid4()), "event_time": _now(-60), "tenant_id": world.tenant_id,
        "event_type": "click", "ad_id": ad_id, "placement_id": world.placement_id,
        "user_key_hash": key if key is not None else visitor_key(world.tenant_id, VISITOR),
        "request_id": request_id, "payload": {"click_id": click_id}, "flags": flags or [],
    }])
    return ad_id, request_id


def _conversion(click_id="clk_abc", **extra):
    return {"event_id": str(uuid4()), "click_id": click_id, "conversion_time": _now(-5).isoformat(), **extra}


def _stored(app, test_engine, world):
    app.event_writer.flush()
    return test_engine.execute(
        text(
            "SELECT event_type, ad_id, user_key_hash, revenue_amount, currency, payload, flags "
            "FROM leo_ads.ad_event WHERE tenant_id = :t AND event_type = 'conversion'"
        ),
        {"t": world.tenant_id},
    ).mappings().all()


# --------------------------------------------------------------------------
# Accepted
# --------------------------------------------------------------------------


def test_a_signed_conversion_is_accepted_and_stored_against_the_click(serve_client, serve_app, test_engine, world):
    ad_id, request_id = _click(serve_app, test_engine, world)

    response = _post(serve_client, world, _conversion(revenue_amount="49.90", currency="aud"))

    assert response.status_code == 202
    (row,) = _stored(serve_app, test_engine, world)
    assert row["ad_id"] == ad_id
    assert row["revenue_amount"] == Decimal("49.90")
    assert row["currency"] == "AUD"
    assert row["payload"]["source"] == "postback"
    assert row["payload"]["click_id"] == "clk_abc"
    assert row["flags"] == []
    # The click's visitor key is reused, never derived.
    assert bytes(row["user_key_hash"]) == visitor_key(world.tenant_id, VISITOR)


def test_a_conversion_from_an_unprofiled_visitors_click_has_a_null_key(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    test_engine.execute(text("UPDATE leo_ads.ad_event SET user_key_hash = NULL WHERE tenant_id = :t"), {"t": world.tenant_id})

    assert _post(serve_client, world, _conversion()).status_code == 202
    (row,) = _stored(serve_app, test_engine, world)
    assert row["user_key_hash"] is None


def test_attribution_by_request_id_alone(serve_client, serve_app, test_engine, world):
    ad_id, request_id = _click(serve_app, test_engine, world)

    response = _post(serve_client, world, {
        "event_id": str(uuid4()), "request_id": request_id, "conversion_time": _now(-5).isoformat(),
    })

    assert response.status_code == 202
    assert _stored(serve_app, test_engine, world)[0]["ad_id"] == ad_id


def test_a_conversion_without_revenue_is_fine(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    assert _post(serve_client, world, _conversion()).status_code == 202
    (row,) = _stored(serve_app, test_engine, world)
    assert row["revenue_amount"] is None and row["currency"] is None


def test_the_click_id_issued_in_serve_round_trips_to_a_conversion(serve_client, serve_app, test_engine, world, hot_state):
    served = serve_client.get(f"/serve/{world.placement_key}", params={"tenant": world.tenant_key}).json()
    ad = served["ads"][0]
    click_id = ad["tracking"]["clickId"]
    assert ad["destination"] is None or ad["destination"]["clickId"] == click_id
    ad_id = test_engine.execute(
        text("SELECT ad_id FROM leo_ads.ad WHERE tenant_id = :t AND ad_key = 'ad_one'"), {"t": world.tenant_id}
    ).scalar_one()
    assert click_id == issue_click_id(
        promotions_settings.effective_visitor_key_secret, world.tenant_id, served["request_id"], ad_id
    )

    # The widget reports the click with that id; the postback then resolves it.
    events = [
        {"event_type": t, "request_id": served["request_id"], "ad_key": "ad_one",
         "event_time": _now(-2).isoformat(), **({"click_id": click_id} if t == "click" else {})}
        for t in ("impression", "click")
    ]
    ack = serve_client.post("/events", json={"tenant": world.tenant_key, "leovid": VISITOR, "events": events})
    assert ack.json()["accepted"] == 2
    serve_app.event_writer.flush()

    assert _post(serve_client, world, _conversion(click_id)).status_code == 202


def test_click_ids_are_stable_unguessable_and_per_tenant():
    first = issue_click_id("master", 1, "req-1", 7)
    assert first == issue_click_id("master", 1, "req-1", 7)
    assert first != issue_click_id("master", 2, "req-1", 7)
    assert first != issue_click_id("master", 1, "req-2", 7)
    assert first != issue_click_id("other", 1, "req-1", 7)
    assert len(first) == 32
    assert issue_click_id(None, 1, "req-1", 7) is None


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


def test_a_bad_signature_is_401(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    assert _post(serve_client, world, _conversion(), secret="wrong-secret").status_code == 401


@pytest.mark.parametrize("missing", ["X-Tenant-Key", "X-Timestamp", "X-Signature"])
def test_a_missing_header_is_401(serve_client, serve_app, test_engine, world, missing):
    _click(serve_app, test_engine, world)
    body = json.dumps(_conversion()).encode()
    headers = {"X-Tenant-Key": world.tenant_key, "X-Timestamp": _now().isoformat(), "X-Signature": _sign(body)}
    del headers[missing]

    assert serve_client.post("/conversions", content=body, headers=headers).status_code == 401


def test_a_signature_over_a_different_body_is_401(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    other = json.dumps({"tampered": True}).encode()
    assert _post(serve_client, world, _conversion(), signature=_sign(other)).status_code == 401


def test_the_sha256_prefix_is_accepted(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    payload = _conversion()
    sig = "sha256=" + _sign(json.dumps(payload).encode())
    assert _post(serve_client, world, payload, signature=sig).status_code == 202


@pytest.mark.parametrize("offset", [-600, 600])
def test_a_timestamp_outside_the_replay_window_is_401(serve_client, serve_app, test_engine, world, offset):
    _click(serve_app, test_engine, world)
    assert _post(serve_client, world, _conversion(), timestamp=_now(offset).isoformat()).status_code == 401


def test_an_unparseable_timestamp_is_401(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    assert _post(serve_client, world, _conversion(), timestamp="soon").status_code == 401


def test_another_tenants_secret_does_not_work(serve_client, serve_app, test_engine, world, monkeypatch):
    other_secret = "other-tenant-secret"
    monkeypatch.setattr(promotions_settings, "conversion_secrets", {world.tenant_key: SECRET, "other": other_secret})
    _click(serve_app, test_engine, world)

    # Signed with the other tenant's secret but claiming this tenant.
    assert _post(serve_client, world, _conversion(), secret=other_secret).status_code == 401


def test_a_tenant_without_a_secret_is_indistinguishable_from_a_bad_signature(
    serve_client, serve_app, test_engine, world
):
    assert _post(serve_client, world, _conversion(), tenant="no-such-tenant").status_code == 401


def test_conversions_are_disabled_until_a_secret_is_configured(serve_client, world, monkeypatch):
    monkeypatch.setattr(promotions_settings, "conversion_secrets", {})
    assert _post(serve_client, world, _conversion()).status_code == 503


def test_secrets_never_appear_in_the_settings_repr():
    promotions_settings.conversion_secrets["probe"] = "super-secret-value"
    try:
        assert "super-secret-value" not in repr(promotions_settings)
    finally:
        promotions_settings.conversion_secrets.pop("probe", None)


# --------------------------------------------------------------------------
# Duplicates, attribution and validation
# --------------------------------------------------------------------------


def test_a_repeated_event_id_is_409(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    payload = _conversion()

    assert _post(serve_client, world, payload).status_code == 202
    assert _post(serve_client, world, payload).status_code == 409
    assert len(_stored(serve_app, test_engine, world)) == 1


def test_a_repeated_event_id_is_409_even_when_redis_is_down(serve_client, serve_app, test_engine, hot_state, world):
    _click(serve_app, test_engine, world)
    payload = _conversion()
    assert _post(serve_client, world, payload).status_code == 202
    serve_app.event_writer.flush()

    hot_state.available = False

    assert _post(serve_client, world, payload).status_code == 409


def test_no_resolvable_click_or_request_is_422(serve_client, serve_app, test_engine, world):
    assert _post(serve_client, world, _conversion("clk_unknown")).status_code == 422
    assert _post(serve_client, world, {
        "event_id": str(uuid4()), "request_id": str(uuid4()), "conversion_time": _now(-5).isoformat(),
    }).status_code == 422
    assert _post(serve_client, world, {"event_id": str(uuid4()), "conversion_time": _now(-5).isoformat()}).status_code == 422
    assert _stored(serve_app, test_engine, world) == []


def test_a_flagged_click_cannot_be_the_origin(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world, flags=["bot"])
    assert _post(serve_client, world, _conversion()).status_code == 422


def test_another_tenants_click_id_is_not_attributable(serve_client, serve_app, test_engine, world, world_factory, enable_personalisation):
    other = world_factory()
    other.add_ad("ad_one")
    enable_personalisation(other)
    _click(serve_app, test_engine, other, click_id="clk_theirs")

    assert _post(serve_client, world, _conversion("clk_theirs")).status_code == 422


@pytest.mark.parametrize(
    "mutation",
    [
        {"revenue_amount": "12.5"},  # revenue without a currency
        {"currency": "AUD"},  # currency without revenue
        {"revenue_amount": "-3", "currency": "AUD"},
        {"revenue_amount": "1,5", "currency": "AUD"},
        {"revenue_amount": "1.123456789", "currency": "AUD"},
        {"currency": "AUSD", "revenue_amount": "1"},
        {"event_id": "not-a-uuid"},
        {"conversion_time": "whenever"},
        {"conversion_time": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()},
        {"conversion_time": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()},
        {"request_id": "not-a-uuid"},
    ],
)
def test_malformed_conversions_are_422(serve_client, serve_app, test_engine, world, mutation):
    _click(serve_app, test_engine, world)
    assert _post(serve_client, world, {**_conversion(), **mutation}).status_code == 422


def test_a_body_that_is_not_json_is_422_after_a_valid_signature(serve_client, world):
    body = b"not json"
    response = serve_client.post(
        "/conversions", content=body,
        headers={"X-Tenant-Key": world.tenant_key, "X-Timestamp": _now().isoformat(), "X-Signature": _sign(body)},
    )
    assert response.status_code == 422


def test_a_full_buffer_is_503_and_a_retry_is_not_a_duplicate(serve_client, serve_app, test_engine, world):
    _click(serve_app, test_engine, world)
    serve_app.event_writer = BufferedWriter(
        serve_app.event_repository.insert_events, max_size=1, name="tiny", start=False
    )
    serve_app.event_writer.put({"filler": True})
    payload = _conversion()

    refused = _post(serve_client, world, payload)
    assert refused.status_code == 503
    assert refused.headers["Retry-After"] == "5"

    serve_app.event_writer = BufferedWriter(
        serve_app.event_repository.insert_events, max_size=10, name="roomy", start=False
    )
    assert _post(serve_client, world, payload).status_code == 202
