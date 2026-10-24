"""
Cross-device history: a known visitor's profile is the union of the profiles of
every key currently linked to their master profile, computed when it is read.
Unlinking removes a key's contribution without rewriting any stored row.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise).
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from personalization.identity import visitor_key
from repository.profile_repository import ProfileRepository
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

PHONE = "cookie-phone"
LAPTOP = "cookie-laptop"
STRANGER = "cookie-stranger"


@pytest.fixture(autouse=True)
def _require_tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.user_serving_key')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


@pytest.fixture
def shop(world_factory, enable_personalisation, test_engine):
    world = world_factory()
    enable_personalisation(world)
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for key, tag in (("ad_travel", "travel"), ("ad_sports", "sports")):
        ad_id = world.add_ad(key)
        test_engine.execute(
            text("UPDATE leo_ads.ad SET metadata = CAST(:m AS jsonb) WHERE ad_id = :id"),
            {"m": json.dumps({"tags": [tag]}), "id": ad_id},
        )
        test_engine.execute(
            text(
                "INSERT INTO leo_ads.ad_stat_hourly (tenant_id, ad_id, hour_start, group_code, impressions, views, clicks, conversions) "
                "VALUES (:t, :ad, :h, 'default', 100000, 50000, 2000, 200)"
            ),
            {"t": world.tenant_id, "ad": ad_id, "h": hour},
        )
    return world


def _profile(test_engine, world, cookie, tag, score=4.0):
    ProfileRepository(engine=test_engine).upsert_profile(
        world.tenant_id,
        visitor_key(world.tenant_id, cookie),
        affinities={"tag": {tag: {"score": score, "n": 5, "last_at": datetime.now(timezone.utc).isoformat()}}},
        event_count=5,
    )


def _link(test_engine, world, cookie, profile_ref):
    ProfileRepository(engine=test_engine).upsert_serving_key(
        world.tenant_id, visitor_key(world.tenant_id, cookie), profile_ref
    )


def _unlink(test_engine, world, cookie, hot_state):
    key = visitor_key(world.tenant_id, cookie)
    test_engine.execute(
        text("UPDATE leo_ads.user_serving_key SET profile_ref = NULL WHERE user_key_hash = :k"), {"k": key}
    )
    # Each device caches its own view of the link set for up to a minute; let them all expire.
    for cookie_id in (PHONE, LAPTOP, STRANGER):
        hot_state.delete(world.tenant_id, "linked", visitor_key(world.tenant_id, cookie_id))


def _serve(client, world, cookie):
    body = client.get(
        f"/serve/{world.placement_key}",
        params={"tenant": world.tenant_key, "leovid": cookie, "limit": 5},
    ).json()
    return body["personalisation"]["group"], [a["adId"] for a in body["ads"]]


# --------------------------------------------------------------------------
# The repository
# --------------------------------------------------------------------------


def test_the_profile_view_is_the_sum_of_the_linked_keys(test_engine, world_factory):
    world = world_factory()
    _profile(test_engine, world, PHONE, "travel", 2.0)
    _profile(test_engine, world, LAPTOP, "travel", 1.0)
    keys = [visitor_key(world.tenant_id, PHONE), visitor_key(world.tenant_id, LAPTOP)]

    view = ProfileRepository(engine=test_engine).get_profile(world.tenant_id, keys)

    assert view["affinities"]["tag"]["travel"]["score"] == pytest.approx(3.0)
    assert view["event_count"] == 10


def test_linked_keys_lists_every_device_of_a_master_profile(test_engine, world_factory):
    world = world_factory()
    for cookie in (PHONE, LAPTOP):
        _link(test_engine, world, cookie, "master-1")
    _link(test_engine, world, STRANGER, "master-2")

    keys = ProfileRepository(engine=test_engine).linked_keys(world.tenant_id, visitor_key(world.tenant_id, PHONE))

    assert sorted(keys) == sorted([visitor_key(world.tenant_id, PHONE), visitor_key(world.tenant_id, LAPTOP)])


# --------------------------------------------------------------------------
# In ranking
# --------------------------------------------------------------------------


def test_history_from_another_device_personalises_a_visitor_with_none_of_their_own(
    serve_client, test_engine, shop
):
    _profile(test_engine, shop, PHONE, "travel")  # only the phone has history
    _link(test_engine, shop, PHONE, "master-1")
    _link(test_engine, shop, LAPTOP, "master-1")

    group, ads = _serve(serve_client, shop, LAPTOP)

    assert group == "personalised"
    assert ads[0] == "ad_travel"


def test_an_unlinked_device_gets_nothing_from_the_other(serve_client, test_engine, hot_state, shop):
    _profile(test_engine, shop, PHONE, "travel")
    _link(test_engine, shop, PHONE, "master-1")
    _link(test_engine, shop, LAPTOP, "master-1")
    assert _serve(serve_client, shop, LAPTOP)[0] == "personalised"

    _unlink(test_engine, shop, PHONE, hot_state)

    assert _serve(serve_client, shop, LAPTOP)[0] == "default_no_history"


def test_unlinking_leaves_every_stored_profile_row_untouched(serve_client, test_engine, hot_state, shop):
    _profile(test_engine, shop, PHONE, "travel")
    _profile(test_engine, shop, LAPTOP, "sports")
    _link(test_engine, shop, PHONE, "master-1")
    _link(test_engine, shop, LAPTOP, "master-1")

    def stored():
        return test_engine.execute(
            text(
                "SELECT user_key_hash, affinities, event_count, profile_version, updated_at "
                "FROM leo_ads.visitor_behaviour_profile WHERE tenant_id = :t ORDER BY user_key_hash"
            ),
            {"t": shop.tenant_id},
        ).all()

    before = stored()
    _serve(serve_client, shop, LAPTOP)
    _unlink(test_engine, shop, PHONE, hot_state)
    _serve(serve_client, shop, LAPTOP)

    assert stored() == before  # nothing merged into, or removed from, any stored row


def test_after_unlinking_the_laptop_is_ranked_on_its_own_history_only(serve_client, test_engine, hot_state, shop):
    _profile(test_engine, shop, PHONE, "travel", 8.0)  # strong
    _profile(test_engine, shop, LAPTOP, "sports", 1.0)  # weak
    _link(test_engine, shop, PHONE, "master-1")
    _link(test_engine, shop, LAPTOP, "master-1")
    assert _serve(serve_client, shop, LAPTOP)[1][0] == "ad_travel"

    _unlink(test_engine, shop, PHONE, hot_state)

    assert _serve(serve_client, shop, LAPTOP)[1][0] == "ad_sports"


def test_a_stranger_is_unaffected_by_someone_elses_links(serve_client, test_engine, shop):
    _profile(test_engine, shop, PHONE, "travel")
    _link(test_engine, shop, PHONE, "master-1")
    _link(test_engine, shop, LAPTOP, "master-1")

    assert _serve(serve_client, shop, STRANGER)[0] == "default_no_history"


def test_links_never_cross_tenants(serve_client, test_engine, world_factory, enable_personalisation):
    mine, theirs = world_factory(), world_factory()
    for world in (mine, theirs):
        enable_personalisation(world)
        world.add_ad("ad_one")
    _profile(test_engine, theirs, PHONE, "travel")
    _link(test_engine, theirs, PHONE, "master-1")

    # The same cookie ids under another tenant derive different keys: no history, no link.
    assert _serve(serve_client, mine, PHONE)[0] == "default_no_history"
