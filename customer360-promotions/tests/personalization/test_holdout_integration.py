"""
Hold-out in practice: stable assignment per visitor, the configured share overall,
and every request recording its group.

The first checks are pure; the last two need PostgreSQL with
personalization-schema.sql applied (skipped otherwise).
"""

import collections

import pytest
from sqlalchemy import text

from personalization.holdout import is_holdout
from personalization.identity import visitor_key
from tests._pg import check_postgres_available

needs_pg = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)


def test_about_ten_percent_of_many_visitors_are_held_out_by_default():
    visitors = 5_000
    held = sum(
        is_holdout(7, visitor_key(7, f"synthetic-visitor-{i}", master_secret="s"), "default", 10.0)
        for i in range(visitors)
    )
    # 10% of 5,000 is 500 with a standard deviation near 21: allow four of them.
    assert 410 <= held <= 590, held


def test_the_share_follows_the_setting():
    keys = [visitor_key(7, f"v{i}", master_secret="s") for i in range(3_000)]
    for pct in (0, 25, 100):
        held = sum(is_holdout(7, k, "default", pct) for k in keys)
        assert abs(held / len(keys) - pct / 100) < 0.04


def test_changing_the_salt_starts_a_new_measurement_period():
    keys = [visitor_key(7, f"v{i}", master_secret="s") for i in range(2_000)]
    before = {k for k in keys if is_holdout(7, k, "period-1", 10.0)}
    after = {k for k in keys if is_holdout(7, k, "period-2", 10.0)}
    assert before != after  # a different group, not a rotation of the same people
    assert len(before & after) < len(before)  # most of the old group is released


@pytest.fixture
def _tables(test_engine):
    if test_engine.execute(text("SELECT to_regclass('leo_ads.serving_decision')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


@needs_pg
def test_a_visitor_stays_in_the_same_group_across_twenty_requests(
    _tables, serve_client, world_factory, enable_personalisation
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world, holdout_pct=50.0)

    groups = {
        serve_client.get(
            f"/serve/{world.placement_key}",
            params={"tenant": world.tenant_key, "leovid": "steady-visitor"},
        ).json()["personalisation"]["group"]
        for _ in range(20)
    }

    assert len(groups) == 1
    assert groups <= {"holdout", "default_no_history"}  # no history yet, so never "personalised"


@needs_pg
def test_every_request_records_its_group_and_holdout_share_is_close_to_the_setting(
    _tables, serve_client, serve_app, test_engine, world_factory, enable_personalisation
):
    world = world_factory()
    world.add_ad("ad_one")
    enable_personalisation(world, holdout_pct=10.0)
    responses = {}
    for i in range(120):
        body = serve_client.get(
            f"/serve/{world.placement_key}",
            params={"tenant": world.tenant_key, "leovid": f"synthetic-{i}"},
        ).json()
        responses[body["request_id"]] = body["personalisation"]["group"]

    serve_app.decision_writer.flush()
    stored = dict(
        test_engine.execute(
            text(
                "SELECT request_id::text, group_code FROM leo_ads.serving_decision WHERE tenant_id = :t"
            ),
            {"t": world.tenant_id},
        ).all()
    )

    assert stored == responses  # one row per request, each with the group the visitor was served
    counts = collections.Counter(stored.values())
    assert set(counts) <= {"holdout", "default_no_history"}
    # 10% of 120 is 12 (sd about 3.3): allow a wide band so this never flakes.
    assert 2 <= counts["holdout"] <= 26, counts
