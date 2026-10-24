"""Tests for the hourly ad statistics job (database group is opt-in; see conftest)."""

from datetime import timedelta

import pytest
from conftest import NOW, SCHEMA, requires_db

from ad_personalization import ad_stats

HOUR = NOW.replace(minute=0)


def _refresh(db, **kwargs):
    return ad_stats.refresh_ad_stats(db, schema=SCHEMA, now=NOW, **kwargs)


def test_floor_hour_truncates_in_utc():
    assert ad_stats.floor_hour(NOW) == HOUR


@requires_db
def test_counts_are_split_by_event_type_and_group(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    personalised = tenant.event(ad, "impression", NOW)
    tenant.decision(personalised, "personalised", NOW - timedelta(seconds=2))
    tenant.event(ad, "view", NOW, request_id=personalised)
    tenant.event(ad, "click", NOW, request_id=personalised)
    held_out = tenant.event(ad, "impression", NOW)
    tenant.decision(held_out, "holdout", NOW - timedelta(seconds=2))
    undecided = tenant.event(ad, "impression", NOW)
    fallback = tenant.event(ad, "impression", NOW)
    tenant.decision(fallback, "default_fallback", NOW - timedelta(seconds=2))

    _refresh(db)

    stats = tenant.stats()
    assert stats[(ad, HOUR, "personalised")] == (1, 1, 1, 0)
    assert stats[(ad, HOUR, "holdout")] == (1, 0, 0, 0)
    assert stats[(ad, HOUR, "default")] == (2, 0, 0, 0)  # no decision, and default_fallback
    assert undecided  # request ids are only used for the join


@requires_db
def test_view_through_conversions_count_as_conversions(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    tenant.event(ad, "conversion", NOW)
    tenant.event(ad, "view_through_conversion", NOW)

    _refresh(db)

    assert tenant.stats()[(ad, HOUR, "default")] == (0, 0, 0, 2)


@requires_db
def test_flagged_events_are_excluded(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    tenant.event(ad, "impression", NOW)
    tenant.event(ad, "impression", NOW, flags=["bot"])
    tenant.event(ad, "click", NOW, flags=["orphan_click"])

    _refresh(db)

    assert tenant.stats()[(ad, HOUR, "default")] == (1, 0, 0, 0)


@requires_db
def test_events_fall_into_their_own_hour(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    tenant.event(ad, "impression", HOUR + timedelta(minutes=5))
    tenant.event(ad, "impression", HOUR - timedelta(minutes=5))

    _refresh(db)

    stats = tenant.stats()
    assert stats[(ad, HOUR, "default")] == (1, 0, 0, 0)
    assert stats[(ad, HOUR - timedelta(hours=1), "default")] == (1, 0, 0, 0)


@requires_db
def test_a_decision_more_than_a_day_before_the_event_is_not_used(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    stale = tenant.event(ad, "impression", NOW)
    tenant.decision(stale, "personalised", NOW - timedelta(days=1, minutes=1))
    recent = tenant.event(ad, "impression", NOW)
    tenant.decision(recent, "personalised", NOW - timedelta(hours=23))

    _refresh(db)

    stats = tenant.stats()
    assert stats[(ad, HOUR, "personalised")] == (1, 0, 0, 0)
    assert stats[(ad, HOUR, "default")] == (1, 0, 0, 0)


@requires_db
def test_a_device_clock_slightly_behind_the_server_still_matches_its_decision(db, tenant_factory):
    # event_time is the visitor's own clock, so an impression can be stamped a little BEFORE the
    # server's decision for the same request. It must still count towards that decision's group.
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    slow = tenant.event(ad, "impression", NOW - timedelta(seconds=30))
    tenant.decision(slow, "personalised", NOW - timedelta(seconds=25))
    far_ahead = tenant.event(ad, "impression", NOW - timedelta(minutes=30))
    tenant.decision(far_ahead, "personalised", NOW - timedelta(minutes=10))  # beyond the 10 minute allowance

    _refresh(db)

    stats = tenant.stats()
    assert stats[(ad, HOUR, "personalised")] == (1, 0, 0, 0)
    assert stats[(ad, HOUR, "default")] == (1, 0, 0, 0)


@requires_db
def test_a_decision_of_another_tenant_with_the_same_request_id_is_ignored(db, tenant_factory):
    first, second = tenant_factory(), tenant_factory()
    ad = first.add_ad("ad_one")
    request_id = first.event(ad, "impression", NOW)
    second.decision(request_id, "personalised", NOW - timedelta(seconds=1))

    _refresh(db)

    assert first.stats() == {(ad, HOUR, "default"): (1, 0, 0, 0)}
    assert second.stats() == {}


@requires_db
def test_a_rerun_gives_the_same_rows(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    tenant.event(ad, "impression", NOW)
    tenant.event(ad, "click", NOW)

    _refresh(db)
    first = tenant.stats()
    _refresh(db)

    assert tenant.stats() == first == {(ad, HOUR, "default"): (1, 0, 1, 0)}


@requires_db
def test_buckets_follow_the_events_when_they_change(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    tenant.event(ad, "impression", NOW, flags=[])
    _refresh(db)

    with db.cursor() as cur:  # an erasure, for example
        cur.execute(f"DELETE FROM {SCHEMA}.ad_event WHERE tenant_id = %s", (tenant.tenant_id,))
    _refresh(db)

    assert tenant.stats() == {}


@requires_db
def test_buckets_outside_the_window_are_left_alone(db, tenant_factory):
    tenant = tenant_factory()
    ad = tenant.add_ad("ad_one")
    old = HOUR - timedelta(hours=72)
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.ad_stat_hourly (tenant_id, ad_id, hour_start, group_code, impressions) "
            "VALUES (%s, %s, %s, 'default', 7)",
            (tenant.tenant_id, ad, old),
        )
    tenant.event(ad, "impression", NOW)

    _refresh(db)

    stats = tenant.stats()
    assert stats[(ad, old, "default")][0] == 7
    assert stats[(ad, HOUR, "default")][0] == 1
