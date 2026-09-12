"""SCRUM-98 E2E -- public email tracking + webhook + compliance, against a live
deployment (UAT). Skipped unless E2E_BASE_URL is set.

These hit the PUBLIC endpoints (open pixel, click redirect, unsubscribe, webhook)
with tokens minted for SYNTHETIC random (tenant, campaign, profile) ids, so they
exercise the contract + the security fixes (open-redirect blocked, webhook
fail-closed) without touching real tenant data: a random profile has no
cdp_profile_links row, so events are skipped and no suppression is written.

Covers TEST_PLAN.md S98-01..05. Requires E2E_EMAIL_TRACKING_SECRET to match the
deployment's EMAIL_TRACKING_SECRET (defaults to the UAT/dev default).
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_BASE_URL"), reason="E2E_BASE_URL not set (see tests/e2e/README.md)"
)

_REDIRECT = (301, 302, 307, 308)


@pytest.fixture(autouse=True)
def _require_email_feature(email_feature):
    """Skip this module unless the target deploys the SCRUM-98 tracking endpoints."""


def _synthetic():
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())  # tenant, campaign, profile


# --- S98-01 open pixel ----------------------------------------------------
@pytest.mark.case("S98-01")
def test_open_pixel_returns_gif(unauth_client, p, mint_token):
    t, c, pr = _synthetic()
    r = unauth_client.get(p("/track/email/open"), params={"u": mint_token(t, c, pr)})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/gif"


@pytest.mark.case("S98-01")
def test_open_pixel_resilient_to_missing_or_bad_token(unauth_client, p):
    # Must always return the GIF -- never 422/500 to a mail client.
    assert unauth_client.get(p("/track/email/open")).status_code == 200
    assert unauth_client.get(p("/track/email/open"), params={"u": "garbage"}).status_code == 200


# --- S98-02 click redirect (positive) -------------------------------------
@pytest.mark.case("S98-02")
def test_click_redirects_to_signed_url(unauth_client, p, mint_token, sign_click_url):
    t, c, pr = _synthetic()
    url = "https://example.com/offer"
    r = unauth_client.get(
        p("/track/email/click"),
        params={"u": mint_token(t, c, pr), "url": url, "k": sign_click_url(url)},
        follow_redirects=False,
    )
    assert r.status_code in _REDIRECT, r.text
    assert r.headers["location"] == url


# --- S98-03 open-redirect blocked (H1 fix) --------------------------------
@pytest.mark.case("S98-03")
def test_click_open_redirect_blocked_without_valid_signature(unauth_client, p, mint_token):
    t, c, pr = _synthetic()
    r = unauth_client.get(
        p("/track/email/click"),
        params={"u": mint_token(t, c, pr), "url": "https://evil.example/phish", "k": "forged"},
        follow_redirects=False,
    )
    assert r.status_code in _REDIRECT
    assert r.headers["location"] == "/"  # refused to redirect to the unsigned destination


@pytest.mark.case("S98-03")
def test_click_rejects_non_http_scheme(unauth_client, p, mint_token, sign_click_url):
    t, c, pr = _synthetic()
    url = "javascript:alert(1)"
    r = unauth_client.get(
        p("/track/email/click"),
        params={"u": mint_token(t, c, pr), "url": url, "k": sign_click_url(url)},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


# --- S98-04 webhook is authenticated / fails closed (B1 fix) --------------
@pytest.mark.case("S98-04")
def test_webhook_forged_callback_is_rejected(unauth_client, p, mint_token):
    t, c, pr = _synthetic()
    # A valid (public) tracking token but NO provider signature must never be
    # accepted: 401 (bad signature) or 503 (webhook disabled -- no secret set).
    # It must NEVER 200 + suppress an attacker-supplied address.
    r = unauth_client.post(
        p("/track/email/webhook"),
        json={"token": mint_token(t, c, pr), "event": "complaint", "email": "victim@example.com"},
    )
    assert r.status_code in (401, 403, 503), r.text
    assert r.status_code != 200


# --- S98-05 unsubscribe ---------------------------------------------------
@pytest.mark.case("S98-05")
def test_unsubscribe_confirms_for_valid_token(unauth_client, p, mint_token):
    t, c, pr = _synthetic()
    r = unauth_client.get(p("/track/email/unsubscribe"), params={"u": mint_token(t, c, pr)})
    assert r.status_code == 200
    assert "unsubscrib" in r.text.lower()


@pytest.mark.case("S98-05")
def test_unsubscribe_rejects_invalid_token(unauth_client, p):
    r = unauth_client.get(p("/track/email/unsubscribe"), params={"u": "not-a-valid-token"})
    assert r.status_code == 400
