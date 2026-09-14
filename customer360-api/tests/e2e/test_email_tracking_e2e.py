"""SCRUM-98 E2E -- public email tracking on data-tracking-api.

These tests are opt-in through E2E_TRACKING_BASE_URL and use synthetic tokens,
so they do not require customer360 database rows or bearer authentication.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_TRACKING_BASE_URL"),
    reason="E2E_TRACKING_BASE_URL not set (see tests/e2e/README.md)",
)


@pytest.fixture(autouse=True)
def _require_email_feature(email_feature):
    """Skip the module when the target has no public tracking routes."""


@pytest.mark.case("S98-01")
def test_open_pixel_returns_gif(email_client, email_p, mint_token):
    response = email_client.get(
        email_p("/track/email/open"),
        params={"u": mint_token("t", "c", "p")},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"


@pytest.mark.case("S98-01")
def test_open_pixel_handles_missing_token(email_client, email_p):
    response = email_client.get(email_p("/track/email/open"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"


@pytest.mark.case("S98-02")
def test_click_redirects_to_signed_url(email_client, email_p, mint_token, sign_click_url):
    url = "https://example.test/offer"
    response = email_client.get(
        email_p("/track/email/click"),
        params={
            "u": mint_token("t", "c", "p"),
            "url": url,
            "k": sign_click_url(url),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == url


@pytest.mark.case("S98-03")
def test_click_rejects_unsigned_redirect(email_client, email_p, mint_token):
    response = email_client.get(
        email_p("/track/email/click"),
        params={
            "u": mint_token("t", "c", "p"),
            "url": "https://evil.test/phish",
            "k": "forged",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/"


@pytest.mark.case("S98-04")
def test_webhook_rejects_unsigned_callback(email_client, email_p, mint_token):
    response = email_client.post(
        email_p("/track/email/webhook"),
        json={"token": mint_token("t", "c", "p"), "event": "complaint"},
    )
    assert response.status_code in (401, 503)


@pytest.mark.case("S98-05")
def test_unsubscribe_confirms_for_valid_token(email_client, email_p, mint_token):
    response = email_client.get(
        email_p("/track/email/unsubscribe"),
        params={"u": mint_token("t", "c", "p")},
    )
    assert response.status_code == 200
    assert "unsubscrib" in response.text.lower()


@pytest.mark.case("S98-05")
def test_unsubscribe_rejects_invalid_token(email_client, email_p):
    response = email_client.get(
        email_p("/track/email/unsubscribe"),
        params={"u": "not-a-valid-token"},
    )
    assert response.status_code == 400
