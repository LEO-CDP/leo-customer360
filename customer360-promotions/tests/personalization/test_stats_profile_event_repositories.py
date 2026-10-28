"""
Repository tests for statistics, profiles and events.

These need PostgreSQL with the leo_ads schema and personalization-schema.sql
applied (see tests/conftest.py); they are skipped otherwise. Each test runs in a
rolled-back transaction. ``merge_profiles`` is pure and always runs.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from repository.event_repository import EventRepository
from repository.profile_repository import ProfileRepository, merge_profiles
from repository.stats_repository import StatsRepository
from tests._pg import check_postgres_available

needs_pg = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

KEY_A = b"\x0a" * 16
KEY_B = b"\x0b" * 16


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# merge_profiles (pure)
# --------------------------------------------------------------------------


def test_merge_profiles_empty_is_none():
    assert merge_profiles([]) is None


def test_merge_profiles_sums_scores_counts_and_keeps_latest():
    first = {
        "affinities": {"advertiser": {"acme": {"score": 1.0, "n": 2, "last_at": "2026-10-01T00:00:00+00:00"}}},
        "recent_dismissed": {"7": "2026-10-02T00:00:00+00:00"},
        "event_count": 3,
        "last_event_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    second = {
        "affinities": {
            "advertiser": {"acme": {"score": 0.5, "n": 1, "last_at": "2026-10-05T00:00:00+00:00"}},
            "format": {"300x250": {"score": 2.0, "n": 1, "last_at": "2026-10-05T00:00:00+00:00"}},
        },
        "recent_dismissed": {"7": "2026-10-06T00:00:00+00:00"},
        "event_count": 4,
        "last_event_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
    }

    merged = merge_profiles([first, second])

    acme = merged["affinities"]["advertiser"]["acme"]
    assert acme["score"] == pytest.approx(1.5)
    assert acme["n"] == 3
    assert acme["last_at"] == "2026-10-05T00:00:00+00:00"
    assert merged["affinities"]["format"]["300x250"]["score"] == 2.0
    assert merged["recent_dismissed"] == {"7": "2026-10-06T00:00:00+00:00"}
    assert merged["event_count"] == 7
    assert merged["last_event_at"] == datetime(2026, 10, 5, tzinfo=timezone.utc)


def test_merge_profiles_does_not_mutate_inputs():
    row = {
        "affinities": {"advertiser": {"acme": {"score": 1.0, "n": 1, "last_at": "2026-10-01T00:00:00+00:00"}}},
        "event_count": 1,
    }
    merge_profiles([row, row])
    assert row["affinities"]["advertiser"]["acme"]["score"] == 1.0


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


@needs_pg
def test_stats_sum_groups_and_respect_window_and_tenant(test_engine, two_tenants):
    first, second = two_tenants
    ad_id = first.add_ad("ad_stats")
    other_ad = second.add_ad("ad_stats")
    hour = _now().replace(minute=0, second=0, microsecond=0)

    def stat(world, ad, at, group, imp, views, clicks, conv):
        test_engine.execute(
            text(
                """
                INSERT INTO leo_ads.ad_stat_hourly
                    (tenant_id, ad_id, hour_start, group_code,
                     impressions, views, clicks, conversions)
                VALUES (:t, :ad, :h, :g, :i, :v, :c, :cv)
                """
            ),
            {"t": world.tenant_id, "ad": ad, "h": at, "g": group,
             "i": imp, "v": views, "c": clicks, "cv": conv},
        )

    stat(first, ad_id, hour, "personalised", 100, 60, 5, 1)
    stat(first, ad_id, hour, "holdout", 50, 30, 2, 0)
    stat(first, ad_id, hour - timedelta(days=30), "default", 999, 999, 999, 999)  # outside window
    stat(second, other_ad, hour, "personalised", 7, 7, 7, 7)  # other tenant

    stats = StatsRepository(engine=test_engine).get_ad_stats(first.tenant_id, [ad_id, other_ad])

    assert set(stats) == {ad_id}
    assert (stats[ad_id].impressions, stats[ad_id].views, stats[ad_id].clicks, stats[ad_id].conversions) == (
        150, 90, 7, 1,
    )


@needs_pg
def test_stats_empty_ids_and_unknown_ads(test_engine, world_factory):
    world = world_factory()
    repo = StatsRepository(engine=test_engine)
    assert repo.get_ad_stats(world.tenant_id, []) == {}
    assert repo.get_ad_stats(world.tenant_id, [123456789]) == {}


# --------------------------------------------------------------------------
# Profiles and serving keys
# --------------------------------------------------------------------------


@needs_pg
def test_profile_upsert_roundtrip_and_version_bump(test_engine, world_factory):
    world = world_factory()
    repo = ProfileRepository(engine=test_engine)
    affinities = {"advertiser": {"acme": {"score": 1.2, "n": 3, "last_at": "2026-10-01T00:00:00+00:00"}}}

    assert repo.get_profile(world.tenant_id, [KEY_A]) is None

    repo.upsert_profile(world.tenant_id, KEY_A, affinities=affinities, recent_dismissed={"9": "2026-10-02T00:00:00+00:00"}, event_count=3)
    repo.upsert_profile(world.tenant_id, KEY_A, affinities=affinities, event_count=4)

    profile = repo.get_profile(world.tenant_id, [KEY_A])
    assert profile["affinities"] == affinities
    assert profile["event_count"] == 4
    assert profile["recent_dismissed"] == {}
    version = test_engine.execute(
        text(
            "SELECT profile_version FROM leo_ads.visitor_behaviour_profile "
            "WHERE tenant_id = :t AND user_key_hash = :k"
        ),
        {"t": world.tenant_id, "k": KEY_A},
    ).scalar_one()
    assert version == 2


@needs_pg
def test_profile_read_merges_linked_keys_and_is_tenant_scoped(test_engine, two_tenants):
    first, second = two_tenants
    repo = ProfileRepository(engine=test_engine)
    entry = {"advertiser": {"acme": {"score": 1.0, "n": 1, "last_at": "2026-10-01T00:00:00+00:00"}}}

    repo.upsert_profile(first.tenant_id, KEY_A, affinities=entry, event_count=1)
    repo.upsert_profile(first.tenant_id, KEY_B, affinities=entry, event_count=2)
    repo.upsert_profile(second.tenant_id, KEY_A, affinities=entry, event_count=50)

    merged = repo.get_profile(first.tenant_id, [KEY_A, KEY_B])
    assert merged["affinities"]["advertiser"]["acme"]["score"] == pytest.approx(2.0)
    assert merged["event_count"] == 3
    # The other tenant's row for the same key is never read.
    assert repo.get_profile(second.tenant_id, [KEY_A])["event_count"] == 50


@needs_pg
def test_serving_key_links_and_linked_keys(test_engine, world_factory):
    world = world_factory()
    repo = ProfileRepository(engine=test_engine)

    # Unlinked key: just itself.
    repo.upsert_serving_key(world.tenant_id, KEY_A)
    assert repo.linked_keys(world.tenant_id, KEY_A) == [KEY_A]

    repo.upsert_serving_key(world.tenant_id, KEY_A, profile_ref="master-1")
    repo.upsert_serving_key(world.tenant_id, KEY_B, profile_ref="master-1")
    # A later touch without a ref keeps the existing link.
    repo.upsert_serving_key(world.tenant_id, KEY_A)

    assert sorted(repo.linked_keys(world.tenant_id, KEY_A)) == sorted([KEY_A, KEY_B])


@needs_pg
def test_serving_key_cannot_be_taken_over_by_another_tenant(test_engine, two_tenants):
    first, second = two_tenants
    repo = ProfileRepository(engine=test_engine)

    repo.upsert_serving_key(first.tenant_id, KEY_A, profile_ref="mine")
    repo.upsert_serving_key(second.tenant_id, KEY_A, profile_ref="theirs")

    row = test_engine.execute(
        text("SELECT tenant_id, profile_ref FROM leo_ads.user_serving_key WHERE user_key_hash = :k"),
        {"k": KEY_A},
    ).one()
    assert (row.tenant_id, row.profile_ref) == (first.tenant_id, "mine")


@needs_pg
def test_profile_delete_for_key_only_touches_that_tenant_and_key(test_engine, two_tenants):
    first, second = two_tenants
    repo = ProfileRepository(engine=test_engine)
    for world in (first, second):
        repo.upsert_profile(world.tenant_id, KEY_A, affinities={}, event_count=1)
    repo.upsert_profile(first.tenant_id, KEY_B, affinities={}, event_count=1)
    repo.upsert_serving_key(first.tenant_id, KEY_A)

    counts = repo.delete_for_key(first.tenant_id, KEY_A)

    assert counts == {"visitor_behaviour_profile": 1, "user_serving_key": 1}
    assert repo.get_profile(first.tenant_id, [KEY_A]) is None
    assert repo.get_profile(first.tenant_id, [KEY_B]) is not None
    assert repo.get_profile(second.tenant_id, [KEY_A]) is not None


# --------------------------------------------------------------------------
# Events and decisions
# --------------------------------------------------------------------------


def _event(world, ad_id, event_type="impression", *, at=None, key=KEY_A, event_id=None, **extra):
    return {
        "event_id": str(event_id or uuid4()),
        "event_time": at or _now(),
        "tenant_id": world.tenant_id,
        "event_type": event_type,
        "ad_id": ad_id,
        "placement_id": world.placement_id,
        "user_key_hash": key,
        "request_id": str(uuid4()),
        **extra,
    }


@needs_pg
def test_insert_events_is_idempotent_on_event_id_and_time(test_engine, world_factory):
    world = world_factory()
    ad_id = world.add_ad("ad_ev")
    repo = EventRepository(engine=test_engine)
    event = _event(world, ad_id, payload={"dwell_ms": 1500})

    assert repo.insert_events([event]) == 1
    assert repo.insert_events([event]) == 0

    count = test_engine.execute(
        text("SELECT count(*) FROM leo_ads.ad_event WHERE tenant_id = :t"), {"t": world.tenant_id}
    ).scalar_one()
    assert count == 1
    assert repo.insert_events([]) == 0


@needs_pg
def test_insert_events_stores_flags_payload_and_anonymous_rows(test_engine, world_factory):
    world = world_factory()
    ad_id = world.add_ad("ad_flags")
    repo = EventRepository(engine=test_engine)

    repo.insert_events([
        _event(world, ad_id, "click", flags=["bot"], payload={"x": 1}),
        _event(world, ad_id, "impression", key=None),
    ])

    rows = test_engine.execute(
        text("SELECT event_type, flags, payload, user_key_hash FROM leo_ads.ad_event WHERE tenant_id = :t"),
        {"t": world.tenant_id},
    ).mappings().all()
    by_type = {r["event_type"]: r for r in rows}
    assert by_type["click"]["flags"] == ["bot"]
    assert by_type["click"]["payload"] == {"x": 1}
    assert by_type["impression"]["user_key_hash"] is None
    assert by_type["impression"]["flags"] == []


@needs_pg
def test_recent_events_excludes_flagged_other_keys_other_tenants_and_old(test_engine, two_tenants):
    first, second = two_tenants
    ad_first = first.add_ad("ad_recent")
    ad_second = second.add_ad("ad_recent")
    repo = EventRepository(engine=test_engine)
    now = _now()

    repo.insert_events([
        _event(first, ad_first, "view", at=now - timedelta(minutes=1)),
        _event(first, ad_first, "click", at=now - timedelta(minutes=2), flags=["bot"]),
        _event(first, ad_first, "impression", at=now - timedelta(minutes=3), key=KEY_B),
        _event(first, ad_first, "impression", at=now - timedelta(days=40)),
        _event(second, ad_second, "impression", at=now - timedelta(minutes=1)),
    ])

    events = repo.recent_events(first.tenant_id, [KEY_A], since=now - timedelta(days=14))

    assert [e["event_type"] for e in events] == ["view"]
    assert repo.recent_events(first.tenant_id, [], since=now) == []


@needs_pg
def test_decisions_insert_and_idempotent(test_engine, world_factory):
    world = world_factory()
    repo = EventRepository(engine=test_engine)
    decision = {
        "request_id": str(uuid4()),
        "decided_at": _now(),
        "tenant_id": world.tenant_id,
        "user_key_hash": KEY_A,
        "placement_id": world.placement_id,
        "group_code": "personalised",
        "chosen": [{"ad_id": 1, "rank": 1, "score": 1.4, "signals": []}],
        "candidate_count": 3,
    }

    assert repo.insert_decisions([decision]) == 1
    assert repo.insert_decisions([decision]) == 0
    assert repo.insert_decisions([]) == 0

    row = test_engine.execute(
        text("SELECT group_code, chosen, candidate_count FROM leo_ads.serving_decision WHERE tenant_id = :t"),
        {"t": world.tenant_id},
    ).one()
    assert row.group_code == "personalised"
    assert row.chosen[0]["ad_id"] == 1
    assert row.candidate_count == 3


@needs_pg
def test_event_delete_for_key_removes_events_and_decisions_for_that_tenant_only(test_engine, two_tenants):
    first, second = two_tenants
    ad_first = first.add_ad("ad_erase")
    ad_second = second.add_ad("ad_erase")
    repo = EventRepository(engine=test_engine)
    repo.insert_events([
        _event(first, ad_first), _event(first, ad_first, key=KEY_B), _event(second, ad_second),
    ])
    repo.insert_decisions([{
        "request_id": str(uuid4()), "decided_at": _now(), "tenant_id": first.tenant_id,
        "user_key_hash": KEY_A, "group_code": "holdout",
    }])

    counts = repo.delete_for_key(first.tenant_id, KEY_A)

    assert counts == {"ad_event": 1, "serving_decision": 1}
    since = _now() - timedelta(days=1)
    assert repo.recent_events(first.tenant_id, [KEY_A], since=since) == []
    assert repo.recent_events(first.tenant_id, [KEY_B], since=since)
    assert repo.recent_events(second.tenant_id, [KEY_A], since=since)
