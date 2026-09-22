"""Unit tests for public email tracking and S3 event ingestion."""

import base64
import hashlib
import hmac
import json
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from app import app
from core.config import settings
from core.routers.email_tracking import decode_tracking_token
from core.routers.tracking import build_tracking_request, get_tracking_service

EMAIL_PREFIX = "/api/v1/track/email"

SECRET = "leocdp-dev-tracking-secret"


def _encode(tenant: str, campaign: str, profile: str, secret: str = SECRET) -> str:
    raw = f"{tenant}|{campaign}|{profile}"
    signature = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:20]
    return base64.urlsafe_b64encode(f"{raw}|{signature}".encode()).decode().rstrip("=")


def _sign_url(url: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), url.encode(), hashlib.sha256).hexdigest()[:20]


def _webhook_sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeTrackingService:
    def __init__(self):
        self.calls = []

    def ingest(self, data_source_id, events, **kwargs):
        self.calls.append((data_source_id, events, kwargs))
        return None, 0


def test_decode_tracking_token_round_trip():
    token = _encode("t1", "c1", "p1")
    assert decode_tracking_token(token) == {
        "tenant_id": "t1",
        "campaign_id": "c1",
        "master_profile_id": "p1",
    }


def test_decode_tracking_token_rejects_tampering():
    token = _encode("t1", "c1", "p1")
    broken = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert decode_tracking_token(broken) is None


def test_email_events_are_sent_to_tracking_service():
    fake_service = FakeTrackingService()
    app.dependency_overrides[get_tracking_service] = lambda: fake_service
    try:
        with patch.object(settings, "email_tracking_secret", SECRET):
            response = TestClient(app).get(f"{EMAIL_PREFIX}/open?u={_encode('t1', 'c1', 'p1')}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(fake_service.calls) == 1
    source_id, events, kwargs = fake_service.calls[0]
    assert str(source_id) == "acc203ab-9f31-50a1-9533-7a71a65a91e0"
    assert events[0]["event_name"] == "email-opened"
    assert events[0]["properties"]["campaign_id"] == "c1"
    assert kwargs["user_id"] == "p1"


def test_open_returns_gif_without_recording_invalid_token():
    fake_service = FakeTrackingService()
    app.dependency_overrides[get_tracking_service] = lambda: fake_service
    try:
        response = TestClient(app).get(f"{EMAIL_PREFIX}/open?u=bad")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"
    assert not fake_service.calls


def test_click_redirects_with_valid_signature():
    fake_service = FakeTrackingService()
    url = "https://shop.example/x"
    app.dependency_overrides[get_tracking_service] = lambda: fake_service
    try:
        with patch.object(settings, "email_tracking_secret", SECRET):
            response = TestClient(app).get(
                f"{EMAIL_PREFIX}/click?u={_encode('t1', 'c1', 'p1')}&url={url}&k={_sign_url(url)}",
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 302
    assert response.headers["location"] == url
    assert fake_service.calls[0][1][0]["event_name"] == "email-clicked"
    assert fake_service.calls[0][1][0]["page_url"] == url
    assert fake_service.calls[0][1][0]["properties"]["url"] == url


def test_click_rejects_unsigned_redirect():
    response = TestClient(app).get(
        f"{EMAIL_PREFIX}/click?u={_encode('t1', 'c1', 'p1')}&url=https://evil.example/phish&k=forged",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_unsubscribe_records_event_and_returns_confirmation():
    fake_service = FakeTrackingService()
    app.dependency_overrides[get_tracking_service] = lambda: fake_service
    try:
        with patch.object(settings, "email_tracking_secret", SECRET):
            response = TestClient(app).get(
                f"{EMAIL_PREFIX}/unsubscribe?u={_encode('t1', 'c1', 'p1')}"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "unsubscribed" in response.text
    assert fake_service.calls[0][1][0]["event_name"] == "email-unsubscribed"


def test_webhook_hard_bounce_is_saved_and_marked_suppressed():
    fake_service = FakeTrackingService()
    payload = {
        "token": _encode("t1", "c1", "p1"),
        "event": "bounce",
        "email": "b@x.io",
        "bounce_type": "hard",
    }
    body = json.dumps(payload).encode()
    app.dependency_overrides[get_tracking_service] = lambda: fake_service
    try:
        with patch.object(settings, "email_tracking_secret", SECRET), patch.object(
            settings, "email_webhook_signing_secret", "test-webhook-secret"
        ):
            response = TestClient(app).post(
                f"{EMAIL_PREFIX}/webhook?provider=ses",
                content=body,
                headers={
                    "content-type": "application/json",
                    "X-Webhook-Signature": _webhook_sig(body, "test-webhook-secret"),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "event": "email-bounced", "suppressed": True}
    assert fake_service.calls[0][1][0]["event_name"] == "email-bounced"
    assert fake_service.calls[0][1][0]["properties"]["suppression_reason"] == "hard_bounce"


def test_webhook_rejects_unsigned_request():
    with patch.object(settings, "email_webhook_signing_secret", "test-webhook-secret"):
        response = TestClient(app).post(
            f"{EMAIL_PREFIX}/webhook",
            json={"token": "bad", "event": "complaint"},
        )
    assert response.status_code == 401


def test_build_tracking_request_matches_documented_envelope():
    request = build_tracking_request(
        data_source_id=UUID("11111111-1111-1111-1111-111111111111"),
        session_id=" session-123 ",
        anonymous_id="anon-123",
        device_id="device-456",
        device_fingerprint="fingerprint-789",
        user_id="user-456",
        metadata={"source": "web", "campaign": {"name": "spring"}},
        events=[
            {
                "event_name": "page_view",
                "page_url": "https://example.test/",
                "properties": {"experiment": {"variant": 2}},
            }
        ],
    )

    assert str(request.data_source_id) == "11111111-1111-1111-1111-111111111111"
    assert request.session_id == "session-123"
    assert request.metadata["campaign"]["name"] == "spring"
    assert request.events[0]["properties"]["experiment"]["variant"] == 2


def test_webhook_soft_bounce_is_saved_without_suppression_request():
    fake_service = FakeTrackingService()
    payload = {
        "token": _encode("t1", "c1", "p1"),
        "event": "bounce",
        "bounce_type": "soft",
    }
    body = json.dumps(payload).encode()
    app.dependency_overrides[get_tracking_service] = lambda: fake_service
    try:
        with patch.object(settings, "email_tracking_secret", SECRET), patch.object(
            settings, "email_webhook_signing_secret", "test-webhook-secret"
        ):
            response = TestClient(app).post(
                f"{EMAIL_PREFIX}/webhook?provider=ses",
                content=body,
                headers={
                    "content-type": "application/json",
                    "X-Webhook-Signature": _webhook_sig(body, "test-webhook-secret"),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["suppressed"] is False
    assert fake_service.calls[0][1][0]["properties"]["suppression_reason"] is None
