"""Tests for the profile aggregation job.

Pure logic (decay, features, the weights contract) always runs. The database
group is opt-in; see tests/conftest.py.
"""

import json
from datetime import timedelta

import pytest
from conftest import NOW, SCHEMA, requires_db

from ad_personalization import profile_aggregation as pa

WEIGHTS = pa.load_weights()

KEY_A = b"\xaa" * 16
KEY_B = b"\xbb" * 16
KEY_C = b"\xcc" * 16


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------


def test_builtin_weights_match_the_contract_file():
    contract = json.loads(pa._DEFAULT_WEIGHTS_PATH.read_text())
    contract.pop("description")
    assert contract == pa.BUILTIN_WEIGHTS


def _ev(event_type, days_ago=0, **extra):
    return {
        "event_type": event_type,
        "event_time": NOW - timedelta(days=days_ago),
        "ad_id": 1,
        "advertiser_id": 5,
        "ad_type": "display",
        "format_code": "300x250",
        "content_payload": {},
        "ad_metadata": {"tags": ["Travel", " travel ", "SPORT"]},
        **extra,
    }


def test_features_follow_the_ad_servers_derivation():
    features = pa.ad_features(_ev("click"), WEIGHTS)
    assert features == [
        ("advertiser", "5"), ("ad_type", "display"), ("format", "300x250"),
        ("tag", "travel"), ("tag", "sport"),
    ]


def test_tags_fall_back_to_the_creative_payload_and_are_bounded():
    row = _ev("click", ad_metadata={}, content_payload={"tags": [f"t{i}" for i in range(30)]})
    tags = [v for d, v in pa.ad_features(row, WEIGHTS) if d == "tag"]
    assert len(tags) == WEIGHTS["max_tags"]


def test_a_click_scores_its_weight_on_every_feature():
    profile = pa.build_profile([_ev("click")], NOW, WEIGHTS)

    entry = profile.affinities["advertiser"]["5"]
    assert entry["score"] == pytest.approx(1.0)
    assert entry["n"] == 1
    assert profile.event_count == 1
    assert profile.affinities["tag"]["travel"]["score"] == pytest.approx(1.0)


def test_scores_halve_every_half_life():
    fresh = pa.build_profile([_ev("click")], NOW, WEIGHTS)
    old = pa.build_profile([_ev("click", days_ago=WEIGHTS["half_life_days"])], NOW, WEIGHTS)
    ancient = pa.build_profile([_ev("click", days_ago=2 * WEIGHTS["half_life_days"])], NOW, WEIGHTS)

    assert old.affinities["advertiser"]["5"]["score"] == pytest.approx(
        fresh.affinities["advertiser"]["5"]["score"] / 2, rel=1e-5
    )
    assert ancient.affinities["advertiser"]["5"]["score"] == pytest.approx(0.25, rel=1e-5)


def test_a_dismissal_lowers_affinity_and_is_remembered_briefly():
    profile = pa.build_profile([_ev("click"), _ev("dismiss")], NOW, WEIGHTS)
    assert profile.affinities["advertiser"]["5"]["score"] == pytest.approx(0.0)
    assert profile.recent_dismissed == {"1": NOW.isoformat()}

    stale = pa.build_profile([_ev("dismiss", days_ago=3)], NOW, WEIGHTS)
    assert stale.recent_dismissed == {}


def test_impressions_count_as_events_but_add_no_affinity():
    profile = pa.build_profile([_ev("impression")], NOW, WEIGHTS)
    assert profile.event_count == 1
    assert profile.affinities["advertiser"]["5"]["score"] == 0.0


def test_unknown_event_types_and_empty_input_make_no_profile():
    assert pa.build_profile([], NOW, WEIGHTS) is None
    assert pa.build_profile([_ev("teleport")], NOW, WEIGHTS) is None


def test_view_through_conversion_counts_as_a_weaker_conversion():
    weights = WEIGHTS["event_weights"]
    assert 0 < weights["view_through_conversion"] < weights["conversion"]


# --------------------------------------------------------------------------
# Database (opt-in)
# --------------------------------------------------------------------------


def _rebuild(db, **kwargs):
    return pa.rebuild_profiles(db, schema=SCHEMA, now=NOW, weights=WEIGHTS, log=lambda _m: None, **kwargs)


