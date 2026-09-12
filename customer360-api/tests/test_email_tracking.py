"""Tests for email tracking: token verification + the public
open/click/unsubscribe/webhook routes (DB writes mocked)."""

import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.routers.email_tracking_api import email_tracking_router
from core.utils.email_tracking import decode_tracking_token

SECRET = "leocdp-dev-tracking-secret"  # matches settings default (email_tracking_secret)


def _encode(tenant, campaign, profile, secret=SECRET):
    raw = f"{tenant}|{campaign}|{profile}"
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:20]
    payload = f"{raw}|{sig}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _sign_url(url, secret=SECRET):
    """The click-URL signature (k=) the engine mints and the endpoint verifies."""
    return hmac.new(secret.encode(), url.encode(), hashlib.sha256).hexdigest()[:20]


def _webhook_sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


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
        self.webhook_secret = "test-webhook-secret"

        self.rec_patcher = patch("core.routers.email_tracking_api.record_engagement_event", return_value="inserted")
        self.supp_patcher = patch("core.routers.email_tracking_api.add_suppression", return_value=True)
        self.email_patcher = patch("core.routers.email_tracking_api.resolve_recipient_email", return_value="x@x.io")
        # Webhook is signature-gated; configure a secret for the tests that exercise it.
        self.secret_patcher = patch.object(settings, "email_webhook_signing_secret", self.webhook_secret)
        self.mock_rec = self.rec_patcher.start()
        self.mock_supp = self.supp_patcher.start()
        self.mock_email = self.email_patcher.start()
        self.secret_patcher.start()
        self.addCleanup(self.rec_patcher.stop)
        self.addCleanup(self.supp_patcher.stop)
        self.addCleanup(self.email_patcher.stop)
        self.addCleanup(self.secret_patcher.stop)

    def _post_webhook(self, payload, provider="generic", sign=True, secret=None):
        body = json.dumps(payload).encode()
        headers = {"content-type": "application/json"}
        if sign:
            headers["X-Webhook-Signature"] = _webhook_sig(body, secret or self.webhook_secret)
        return self.client.post(f"/track/email/webhook?provider={provider}", content=body, headers=headers)

    def test_open_returns_gif_and_records(self):
        resp = self.client.get(f"/track/email/open?u={self.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/gif")
        self.assertEqual(self.mock_rec.call_args.args[3], "email-opened")

    def test_open_with_bad_token_still_returns_gif_no_record(self):
        resp = self.client.get("/track/email/open?u=bad")
        self.assertEqual(resp.status_code, 200)
        self.mock_rec.assert_not_called()

    def test_open_without_token_returns_gif(self):
        # L4: a stripped pixel URL must return the GIF, not a 422.
        resp = self.client.get("/track/email/open")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/gif")

    def test_click_redirects_with_valid_signature(self):
        url = "https://shop.example/x"
        resp = self.client.get(
            f"/track/email/click?u={self.token}&url={url}&k={_sign_url(url)}",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], url)
        self.assertEqual(self.mock_rec.call_args.args[3], "email-clicked")

    def test_click_open_redirect_blocked_without_valid_signature(self):
        # H1: attacker swaps url= to their host; without a matching k it won't redirect there.
        resp = self.client.get(
            f"/track/email/click?u={self.token}&url=https://evil.example/phish&k=forged",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "/")

    def test_click_rejects_non_http_scheme(self):
        url = "javascript:alert(1)"
        resp = self.client.get(
            f"/track/email/click?u={self.token}&url={url}&k={_sign_url(url)}",
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
        resp = self._post_webhook(
            {"token": self.token, "event": "bounce", "email": "b@x.io", "bounce_type": "hard"}, provider="ses"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["event"], "email-bounced")
        self.assertTrue(body["suppressed"])
        # B1: address resolved from OUR ledger (x@x.io), NOT the payload's b@x.io.
        self.assertEqual(self.mock_supp.call_args.args[1], "x@x.io")
        self.assertEqual(self.mock_supp.call_args.args[2], "hard_bounce")

    def test_webhook_soft_bounce_does_not_suppress(self):
        resp = self._post_webhook({"token": self.token, "event": "bounce", "bounce_type": "soft"}, provider="ses")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["suppressed"])
        self.mock_supp.assert_not_called()

    def test_webhook_unknown_event_ignored(self):
        resp = self._post_webhook({"token": self.token, "event": "teleport"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")

    def test_webhook_bad_token_ignored(self):
        resp = self._post_webhook({"token": "bad", "event": "complaint"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ignored")
        self.mock_supp.assert_not_called()

    def test_webhook_rejects_unsigned(self):
        # B1: no signature -> 401, no suppression.
        resp = self._post_webhook({"token": self.token, "event": "complaint"}, sign=False)
        self.assertEqual(resp.status_code, 401)
        self.mock_supp.assert_not_called()

    def test_webhook_rejects_wrong_signature(self):
        resp = self._post_webhook({"token": self.token, "event": "complaint"}, secret="wrong-secret")
        self.assertEqual(resp.status_code, 401)
        self.mock_supp.assert_not_called()

    def test_webhook_disabled_when_secret_unset(self):
        # Fail closed: no configured secret -> endpoint disabled (503).
        with patch.object(settings, "email_webhook_signing_secret", ""):
            resp = self._post_webhook({"token": self.token, "event": "complaint"}, sign=False)
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
