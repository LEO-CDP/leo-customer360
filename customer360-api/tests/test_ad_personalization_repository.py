"""SQL tests for AdPersonalizationRepository against a real PostgreSQL.

These need the ``leo_ads`` schema with personalization-schema.sql applied, so
they are opt-in, which also guarantees they never run against a real database by
accident:

    AD_PERSONALIZATION_ALLOW_DB_TESTS=1 \
    ADS_DATABASE_URL=postgresql+psycopg2://user:pass@127.0.0.1:5432/customer360 \
        python -m pytest tests/test_ad_personalization_repository.py

Every test runs inside a transaction that is rolled back (repository commits
become savepoint releases), so nothing is persisted.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.repositories.ad_personalization_repository import (
    AdPersonalizationRepository,
    AdPersonalizationValidationError,
    AdTenantNotFoundError,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("AD_PERSONALIZATION_ALLOW_DB_TESTS") != "1",
    reason="set AD_PERSONALIZATION_ALLOW_DB_TESTS=1 (and ADS_DATABASE_URL) to run against a disposable database",
)


@pytest.fixture
def session():
    from core.ads_database import engine

    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        outer.rollback()
        connection.close()


def _make_ad_tenant(db, platform_tenant: uuid.UUID, *, status: str = "active") -> int:
    key = f"test-{uuid.uuid4().hex[:12]}"
    return db.execute(
        text(
            "INSERT INTO leo_ads.tenant (tenant_key, name, status, customer360_tenant_id) "
            "VALUES (:k, :k, :s, :p) RETURNING tenant_id"
        ),
        {"k": key, "s": status, "p": str(platform_tenant)},
    ).scalar_one()


# --------------------------------------------------------------------------
# Tenant mapping
# --------------------------------------------------------------------------


def test_the_platform_tenant_maps_to_its_ad_tenant(session):
    platform = uuid.uuid4()
    ad_tenant = _make_ad_tenant(session, platform)
    repo = AdPersonalizationRepository(session)

    assert repo.resolve_ad_tenant_id(platform) == ad_tenant
    assert repo.resolve_ad_tenant_id(str(platform)) == ad_tenant


def test_an_unlinked_or_inactive_tenant_does_not_resolve(session):
    repo = AdPersonalizationRepository(session)
    with pytest.raises(AdTenantNotFoundError):
        repo.resolve_ad_tenant_id(uuid.uuid4())

    paused = uuid.uuid4()
    _make_ad_tenant(session, paused, status="paused")
    with pytest.raises(AdTenantNotFoundError):
        repo.resolve_ad_tenant_id(paused)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_creating_the_tenant_row_uses_the_documented_defaults(session):
    ad_tenant = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)

    row = repo.upsert_setting(ad_tenant, scope_type="tenant", updated_by="admin@example.com")

    assert row["enabled"] is False
    assert row["holdout_pct"] == 10.0
    assert row["consent_mode"] == "opt_in"
    assert row["holdout_salt"] == "default"
    assert row["scope_ref"] == ""
    assert row["updated_by"] == "admin@example.com"
    assert row["updated_at"] is not None


def test_an_update_only_changes_the_fields_provided(session):
    ad_tenant = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)
    repo.upsert_setting(ad_tenant, scope_type="tenant", enabled=True, holdout_pct=20)

    row = repo.upsert_setting(ad_tenant, scope_type="tenant", consent_mode="opt_out")

    assert (row["enabled"], row["holdout_pct"], row["consent_mode"]) == (True, 20.0, "opt_out")


def test_a_new_measurement_period_replaces_the_salt(session):
    ad_tenant = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)
    before = repo.upsert_setting(ad_tenant, scope_type="tenant")

    after = repo.upsert_setting(ad_tenant, scope_type="tenant", new_measurement_period=True)

    assert after["holdout_salt"] != before["holdout_salt"]
    assert after["holdout_salt"].isdigit()


def test_site_and_placement_rows_need_the_tenant_row_first(session):
    ad_tenant = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)
    with pytest.raises(AdPersonalizationValidationError):
        repo.upsert_setting(ad_tenant, scope_type="placement", scope_ref="homepage_top")

    repo.upsert_setting(ad_tenant, scope_type="tenant")
    row = repo.upsert_setting(
        ad_tenant, scope_type="placement", scope_ref="homepage_top", enabled=False
    )
    assert (row["scope_type"], row["scope_ref"], row["enabled"]) == ("placement", "homepage_top", False)
    assert row["consent_mode"] is None  # only reported on the tenant scope


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope_type": "galaxy"},
        {"scope_type": "tenant", "scope_ref": "x"},
        {"scope_type": "site"},
        {"scope_type": "tenant", "holdout_pct": 101},
        {"scope_type": "tenant", "holdout_pct": -1},
        {"scope_type": "tenant", "consent_mode": "maybe"},
        {"scope_type": "placement", "scope_ref": "p", "consent_mode": "opt_out"},
        {"scope_type": "placement", "scope_ref": "p", "new_measurement_period": True},
    ],
)
def test_rule_violations_are_rejected(session, kwargs):
    ad_tenant = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)
    repo.upsert_setting(ad_tenant, scope_type="tenant")
    with pytest.raises(AdPersonalizationValidationError):
        repo.upsert_setting(ad_tenant, **kwargs)


def test_settings_are_isolated_between_ad_tenants(session):
    first = _make_ad_tenant(session, uuid.uuid4())
    second = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)
    repo.upsert_setting(first, scope_type="tenant", enabled=True, holdout_pct=30)
    repo.upsert_setting(second, scope_type="tenant", enabled=False, holdout_pct=5)

    assert [r["holdout_pct"] for r in repo.list_settings(first)] == [30.0]
    assert [r["holdout_pct"] for r in repo.list_settings(second)] == [5.0]
    assert repo.list_settings(first + 100_000) == []


# --------------------------------------------------------------------------
# Explanations and the comparison report
# --------------------------------------------------------------------------

KEY = bytes.fromhex("a91f00112233445566778899aabbccdd")


def _catalogue(db, ad_tenant: int, *, objective: str = "traffic", ad_key: str = "ad_one") -> dict:
    """A placement, creative, campaign and ad for one ad tenant; returns their ids."""
    placement = db.execute(
        text(
            "INSERT INTO leo_ads.placement (tenant_id, placement_key, name) "
            "VALUES (:t, 'homepage_top', 'x') RETURNING placement_id"
        ),
        {"t": ad_tenant},
    ).scalar_one()
    creative = db.execute(
        text(
            "INSERT INTO leo_ads.creative (tenant_id, creative_key, ad_type, format_code) "
            "VALUES (:t, :k, 'display', '300x250') RETURNING creative_id"
        ),
        {"t": ad_tenant, "k": f"cr-{uuid.uuid4().hex[:8]}"},
    ).scalar_one()
    campaign = db.execute(
        text(
            "INSERT INTO leo_ads.campaign (tenant_id, campaign_key, name, objective, status) "
            "VALUES (:t, :k, 'c', :o, 'active') RETURNING campaign_id"
        ),
        {"t": ad_tenant, "k": f"cmp-{uuid.uuid4().hex[:8]}", "o": objective},
    ).scalar_one()
    ad = db.execute(
        text(
            "INSERT INTO leo_ads.ad (tenant_id, ad_key, campaign_id, creative_id, placement_id) "
            "VALUES (:t, :k, :c, :cr, :p) RETURNING ad_id"
        ),
        {"t": ad_tenant, "k": ad_key, "c": campaign, "cr": creative, "p": placement},
    ).scalar_one()
    return {"placement": placement, "campaign": campaign, "ad": ad}


def _decision(db, ad_tenant, ids, *, group="personalised", age_days=0, key=KEY, request_id=None):
    request_id = request_id or str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO leo_ads.serving_decision "
            "(request_id, decided_at, tenant_id, user_key_hash, placement_id, group_code, chosen, candidate_count) "
            "VALUES (CAST(:r AS uuid), now() - make_interval(days => :age), :t, :k, :p, :g, CAST(:c AS jsonb), 3)"
        ),
        {
            "r": request_id, "age": age_days, "t": ad_tenant, "k": key, "p": ids["placement"], "g": group,
            "c": '[{"ad_id": %d, "rank": 1, "score": 1.5, "signals": '
                 '[{"name": "visitor_affinity", "contribution": 1.2, "note": "n"}, '
                 '{"name": "bogus", "contribution": 3, "note": "x"}]}]' % ids["ad"],
        },
    )
    return request_id


def test_a_decision_is_explained_with_ad_key_objective_and_a_fingerprint(session):
    tenant = _make_ad_tenant(session, uuid.uuid4())
    ids = _catalogue(session, tenant)
    request_id = _decision(session, tenant, ids)

    decision = AdPersonalizationRepository(session).get_decision(tenant, request_id)

    assert decision["group"] == "personalised"
    assert decision["placement_key"] == "homepage_top"
    assert decision["visitor_fingerprint"] == "a91f0011"
    assert decision["candidate_count"] == 3
    (chosen,) = decision["chosen"]
    assert (chosen["rank"], chosen["ad_key"], chosen["objective"], chosen["score"]) == (1, "ad_one", "traffic", 1.5)
    assert [s["name"] for s in chosen["signals"]] == ["visitor_affinity"]


def test_a_decision_of_another_tenant_or_past_retention_is_not_found(session):
    first = _make_ad_tenant(session, uuid.uuid4())
    second = _make_ad_tenant(session, uuid.uuid4())
    ids = _catalogue(session, first)
    mine = _decision(session, first, ids)
    old = _decision(session, first, ids, age_days=91)
    repo = AdPersonalizationRepository(session)

    assert repo.get_decision(second, mine) is None
    assert repo.get_decision(first, old) is None
    assert repo.get_decision(first, str(uuid.uuid4())) is None


def test_decisions_are_listed_newest_first_with_filters(session):
    tenant = _make_ad_tenant(session, uuid.uuid4())
    ids = _catalogue(session, tenant)
    newest = _decision(session, tenant, ids, age_days=1)
    older = _decision(session, tenant, ids, group="holdout", age_days=5)
    _decision(session, tenant, ids, age_days=100)  # past retention
    repo = AdPersonalizationRepository(session)

    listed = repo.list_decisions(tenant, "ad_one")
    assert [d["request_id"] for d in listed] == [newest, older]
    assert listed[0]["rank"] == 1
    assert [d["request_id"] for d in repo.list_decisions(tenant, "ad_one", group="holdout")] == [older]
    assert len(repo.list_decisions(tenant, "ad_one", limit=1)) == 1
    assert repo.list_decisions(tenant, "no_such_ad") == []


def test_listing_never_shows_another_tenants_decisions(session):
    first = _make_ad_tenant(session, uuid.uuid4())
    second = _make_ad_tenant(session, uuid.uuid4())
    first_ids = _catalogue(session, first)
    second_ids = _catalogue(session, second)  # the same ad_key 'ad_one' in both
    _decision(session, first, first_ids)
    repo = AdPersonalizationRepository(session)

    assert len(repo.list_decisions(first, "ad_one")) == 1
    assert repo.list_decisions(second, "ad_one") == []
    assert second_ids["ad"] != first_ids["ad"]


def _stats(db, tenant, ids, group, impressions, clicks=0, hours_ago=2):
    db.execute(
        text(
            "INSERT INTO leo_ads.ad_stat_hourly "
            "(tenant_id, ad_id, hour_start, group_code, placement_id, campaign_id, impressions, views, clicks, conversions) "
            "VALUES (:t, :ad, date_trunc('hour', now()) - make_interval(hours => :h), :g, :p, :c, :i, 0, :k, 0)"
        ),
        {"t": tenant, "ad": ids["ad"], "h": hours_ago, "g": group, "p": ids["placement"],
         "c": ids["campaign"], "i": impressions, "k": clicks},
    )


def _window():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now


def test_the_comparison_uses_each_campaigns_objective_metric(session):
    tenant = _make_ad_tenant(session, uuid.uuid4())
    ids = _catalogue(session, tenant, objective="traffic")
    _stats(session, tenant, ids, "personalised", 20_000, clicks=520)
    _stats(session, tenant, ids, "holdout", 2_000, clicks=40)
    date_from, date_to = _window()

    report = AdPersonalizationRepository(session).comparison_report(tenant, date_from, date_to, min_sample=1000)

    groups = {g["group"]: g for g in report["groups"]}
    assert (groups["personalised"]["impressions"], groups["holdout"]["clicks"]) == (20_000, 40)
    (entry,) = report["by_objective"]
    assert entry["objective"] == "traffic" and entry["metric"] == "click_through_rate"
    assert entry["lift"] == pytest.approx(0.3)


def test_the_comparison_respects_the_window_the_placement_and_the_tenant(session):
    first = _make_ad_tenant(session, uuid.uuid4())
    second = _make_ad_tenant(session, uuid.uuid4())
    ids = _catalogue(session, first)
    other_ids = _catalogue(session, second)
    _stats(session, first, ids, "personalised", 5_000, clicks=50)
    _stats(session, first, ids, "personalised", 9_999, clicks=9_999, hours_ago=24 * 10)  # outside the window
    _stats(session, second, other_ids, "personalised", 7_777, clicks=7_777)  # another tenant
    date_from, date_to = _window()
    repo = AdPersonalizationRepository(session)

    report = repo.comparison_report(first, date_from, date_to)
    assert {g["group"]: g["impressions"] for g in report["groups"]}["personalised"] == 5_000

    elsewhere = repo.comparison_report(first, date_from, date_to, placement_key="no_such_placement")
    assert {g["group"]: g["impressions"] for g in elsewhere["groups"]}["personalised"] == 0
    here = repo.comparison_report(first, date_from, date_to, placement_key="homepage_top")
    assert {g["group"]: g["impressions"] for g in here["groups"]}["personalised"] == 5_000


# --------------------------------------------------------------------------
# Erasure requests
# --------------------------------------------------------------------------

KEY_A = bytes.fromhex("0a" * 16)
KEY_B = bytes.fromhex("0b" * 16)


def _link_key(db, ad_tenant: int, key: bytes, profile_ref: str) -> None:
    db.execute(
        text(
            "INSERT INTO leo_ads.user_serving_key (user_key_hash, tenant_id, profile_ref) "
            "VALUES (:k, :t, :p)"
        ),
        {"k": key, "t": ad_tenant, "p": profile_ref},
    )


def test_an_erasure_request_is_recorded_pending_with_the_hashed_key(session):
    tenant = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)

    created = repo.create_erasure_request(tenant, KEY_A, urgent=True, requested_by="admin@example.com")

    assert created["status"] == "pending"
    assert created["urgent"] is True
    assert created["completed_at"] is None and created["deleted_counts"] is None
    stored = session.execute(
        text("SELECT user_key_hash, requested_by FROM leo_ads.erasure_request WHERE erasure_id = :i"),
        {"i": created["erasure_id"]},
    ).one()
    assert bytes(stored.user_key_hash) == KEY_A
    assert stored.requested_by == "admin@example.com"


def test_a_pending_request_moves_to_redis_purged_once_and_never_backwards(session):
    tenant = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)
    erasure_id = repo.create_erasure_request(tenant, KEY_A, urgent=False, requested_by=None)["erasure_id"]

    assert repo.mark_redis_purged(tenant, erasure_id)["status"] == "redis_purged"

    session.execute(
        text(
            "UPDATE leo_ads.erasure_request SET status = 'completed', completed_at = now(), "
            "deleted_counts = CAST(:c AS jsonb) WHERE erasure_id = :i"
        ),
        {"c": '{"ad_event": 3}', "i": erasure_id},
    )
    again = repo.mark_redis_purged(tenant, erasure_id)  # a completed request is left alone
    assert again["status"] == "completed"
    assert again["deleted_counts"] == {"ad_event": 3}
    assert again["completed_at"] is not None


def test_erasure_requests_are_invisible_to_other_tenants(session):
    first = _make_ad_tenant(session, uuid.uuid4())
    second = _make_ad_tenant(session, uuid.uuid4())
    repo = AdPersonalizationRepository(session)
    erasure_id = repo.create_erasure_request(first, KEY_A, urgent=False, requested_by=None)["erasure_id"]

    assert repo.get_erasure_request(first, erasure_id)["erasure_id"] == erasure_id
    assert repo.get_erasure_request(second, erasure_id) is None
    assert repo.mark_redis_purged(second, erasure_id) is None
    assert repo.get_erasure_request(first, erasure_id)["status"] == "pending"  # untouched


def test_a_profile_reference_resolves_to_its_linked_keys_in_this_tenant_only(session):
    first = _make_ad_tenant(session, uuid.uuid4())
    second = _make_ad_tenant(session, uuid.uuid4())
    _link_key(session, first, KEY_A, "master-1")
    _link_key(session, first, KEY_B, "master-1")
    _link_key(session, second, bytes.fromhex("0c" * 16), "master-1")  # same reference, other tenant
    repo = AdPersonalizationRepository(session)

    assert repo.profile_keys(first, "master-1") == [KEY_A, KEY_B]
    assert repo.profile_keys(first, "unknown") == []
    assert repo.profile_keys(second, "master-1") == [bytes.fromhex("0c" * 16)]
