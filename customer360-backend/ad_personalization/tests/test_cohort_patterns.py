"""Tests for the cohort patterns job.

Pure logic (the key, the objective mapping, the privacy threshold) always runs.
The database group is opt-in; see tests/conftest.py. The DB tests use a small
minimum cohort size so a handful of synthetic visitors is enough.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import NOW, SCHEMA, requires_db
from psycopg2.extras import Json

from ad_personalization import cohort_patterns as cp

VECTORS = (
    Path(cp.__file__).resolve().parents[3]
    / "docs" / "ad-personalization" / "contracts" / "cohort-key-test-vectors.json"
)
MIN = 3  # the minimum cohort size used by the database tests


def keys(n):
    return [bytes([i + 1]) * 16 for i in range(n)]


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------


def test_cohort_keys_match_the_shared_vectors():
    if not VECTORS.exists():
        pytest.skip("vectors file not available in this checkout")
    vectors = json.loads(VECTORS.read_text())["vectors"]
    assert vectors
    for vector in vectors:
        assert cp.cohort_key(vector["affinities"], vector["device_type"]) == vector["expected"], vector["name"]


def test_the_same_buckets_always_give_the_same_key():
    a = {"tag": {"x": {"score": 2.0}, "y": {"score": 1.0}}}
    b = {"tag": {"y": {"score": 1.0}, "x": {"score": 2.0}}}  # different insertion order
    assert cp.cohort_key(a, "mobile") == cp.cohort_key(b, "mobile")


@pytest.mark.parametrize(
    "objective, types",
    [
        ("awareness", ("view",)),
        ("reach", ("view",)),
        ("traffic", ("click",)),
        ("conversions", ("conversion", "view_through_conversion")),
        ("conversion", ("conversion", "view_through_conversion")),
        ("retargeting", ("click",)),
        (None, ("click",)),
    ],
)
def test_objective_event_types(objective, types):
    assert cp.objective_event_types(objective) == types


def test_min_size_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv(cp.MIN_SIZE_ENV, raising=False)
    assert cp.min_size_from_env() == 50
    monkeypatch.setenv(cp.MIN_SIZE_ENV, "7")
    assert cp.min_size_from_env() == 7
    monkeypatch.setenv(cp.MIN_SIZE_ENV, "nonsense")
    assert cp.min_size_from_env() == 50


def _row(key, event_type, ad_id, device="mobile", days_ago=1, objective="traffic", tag="travel"):
    profile = {"tag": {tag: {"score": 1.0}}}
    return (key, profile, event_type, ad_id, device, NOW - timedelta(days=days_ago), objective)


def test_compute_publishes_only_ads_shown_to_enough_distinct_visitors():
    rows = []
    for key in keys(3):
        rows += [_row(key, "impression", 1), _row(key, "click", 1)]
    for key in keys(2):  # ad 2 is shown to only two of them
        rows.append(_row(key, "impression", 2))

    result = cp.compute_patterns(rows, NOW, MIN)

    assert result == [("tag:travel@mobile", 1, 3, 3, 3)]


def test_one_visitor_seeing_an_ad_many_times_is_still_one_visitor():
    rows = [_row(keys(1)[0], "impression", 1) for _ in range(10)]
    assert cp.compute_patterns(rows, NOW, 2) == []


def test_old_responses_are_ignored_and_a_visitor_without_events_adds_nothing():
    rows = [_row(k, "impression", 1, days_ago=20) for k in keys(3)]
    rows += [(k, {}, None, None, None, None, None) for k in keys(3)]
    assert cp.compute_patterns(rows, NOW, MIN) == []


def test_the_modal_device_decides_the_cohort():
    key = keys(1)[0]
    rows = [_row(key, "impression", 1, device="desktop"), _row(key, "impression", 1, device="mobile"),
            _row(key, "view", 1, device="mobile")]
    (row,) = cp.compute_patterns(rows, NOW, 1)
    assert row[0] == "tag:travel@mobile"


# --------------------------------------------------------------------------
# Database (opt-in)
# --------------------------------------------------------------------------


def _visitor(tenant, key, ad_id, *, impressions=1, clicks=0, device="mobile", tag="travel", flags=None,
             consent=None, with_key=True):
    with tenant.conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.visitor_behaviour_profile (tenant_id, user_key_hash, affinities, event_count) "
            "VALUES (%s, %s, %s, 1) ON CONFLICT DO NOTHING",
            (tenant.tenant_id, key, Json({"tag": {tag: {"score": 1.0}}})),
        )
    if consent:
        tenant.consent(key, consent)
    event_key = key if with_key else None
    for _ in range(impressions):
        tenant.event(ad_id, "impression", NOW - timedelta(days=1), key=event_key, flags=flags)
    for _ in range(clicks):
        tenant.event(ad_id, "click", NOW - timedelta(days=1), key=event_key, flags=flags)
    with tenant.conn.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.ad_event SET device_type = %s WHERE tenant_id = %s AND user_key_hash = %s",
            (device, tenant.tenant_id, key),
        )


def _patterns(tenant):
    with tenant.conn.cursor() as cur:
        cur.execute(
            f"SELECT cohort_key, ad_id, cohort_size, impressions, objective_events "
            f"FROM {SCHEMA}.cohort_pattern WHERE tenant_id = %s ORDER BY cohort_key, ad_id",
            (tenant.tenant_id,),
        )
        return cur.fetchall()


def _refresh(db, **kwargs):
    return cp.refresh_cohort_patterns(db, schema=SCHEMA, now=NOW, min_size=MIN, log=lambda _m: None, **kwargs)


def _campaign(tenant, ad_id, objective):
    with tenant.conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.campaign (tenant_id, campaign_key, name, objective, status) "
            "VALUES (%s, %s, 'c', %s, 'active') RETURNING campaign_id",
            (tenant.tenant_id, f"cmp-{ad_id}", objective),
        )
        cur.execute(f"UPDATE {SCHEMA}.ad SET campaign_id = %s WHERE ad_id = %s", (cur.fetchone()[0], ad_id))


@requires_db
def test_a_cohort_of_enough_visitors_is_published_with_its_totals(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    _campaign(tenant, ad, "traffic")
    for i, key in enumerate(keys(3)):
        _visitor(tenant, key, ad, impressions=2, clicks=1 if i < 2 else 0)

    summary = _refresh(db)

    assert _patterns(tenant) == [("tag:travel@mobile", ad, 3, 6, 2)]
    assert summary[tenant.tenant_id] == {"cohorts": 1, "patterns": 1}


@requires_db
def test_cohorts_below_the_minimum_are_not_written(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one")
    for key in keys(2):
        _visitor(tenant, key, ad)

    _refresh(db)

    assert _patterns(tenant) == []


@requires_db
def test_rows_are_deleted_when_a_cohort_drops_below_the_minimum(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one")
    for key in keys(3):
        _visitor(tenant, key, ad)
    _refresh(db)
    assert len(_patterns(tenant)) == 1

    tenant.consent(keys(3)[0], "denied")  # one visitor opts out
    _refresh(db)

    assert _patterns(tenant) == []


@requires_db
def test_only_eligible_visitors_contribute(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_in")
    ad = tenant.add_ad("ad_one")
    granted = keys(3)
    for key in granted:
        _visitor(tenant, key, ad, consent="granted")
    _visitor(tenant, b"\x50" * 16, ad)  # no record in an opt_in tenant
    _visitor(tenant, b"\x51" * 16, ad, consent="denied")
    _visitor(tenant, b"\x52" * 16, ad, consent="granted", flags=["bot"])  # only flagged events
    _visitor(tenant, b"\x53" * 16, ad, consent="granted", with_key=False)  # events carry no key
    erased = b"\x54" * 16
    _visitor(tenant, erased, ad, consent="granted")
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.erasure_request (tenant_id, user_key_hash, status) VALUES (%s, %s, 'pending')",
            (tenant.tenant_id, erased),
        )

    _refresh(db)

    # Only the three granted visitors with real events are counted.
    assert _patterns(tenant) == [("tag:travel@mobile", ad, 3, 3, 0)]


@requires_db
def test_an_erased_visitor_stops_contributing_at_the_next_refresh(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one")
    for key in keys(3):
        _visitor(tenant, key, ad)
    _refresh(db)
    assert _patterns(tenant)[0][2] == 3

    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.erasure_request (tenant_id, user_key_hash, status) VALUES (%s, %s, 'redis_purged')",
            (tenant.tenant_id, keys(3)[2]),
        )
    _refresh(db)

    assert _patterns(tenant) == []


@requires_db
def test_the_minimum_applies_per_ad_not_just_per_cohort(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    popular = tenant.add_ad("popular")
    rare = tenant.add_ad("rare")
    for key in keys(4):
        _visitor(tenant, key, popular)
    _visitor(tenant, keys(4)[0], rare)  # only one visitor saw this ad

    _refresh(db)

    assert [row[1] for row in _patterns(tenant)] == [popular]


@requires_db
def test_no_visitor_identifier_is_stored(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one")
    for key in keys(3):
        _visitor(tenant, key, ad)

    _refresh(db)

    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'cohort_pattern'",
            (SCHEMA,),
        )
        columns = {r[0] for r in cur.fetchall()}
    assert "user_key_hash" not in columns


@requires_db
def test_tenants_never_mix_and_disabled_tenants_are_skipped(db, tenant_factory):
    first, second, off = tenant_factory(), tenant_factory(), tenant_factory()
    first.settings(consent_mode="opt_out")
    second.settings(consent_mode="opt_out")
    off.settings(enabled=False, consent_mode="opt_out")
    for tenant in (first, off):
        ad = tenant.add_ad("ad_one")
        for key in keys(3):
            _visitor(tenant, key, ad)
    second_ad = second.add_ad("ad_one")
    for key in keys(2):  # the same keys in another tenant: too few there
        _visitor(second, key, second_ad)

    _refresh(db)

    assert len(_patterns(first)) == 1
    assert _patterns(second) == []
    assert _patterns(off) == []