@requires_db
def test_profiles_come_only_from_unflagged_events_of_eligible_visitors(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_in")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    for key in (KEY_A, KEY_B, KEY_C):
        tenant.consent(key, "granted")
    tenant.consent(KEY_C, "denied")
    tenant.event(ad, "click", NOW, key=KEY_A)
    tenant.event(ad, "click", NOW, key=KEY_B, flags=["bot"])  # flagged: no basis
    tenant.event(ad, "click", NOW, key=KEY_C)  # consent withdrawn
    tenant.event(ad, "click", NOW, key=None)  # no visitor key

    summary = _rebuild(db)

    assert summary[tenant.tenant_id]["profiles"] == 1
    profiles = tenant.profiles()
    assert set(profiles) == {KEY_A}
    assert profiles[KEY_A]["affinities"]["tag"]["travel"]["score"] == pytest.approx(1.0)
    assert profiles[KEY_A]["events"] == 1


@requires_db
def test_opt_in_needs_a_grant_and_opt_out_only_needs_no_denial(db, tenant_factory):
    opt_in, opt_out = tenant_factory(), tenant_factory()
    opt_in.settings(consent_mode="opt_in")
    opt_out.settings(consent_mode="opt_out")
    for tenant in (opt_in, opt_out):
        ad = tenant.add_ad("ad_one")
        tenant.event(ad, "click", NOW, key=KEY_A)  # no recorded choice
        tenant.event(ad, "click", NOW, key=KEY_B)
        tenant.consent(KEY_B, "denied")

    _rebuild(db)

    assert set(opt_in.profiles()) == set()
    assert set(opt_out.profiles()) == {KEY_A}


@requires_db
def test_a_tenant_with_personalisation_off_gets_no_profiles(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(enabled=False, consent_mode="opt_out")
    tenant.event(tenant.add_ad("ad_one"), "click", NOW, key=KEY_A)

    assert tenant.tenant_id not in _rebuild(db)
    assert tenant.profiles() == {}


@requires_db
def test_decay_is_applied_from_the_event_time(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.event(ad, "click", NOW - timedelta(days=WEIGHTS["half_life_days"]), key=KEY_A)

    _rebuild(db)

    score = tenant.profiles()[KEY_A]["affinities"]["tag"]["travel"]["score"]
    assert score == pytest.approx(0.5, rel=1e-4)


@requires_db
def test_a_rerun_is_idempotent_and_keeps_the_version(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.event(ad, "click", NOW - timedelta(days=1), key=KEY_A)
    tenant.event(ad, "view", NOW - timedelta(days=2), key=KEY_A)

    _rebuild(db)
    first = tenant.profiles()
    _rebuild(db)
    second = tenant.profiles()

    assert second == first
    assert second[KEY_A]["version"] == 1


@requires_db
def test_new_events_change_the_profile_and_bump_the_version(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.event(ad, "click", NOW - timedelta(days=1), key=KEY_A)
    _rebuild(db)

    tenant.event(ad, "click", NOW, key=KEY_A)
    _rebuild(db)

    profile = tenant.profiles()[KEY_A]
    assert profile["version"] == 2
    assert profile["events"] == 2


@requires_db
def test_updated_at_is_the_jobs_own_clock_on_insert_and_on_update(db, tenant_factory):
    # The ad server uses updated_at as "events up to here are already in the
    # profile" when it layers fresh signals on top, so it must be the job's time
    # on both paths, not the database's.
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one", tags=["travel"])
    tenant.event(ad, "click", NOW - timedelta(days=1), key=KEY_A)

    def updated_at():
        with db.cursor() as cur:
            cur.execute(
                f"SELECT updated_at FROM {SCHEMA}.visitor_behaviour_profile "
                "WHERE tenant_id = %s AND user_key_hash = %s",
                (tenant.tenant_id, KEY_A),
            )
            return cur.fetchone()[0]

    _rebuild(db)
    assert updated_at() == NOW

    later = NOW + timedelta(hours=1)  # decay changes the scores, so the row is updated
    pa.rebuild_profiles(db, schema=SCHEMA, now=later, weights=WEIGHTS, log=lambda _m: None)
    assert updated_at() == later


@requires_db
def test_a_profile_is_deleted_when_the_visitor_is_no_longer_eligible(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_in")
    ad = tenant.add_ad("ad_one")
    tenant.consent(KEY_A, "granted")
    tenant.event(ad, "click", NOW, key=KEY_A)
    _rebuild(db)
    assert set(tenant.profiles()) == {KEY_A}

    tenant.consent(KEY_A, "denied")
    summary = _rebuild(db)

    assert tenant.profiles() == {}
    assert summary[tenant.tenant_id]["deleted"] == 1


@requires_db
def test_events_older_than_the_window_do_not_count(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    ad = tenant.add_ad("ad_one")
    tenant.event(ad, "click", NOW - timedelta(days=WEIGHTS["window_days"] + 1), key=KEY_A)

    _rebuild(db)

    assert tenant.profiles() == {}


@requires_db
def test_tenants_never_mix_even_for_the_same_visitor_key(db, tenant_factory):
    first, second = tenant_factory(), tenant_factory()
    for tenant in (first, second):
        tenant.settings(consent_mode="opt_out")
    first.event(first.add_ad("ad_a", tags=["travel"]), "click", NOW, key=KEY_A)
    second.event(second.add_ad("ad_b", tags=["sport"]), "click", NOW, key=KEY_A)

    _rebuild(db)

    assert set(first.profiles()[KEY_A]["affinities"]["tag"]) == {"travel"}
    assert set(second.profiles()[KEY_A]["affinities"]["tag"]) == {"sport"}


@requires_db
def test_advertiser_affinity_is_learned(db, tenant_factory):
    tenant = tenant_factory()
    tenant.settings(consent_mode="opt_out")
    advertiser = tenant.add_advertiser()
    ad = tenant.add_ad("ad_one", advertiser_id=advertiser)
    tenant.event(ad, "conversion", NOW, key=KEY_A)

    _rebuild(db)

    entry = tenant.profiles()[KEY_A]["affinities"]["advertiser"][str(advertiser)]
    assert entry["score"] == pytest.approx(2.0)
