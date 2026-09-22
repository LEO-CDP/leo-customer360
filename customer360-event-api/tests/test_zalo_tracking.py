"""Unit tests for the Zalo ZNS webhook (S3-first, shared make_webhook_router)."""

import base64
import hashlib
import hmac
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from core.config import settings
from core.routers.tracking import get_tracking_service

ZALO_PREFIX = "/api/v1/track/zalo"
TRACKING_SECRET = "leocdp-dev-tracking-secret"
WEBHOOK_SECRET = "test-zalo-webhook-secret"


def _token(tenant: str, campaign: str, profile: str, secret: str = TRACKING_SECRET) -> str:
    raw = f"{tenant}|{campaign}|{profile}"
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:20]
    return base64.urlsafe_b64encode(f"{raw}|{sig}".encode()).decode().rstrip("=")


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeTrackingService:
    def __init__(self):
        self.calls = []

    def ingest(self, data_source_id, events, **kwargs):
        self.calls.append((data_source_id, events, kwargs))
        return None, 0


def _client(fake):
    app.dependency_overrides[get_tracking_service] = lambda: fake
    return TestClient(app)


def test_opt_out_event_recorded_to_s3_with_suppression_reason():
    fake = FakeTrackingService()
    client = _client(fake)
    body = json.dumps({
        "event_name": "user_unfollow",
        "tracking_id": _token("t1", "c1", "p1"),
        "msg_id": "zmsg-1",
    }).encode()
    with patch.object(settings, "email_webhook_signing_secret", WEBHOOK_SECRET):
        resp = client.post(f"{ZALO_PREFIX}/webhook", content=body,
                           headers={"X-ZEvent-Signature": _sign(body, WEBHOOK_SECRET)})
    app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["event"] == "zalo-opt-out"
    assert payload["suppression_reason"] == "opt_out"
    # S3-first: the event went to the tracking service with the opt-out marker,
    # NOT a direct DB write.
    assert fake.calls, "webhook must record the event to the tracking service (S3)"
    _, events, _ = fake.calls[0]
    props = events[0]["properties"]
    assert props["tracking_channel"] == "zalo"
    assert props["suppression_reason"] == "opt_out"
    assert props["master_profile_id"] == "p1"


def test_delivered_event_has_no_suppression_reason():
    fake = FakeTrackingService()
    client = _client(fake)
    body = json.dumps({"event_name": "delivered", "tracking_id": _token("t1", "c1", "p2"), "msg_id": "m2"}).encode()
    with patch.object(settings, "email_webhook_signing_secret", WEBHOOK_SECRET):
        resp = client.post(f"{ZALO_PREFIX}/webhook", content=body,
                           headers={"X-ZEvent-Signature": _sign(body, WEBHOOK_SECRET)})
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["event"] == "zalo-delivered"
    assert resp.json()["suppression_reason"] is None


def test_webhook_rejects_bad_signature():
    fake = FakeTrackingService()
    client = _client(fake)
    body = json.dumps({"event_name": "user_unfollow", "tracking_id": _token("t1", "c1", "p1")}).encode()
    with patch.object(settings, "email_webhook_signing_secret", WEBHOOK_SECRET):
        resp = client.post(f"{ZALO_PREFIX}/webhook", content=body, headers={"X-ZEvent-Signature": "sha256=deadbeef"})
    app.dependency_overrides.clear()
    assert resp.status_code == 401
    assert not fake.calls  # nothing recorded on a rejected callback


def test_webhook_disabled_when_secret_unset():
    fake = FakeTrackingService()
    client = _client(fake)
    body = json.dumps({"event_name": "user_unfollow", "tracking_id": _token("t1", "c1", "p1")}).encode()
    with patch.object(settings, "email_webhook_signing_secret", ""):
        resp = client.post(f"{ZALO_PREFIX}/webhook", content=body, headers={"X-ZEvent-Signature": "x"})
    app.dependency_overrides.clear()
    assert resp.status_code == 503


def test_unknown_event_or_bad_token_ignored():
    fake = FakeTrackingService()
    client = _client(fake)
    body = json.dumps({"event_name": "some_unmapped_event", "tracking_id": _token("t1", "c1", "p1")}).encode()
    with patch.object(settings, "email_webhook_signing_secret", WEBHOOK_SECRET):
        resp = client.post(f"{ZALO_PREFIX}/webhook", content=body,
                           headers={"X-ZEvent-Signature": _sign(body, WEBHOOK_SECRET)})
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert not fake.calls
