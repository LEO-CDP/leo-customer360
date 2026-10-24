"""
Security hardening of the personalisation endpoints (T097): stored page addresses
carry no query string or fragment, no log line contains a raw visitor id or a full
visitor key, and the per-visitor rate limits and cross-origin rules hold.

The first group is pure; the rest need PostgreSQL with personalization-schema.sql
applied (skipped otherwise).
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from core.config import db_promotions_engine, promotions_settings
from personalization.event_ingest import sanitise_page_url
from personalization.identity import visitor_key
from tests._pg import check_postgres_available

needs_pg = pytest.mark.skipif(
    not check_postgres_available(), reason="PostgreSQL with leo_ads schema required"
)

VISITOR = "raw-visitor-id-should-never-be-logged"


# --------------------------------------------------------------------------
# page_url
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given, expected",
    [
        ("https://shop.example.com/a/b?token=secret&email=a@b.com#frag", "https://shop.example.com/a/b"),
        ("http://shop.example.com:8080/p?x=1", "http://shop.example.com:8080/p"),
        ("https://user:pass@shop.example.com/p", "https://shop.example.com/p"),
        ("https://shop.example.com", "https://shop.example.com"),
        ("javascript:alert(1)", None),
        ("ftp://files.example.com/x", None),
        ("/relative/path?x=1", None),
        ("not a url", None),
        ("", None),
        (None, None),
    ],
)
def test_page_urls_lose_their_query_string_fragment_and_credentials(given, expected):
    assert sanitise_page_url(given) == expected


def test_a_very_long_path_is_bounded():
    assert len(sanitise_page_url("https://a.example.com/" + "x" * 5000)) <= 2048


# --------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------


def test_the_database_engine_hides_bound_parameters_in_errors():
    assert db_promotions_engine.hide_parameters is True


@pytest.fixture
def _tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.consent_record')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


def _event(request_id=None):
    return {
        "event_type": "impression", "request_id": request_id or str(uuid4()), "ad_key": "ad_one",
        "event_time": datetime.now(timezone.utc).isoformat(),
        "page_url": "https://shop.example.com/p?session=abc#top",
    }


@needs_pg
def test_no_log_line_contains_a_raw_visitor_id_or_a_full_visitor_key(
    _tables, serve_client, serve_app, test_engine, world_factory, enable_personalisation, caplog
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world)
    key_hex = visitor_key(world.tenant_id, VISITOR).hex()

    with caplog.at_level(logging.DEBUG):
        serve_client.put("/consent", json={"tenant": world.tenant_key, "leovid": VISITOR, "status": "granted"})
        serve_client.get(f"/serve/{world.placement_key}", params={"tenant": world.tenant_key, "leovid": VISITOR})
        serve_client.post("/events", json={"tenant": world.tenant_key, "leovid": VISITOR, "events": [_event()]})
        serve_client.put("/consent", json={"tenant": world.tenant_key, "leovid": VISITOR, "status": "denied"})
        serve_client.get(
            f"/serve/{world.placement_key}",
            params={"tenant": world.tenant_key, "leovid": VISITOR, "consent": "denied"},
        )

    # Server-side loggers only: the test client's own logger prints the URLs it calls.
    logged = "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith(("httpx", "httpcore"))
    )
    assert VISITOR not in logged
    assert key_hex not in logged
    assert "consent: tenant=" in logged  # the line that identifies a visitor does so by fingerprint only
    assert key_hex[:8] in logged


@needs_pg
def test_a_failing_profile_read_logs_no_visitor_key(
    _tables, serve_client, serve_app, world_factory, enable_personalisation, caplog
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world)
    key = visitor_key(world.tenant_id, VISITOR)

    class Broken:
        def get_profile(self, tenant_id, user_keys, timeout_ms=None):
            # A genuine database error raised by the application's own engine, with the
            # visitor key bound as a parameter, exactly as a real profile query would.
            with db_promotions_engine.connect() as connection:
                connection.execute(text("SELECT CAST(:k AS integer)"), {"k": user_keys[0].hex()})

        def linked_keys(self, tenant_id, user_key):
            return [user_key]

    serve_app.profile_repository = Broken()
    with caplog.at_level(logging.DEBUG):
        response = serve_client.get(
            f"/serve/{world.placement_key}", params={"tenant": world.tenant_key, "leovid": VISITOR, "consent": "granted"}
        )

    assert response.status_code == 200  # fail open
    assert response.json()["personalisation"]["group"] == "default_fallback"
    server_side = "\n".join(
        f"{r.getMessage()}\n{r.exc_text or ''}" for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))
    )
    assert "ranking failed" in server_side  # the failure was logged, with its traceback...
    assert key.hex() not in server_side  # ...but without the visitor key it was run for


def test_the_access_log_filter_redacts_the_visitor_id():
    from core.application import RedactVisitorIds

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5000", "GET", "/serve/homepage_top?tenant=demo&leovid=0f8f2e0a-7a5c&limit=5", "1.1", 200),
        None,
    )

    assert RedactVisitorIds().filter(record) is True
    line = record.getMessage()
    assert "0f8f2e0a" not in line
    assert "leovid=[redacted]&limit=5" in line
    assert "tenant=demo" in line  # everything else is kept


def test_the_filter_is_installed_on_the_access_logger():
    from core.application import PromotionsApplication, RedactVisitorIds

    PromotionsApplication().create_app()
    assert any(isinstance(f, RedactVisitorIds) for f in logging.getLogger("uvicorn.access").filters)


# --------------------------------------------------------------------------
# Stored events
# --------------------------------------------------------------------------


@needs_pg
def test_stored_events_keep_only_the_page_address(
    _tables, serve_client, serve_app, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world)

    serve_client.post("/events", json={"tenant": world.tenant_key, "leovid": VISITOR, "events": [_event()]})
    serve_app.event_writer.flush()

    stored = test_engine.execute(
        text("SELECT page_url FROM leo_ads.ad_event WHERE tenant_id = :t"), {"t": world.tenant_id}
    ).scalars().all()
    assert stored == ["https://shop.example.com/p"]


# --------------------------------------------------------------------------
# Limits and cross-origin rules
# --------------------------------------------------------------------------


@needs_pg
def test_events_and_consent_are_rate_limited_per_visitor(
    _tables, serve_client, world_factory, enable_personalisation, monkeypatch
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world)
    monkeypatch.setattr(promotions_settings, "event_rate_limit_per_minute", 2)
    monkeypatch.setattr(promotions_settings, "consent_rate_limit_per_minute", 2)

    events = [
        serve_client.post("/events", json={"tenant": world.tenant_key, "leovid": VISITOR, "events": [_event()]}).status_code
        for _ in range(3)
    ]
    consents = [
        serve_client.put("/consent", json={"tenant": world.tenant_key, "leovid": VISITOR, "status": "granted"}).status_code
        for _ in range(3)
    ]

    assert events == [202, 202, 429]
    assert consents == [204, 204, 429]


@needs_pg
def test_the_beacon_endpoints_allow_cross_origin_posts_without_credentials(_tables, serve_client, world_factory):
    world_factory()

    for method, path in (("POST", "/events"), ("PUT", "/consent")):
        preflight = serve_client.options(
            path,
            headers={
                "Origin": "https://publisher.example.org",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "*"
        # Credentials are never allowed: a wildcard origin with credentials would be unsafe.
        assert "access-control-allow-credentials" not in preflight.headers
