"""
Cohorts and page context for visitors with little history of their own.

Pure tests (key vectors shared with the backend job, the two signals, ranking with
fakes) always run. The repository check needs PostgreSQL with
personalization-schema.sql applied.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from personalization.cohorts import cohort_key, device_class, top_interests
from personalization.decision_log import DecisionLog
from personalization.eligibility import RequestContext
from personalization.ranking_service import NO_HISTORY, PERSONALISED, RankingService
from personalization.scoring import AdStats, ScoringConfig, cohort_signal, page_context_signal
from personalization.settings import EffectiveSettings
from repository.stats_repository import StatsRepository
from tests._pg import check_postgres_available

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
TENANT = 7
VECTORS = (
    Path(__file__).resolve().parents[3]
    / "docs" / "ad-personalization" / "contracts" / "cohort-key-test-vectors.json"
)


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------


def test_every_shared_vector_gives_the_expected_key():
    if not VECTORS.exists():
        pytest.skip("test vectors not available in this checkout")
    vectors = json.loads(VECTORS.read_text())["vectors"]
    assert vectors
    for vector in vectors:
        assert cohort_key(vector["affinities"], vector["device_type"]) == vector["expected"], vector["name"]


def test_the_key_is_deterministic_whatever_the_order_of_the_buckets():
    one = {"tag": {"a": {"score": 2.0}, "b": {"score": 1.0}}}
    two = {"tag": {"b": {"score": 1.0}, "a": {"score": 2.0}}}
    assert cohort_key(one, "mobile") == cohort_key(two, "mobile") == "tag:a+tag:b@mobile"


def test_only_coarse_buckets_matter():
    base = {"tag": {"travel": {"score": 2.0}}}
    nudged = {"tag": {"travel": {"score": 2.0004}}, "ad_type": {"display": {"score": 9.0}}}
    assert cohort_key(base, "desktop") == cohort_key(nudged, "desktop")


def test_unknown_devices_share_one_cohort():
    assert device_class("smart-fridge") == device_class(None) == "any"
    assert top_interests(None) == []


# --------------------------------------------------------------------------
# The two signals
# --------------------------------------------------------------------------

SCORING = ScoringConfig()
BASELINES = {"views": 0.5, "clicks": 0.02, "conversions": 0.002}
AD = {"ad_id": 1, "campaign_objective": "traffic"}
OVERALL = AdStats(impressions=100_000, clicks=2_000)  # 2% clicks overall


def test_a_cohort_that_clicks_more_than_average_lifts_the_ad():
    signal = cohort_signal(
        AD, stats=OVERALL, baselines=BASELINES, config=SCORING,
        cohort_row={"impressions": 5_000, "objective_events": 250, "cohort_size": 80},  # 5% vs 2%
    )
    assert signal.name == "cohort_pattern"
    assert signal.contribution > 1.5
    assert "similar visitors" in signal.note


def test_a_cohort_that_clicks_less_lowers_it_but_never_below_the_clip():
    signal = cohort_signal(
        AD, stats=OVERALL, baselines=BASELINES, config=SCORING,
        cohort_row={"impressions": 50_000, "objective_events": 0, "cohort_size": 80},
    )
    assert signal.contribution == pytest.approx(SCORING.clip_min)


def test_a_thin_or_missing_cohort_row_says_nothing():
    assert cohort_signal(AD, stats=OVERALL, baselines=BASELINES, config=SCORING, cohort_row=None) is None
    thin = {"impressions": 40, "objective_events": 40, "cohort_size": 80}
    assert cohort_signal(AD, stats=OVERALL, baselines=BASELINES, config=SCORING, cohort_row=thin) is None


def test_the_factor_is_pulled_toward_one_for_small_counts():
    small = cohort_signal(
        AD, stats=OVERALL, baselines=BASELINES, config=SCORING,
        cohort_row={"impressions": 100, "objective_events": 3, "cohort_size": 60},  # 3% on thin data
    )
    large = cohort_signal(
        AD, stats=OVERALL, baselines=BASELINES, config=SCORING,
        cohort_row={"impressions": 100_000, "objective_events": 3_000, "cohort_size": 60},  # 3% on lots
    )
    assert 1.0 < small.contribution < large.contribution < SCORING.clip_max


def test_page_keywords_boost_matching_ads_and_never_exclude_others():
    ad = {"ad_id": 1, "rules": [{"context_keywords": ["Travel", "hotels"]}]}
    assert page_context_signal(ad, ["travel"]).contribution == pytest.approx(1.2)
    assert page_context_signal(ad, ["travel", "HOTELS", "other"]).contribution == pytest.approx(1.44)
    assert page_context_signal(ad, ["cooking"]) is None
    assert page_context_signal(ad, []) is None
    assert page_context_signal({"ad_id": 2, "rules": []}, ["travel"]) is None


# --------------------------------------------------------------------------
# In the ranking service
# --------------------------------------------------------------------------


def _config():
    return SimpleNamespace(
        personalization_time_budget_ms=10_000, eligibility_enforced=True, beta_prior_strength=20,
        affinity_half_life_days=14, affinity_weight_clip_min=0.5, affinity_weight_clip_max=2.0,
        low_history_event_threshold=5, new_ad_min_share=0.0, new_ad_impression_threshold=1000,
        candidate_cache_ttl_seconds=60, settings_cache_ttl_seconds=60,
    )


def _ad(ad_id, **extra):
    return {"ad_id": ad_id, "tenant_id": TENANT, "score_weight": 1.0, "campaign_id": None, "rules": [],
            "campaign_objective": "traffic", **extra}


class _Ads:
    def __init__(self, ads):
        self.ads = ads

    def get_candidate_ads(self, *_a, **_k):
        return {"tenant_id": TENANT, "placement_id": 1, "placement_key": "p"}, list(self.ads)


class _Settings:
    def resolve(self, *_a, **_k):
        return EffectiveSettings(enabled=True, holdout_pct=0.0, holdout_salt="s", consent_mode="opt_out")


class _Consent:
    def get_status(self, *_a):
        return None

    def has_open_erasure(self, *_a):
        return False


class _Profiles:
    def __init__(self, event_count=0):
        self.event_count = event_count

    def get_profile(self, *_a, **_k):
        return {"affinities": {}, "event_count": self.event_count} if self.event_count else None

    def linked_keys(self, tenant_id, key):
        return [key]


class _Stats:
    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def get_ad_stats(self, tenant_id, ad_ids, **_k):
        return {i: OVERALL for i in ad_ids}

    def get_cohort_patterns(self, tenant_id, key, ad_ids=None, timeout_ms=None):
        self.asked.append(key)
        return self.rows


class _Sink:
    def put(self, _r):
        pass


def _service(ads, stats, profiles):
    return RankingService(
        ad_repository=_Ads(ads), settings_resolver=_Settings(), consent_repository=_Consent(),
        profile_repository=profiles, stats_repository=stats, hot_state=None,
        decisions=DecisionLog(_Sink()), config=_config(), now=lambda: NOW,
    )


def _rank(service, device_type="mobile", keywords=()):
    return service.rank(
        tenant_key="demo", placement_ref="p", limit=5, leovid="visitor", request_id="fixed",
        context=RequestContext(device_type=device_type, keywords=tuple(keywords)),
    )


COHORT_ROWS = {
    2: {"cohort_size": 80, "impressions": 20_000, "objective_events": 1_600},  # 8% vs 2% overall
    1: {"cohort_size": 80, "impressions": 20_000, "objective_events": 200},  # 1% vs 2% overall
}


def test_a_visitor_with_no_history_is_ranked_by_their_cohort_when_one_exists():
    stats = _Stats(COHORT_ROWS)
    result = _rank(_service([_ad(1), _ad(2)], stats, _Profiles(0)))

    assert result.group_code == PERSONALISED
    assert [a["ad_id"] for a in result.ads] == [2, 1]
    assert stats.asked == ["none@mobile"]  # no interests yet, on a phone
    names = {s.name for s in result.scored[0].signals}
    assert "cohort_pattern" in names


def test_without_a_published_cohort_the_same_visitor_gets_default_order():
    result = _rank(_service([_ad(1), _ad(2)], _Stats({}), _Profiles(0)))

    assert result.group_code == NO_HISTORY
    assert [a["ad_id"] for a in result.ads] == [1, 2]


def test_the_cohort_is_only_consulted_for_visitors_with_little_history():
    stats = _Stats(COHORT_ROWS)
    _rank(_service([_ad(1), _ad(2)], stats, _Profiles(event_count=50)))
    assert stats.asked == []


def test_a_cohort_of_the_visitors_own_device_class_is_asked_for():
    stats = _Stats({})
    _rank(_service([_ad(1)], stats, _Profiles(0)), device_type="desktop")
    assert stats.asked == ["none@desktop"]


def test_page_context_alone_can_personalise_a_visitor_with_no_history():
    ads = [_ad(1), _ad(2, rules=[{"context_keywords": ["travel"]}])]
    result = _rank(_service(ads, _Stats({}), _Profiles(0)), keywords=("travel",))

    assert result.group_code == PERSONALISED
    assert [a["ad_id"] for a in result.ads] == [2, 1]
    assert any(s.name == "page_context" for s in result.scored[0].signals)


def test_a_failing_cohort_lookup_does_not_break_ranking():
    class Broken(_Stats):
        def get_cohort_patterns(self, *_a, **_k):
            raise RuntimeError("database down")

    result = _rank(_service([_ad(1), _ad(2)], Broken({}), _Profiles(0)))

    assert result.group_code == NO_HISTORY
    assert [a["ad_id"] for a in result.ads] == [1, 2]


# --------------------------------------------------------------------------
# The repository
# --------------------------------------------------------------------------


@pytest.mark.skipif(not check_postgres_available(), reason="PostgreSQL with leo_ads schema required")
def test_the_repository_reads_one_tenants_cohort_only(test_engine, two_tenants):
    first, second = two_tenants
    if test_engine.execute(text("SELECT to_regclass('leo_ads.cohort_pattern')")).scalar_one() is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")
    for world, events in ((first, 30), (second, 99)):
        ad_id = test_engine.execute(
            text("SELECT ad_id FROM leo_ads.ad WHERE tenant_id = :t"), {"t": world.tenant_id}
        ).scalar_one()
        test_engine.execute(
            text(
                "INSERT INTO leo_ads.cohort_pattern (tenant_id, cohort_key, ad_id, cohort_size, impressions, objective_events) "
                "VALUES (:t, 'tag:travel@mobile', :ad, 60, 1000, :e)"
            ),
            {"t": world.tenant_id, "ad": ad_id, "e": events},
        )

    repo = StatsRepository(engine=test_engine)
    rows = repo.get_cohort_patterns(first.tenant_id, "tag:travel@mobile")

    assert [r["objective_events"] for r in rows.values()] == [30]
    assert repo.get_cohort_patterns(first.tenant_id, "tag:other@mobile") == {}
