"""Tests for email tracking: token verification + the public
open/click/unsubscribe/webhook routes (DB writes mocked)."""

import base64
import hashlib
import hmac
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.routers.email_tracking_api import email_tracking_router
from core.utils.email_tracking import decode_tracking_token

SECRET = "leocdp-dev-tracking-secret"  # matches settings default


def _encode(tenant, campaign, profile, secret=SECRET):
    raw = f"{tenant}|{campaign}|{profile}"
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:20]
    payload = f"{raw}|{sig}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


class DecodeTokenTests(unittest.TestCase):
    def test_round_trip(self):
        token = _encode("t1", "c1", "p1")
        decoded = decode_tracking_token(token, secret=SECRET)
        self.assertEqual(decoded, {"tenant_id": "t1", "campaign_id": "c1", "master_profile_id": "p1"})

    def test_tampered_signature_rejected(self):
        token = _encode("t1", "c1", "p1")
        # Flip the last character to break the signature.
        broken = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertIsNone(decode_tracking_token(broken, secret=SECRET))

    def test_wrong_secret_rejected(self):
        token = _encode("t1", "c1", "p1", secret="other")
        self.assertIsNone(decode_tracking_token(token, secret=SECRET))

    def test_garbage_rejected(self):
        self.assertIsNone(decode_tracking_token("not-a-token", secret=SECRET))


class TrackingRouterTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(email_tracking_router)
        self.client = TestClient(self.app)
        self.token = _encode("t1", "c1", "p1")

        self.rec_patcher = patch("core.routers.email_tracking_api.record_engagement_event", return_value="inserted")
        self.supp_patcher = patch("core.routers.email_tracking_api.add_suppression", return_value=True)
        self.email_patcher = patch("core.routers.email_tracking_api.resolve_recipient_email", return_value="x@x.io")
        self.mock_rec = self.rec_patcher.start()
        self.mock_supp = self.supp_patcher.start()
        self.mock_email = self.email_patcher.start()
        self.addCleanup(self.rec_patcher.stop)
        self.addCleanup(self.supp_patcher.stop)
        self.addCleanup(self.email_patcher.stop)

    def test_open_returns_gif_and_records(self):
        resp = self.client.get(f"/track/email/open?u={self.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/gif")
        self.assertEqual(self.mock_rec.call_args.args[3], "email-opened")

    def test_open_with_bad_token_still_returns_gif_no_record(self):
        resp = self.client.get("/track/email/open?u=bad")
        self.assertEqual(resp.status_code, 200)
        self.mock_rec.assert_not_called()

    def test_click_redirects_and_records(self):
        resp = self.client.get(
            f"/track/email/click?u={self.token}&url=https://shop.example/x",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "https://shop.example/x")
        self.assertEqual(self.mock_rec.call_args.args[3], "email-clicked")

    def test_click_rejects_non_http_scheme(self):
        resp = self.client.get(
            f"/track/email/click?u={self.token}&url=javascript:alert(1)",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "/")

    def test_unsubscribe_suppresses(self):
        resp = self.client.get(f"/track/email/unsubscribe?u={self.token}")
        self.assertEqual(resp.status_code, 200)
        self.mock_supp.assert_called_once()
        self.assertEqual(self.mock_supp.call_args.args[2], "unsubscribe")

    def test_webhook_hard_bounce_suppresses(self):
        resp = self.client.post(
            "/track/email/webhook?provider=ses",
            json={"token": self.token, "event": "bounce", "email": "b@x.io", "bounce_type": "hard"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["event"], "email-bounced")
        self.assertTrue(body["suppressed"])
        self.assertEqual(self.mock_supp.call_args.args[2], "hard_bounce")

    def test_webhook_soft_bounce_does_not_suppress(self):
        resp = self.client.post(
            "/track/email/webhook?provider=ses",
            json={"token": self.token, "event": "bounce", "email": "b@x.io", "bounce_type": "soft"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["suppressed"])
        self.mock_supp.assert_not_called()

    def test_webhook_unknown_event_ignored(self):
        resp = self.client.post(
            "/track/email/webhook", json={"token": self.token, "event": "teleport"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_webhook_bad_token_ignored(self):
        resp = self.client.post(
            "/track/email/webhook", json={"token": "bad", "event": "complaint"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")
        self.mock_supp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
