"""
Scope switches: personalisation can be turned off for one placement or one site
while staying on elsewhere, and the most specific scope wins.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise).
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

VISITOR = "visitor-scope-1"


@pytest.fixture(autouse=True)
def _require_tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.personalization_setting')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


@pytest.fixture
def shop(world_factory, test_engine, enable_personalisation):
    """One tenant, personalisation ON at tenant level, with two placements that each have an ad."""
    world = world_factory()  # placement "homepage_top"
    enable_personalisation(world)
    world.add_ad("ad_top")
    other_placement = test_engine.execute(
        text(
            "INSERT INTO leo_ads.placement (tenant_id, placement_key, name) "
            "VALUES (:t, 'article_side', 'article_side') RETURNING placement_id"
        ),
        {"t": world.tenant_id},
    ).scalar_one()
    test_engine.execute(
        text(
            "INSERT INTO leo_ads.ad (tenant_id, ad_key, creative_id, placement_id, status) "
            "VALUES (:t, 'ad_side', :c, :p, 'active')"
        ),
        {"t": world.tenant_id, "c": world.creative_id, "p": other_placement},
    )
    return world


def _scope(test_engine, world, scope_type, scope_ref, enabled):
    test_engine.execute(
        text(
            "INSERT INTO leo_ads.personalization_setting "
            "(tenant_id, scope_type, scope_ref, enabled, holdout_pct) "
            "VALUES (:t, :type, :ref, :enabled, 0) "
            "ON CONFLICT (tenant_id, scope_type, scope_ref) DO UPDATE SET enabled = EXCLUDED.enabled"
        ),
        {"t": world.tenant_id, "type": scope_type, "ref": scope_ref, "enabled": enabled},
    )


def _outcome(client, world, placement, leovid=VISITOR, **params):
    body = client.get(
        f"/serve/{placement}",
        params={"tenant": world.tenant_key, "leovid": leovid, **params},
    ).json()
    return body["personalisation"]["group"], body["personalisation"]["reason"]


def test_with_only_the_tenant_switch_both_placements_are_personalisable(serve_client, shop):
    assert _outcome(serve_client, shop, "homepage_top")[0] == "default_no_history"
    assert _outcome(serve_client, shop, "article_side")[0] == "default_no_history"


def test_a_placement_switched_off_is_default_disabled_for_everyone_while_another_stays_on(
    serve_client, test_engine, shop
):
    _scope(test_engine, shop, "placement", "article_side", False)

    for visitor in ("a", "b", "c"):
        assert _outcome(serve_client, shop, "article_side", visitor) == ("default_disabled", "disabled")
    assert _outcome(serve_client, shop, "homepage_top")[0] != "default_disabled"


def test_a_site_switch_overrides_the_tenant_level(serve_client, test_engine, shop):
    _scope(test_engine, shop, "site", "kiosk-site", False)

    assert _outcome(serve_client, shop, "homepage_top", site="kiosk-site")[0] == "default_disabled"
    assert _outcome(serve_client, shop, "homepage_top", site="other-site")[0] != "default_disabled"
    assert _outcome(serve_client, shop, "homepage_top")[0] != "default_disabled"  # no site given


def test_a_placement_switch_beats_a_site_switch(serve_client, test_engine, shop):
    _scope(test_engine, shop, "site", "kiosk-site", False)
    _scope(test_engine, shop, "placement", "homepage_top", True)

    assert _outcome(serve_client, shop, "homepage_top", site="kiosk-site")[0] != "default_disabled"
    assert _outcome(serve_client, shop, "article_side", site="kiosk-site")[0] == "default_disabled"


def test_a_switched_off_site_stores_no_visitor_key_for_events(serve_client, serve_app, test_engine, shop):
    _scope(test_engine, shop, "site", "kiosk-site", False)
    serve_client.post(
        "/events",
        json={
            "tenant": shop.tenant_key, "leovid": VISITOR, "site": "kiosk-site",
            "events": [{
                "event_type": "impression", "request_id": str(uuid4()), "ad_key": "ad_top",
                "event_time": datetime.now(timezone.utc).isoformat(),
            }],
        },
    )
    serve_app.event_writer.flush()

    keys = test_engine.execute(
        text("SELECT user_key_hash FROM leo_ads.ad_event WHERE tenant_id = :t"), {"t": shop.tenant_id}
    ).scalars().all()
    assert keys == [None]
