"""Scoring: objective metrics, baselines, Beta smoothing, exploration, affinity, ordering."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from personalization import scoring
from personalization.scoring import (
    DEFAULT_BASELINES,
    AdStats,
    ScoredAd,
    ScoringConfig,
    Signal,
    affinity_factor,
    affinity_raw,
    beta_params,
    objective_metric,
    placement_baselines,
    rank_ads,
    score_ad,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
CONFIG = ScoringConfig()


def ad(ad_id=1, objective="traffic", weight=1.0, advertiser_id=None, **extra):
    return {
        "ad_id": ad_id,
        "campaign_objective": objective,
        "score_weight": weight,
        "advertiser_id": advertiser_id,
        **extra,
    }


def signal(scored: ScoredAd, name: str) -> Signal:
    return next(s for s in scored.signals if s.name == name)


# --------------------------------------------------------------------------
# Objective -> metric
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objective,metric",
    [
        ("awareness", "views"),
        ("traffic", "clicks"),
        ("conversions", "conversions"),
        ("Awareness", "views"),  # case and spaces do not matter
        (" CONVERSIONS ", "conversions"),
        ("conversion", "conversions"),  # aliases of the three known types (used by the seed data)
        ("reach", "views"),
        ("brand_awareness", "views"),
        ("retargeting", "clicks"),  # anything else measures clicks
        ("retention", "clicks"),
        ("", "clicks"),
        (None, "clicks"),
    ],
)
def test_the_objective_decides_the_metric(objective, metric):
    assert objective_metric(objective) == metric


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------


def test_without_enough_data_the_defaults_apply():
    candidates = [ad(1, "traffic"), ad(2, "awareness")]
    stats = {1: AdStats(impressions=50, clicks=40), 2: AdStats(impressions=10)}
    assert placement_baselines(candidates, stats) == DEFAULT_BASELINES


def test_a_baseline_is_measured_from_the_ads_that_share_its_metric():
    candidates = [ad(1, "traffic"), ad(2, "traffic"), ad(3, "awareness")]
    stats = {
        1: AdStats(impressions=500, clicks=10),
        2: AdStats(impressions=500, clicks=30),
        3: AdStats(impressions=1000, views=800, clicks=900),  # its clicks are not a clicks baseline
    }
    baselines = placement_baselines(candidates, stats)
    assert baselines["clicks"] == pytest.approx(40 / 1000)
    assert baselines["views"] == pytest.approx(0.8)
    assert baselines["conversions"] == DEFAULT_BASELINES["conversions"]


def test_events_can_never_exceed_impressions_in_a_baseline():
    baselines = placement_baselines(
        [ad(1, "traffic")], {1: AdStats(impressions=200, clicks=5_000)}
    )
    assert baselines["clicks"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Beta smoothing
# --------------------------------------------------------------------------


def test_with_no_data_the_posterior_mean_is_the_baseline():
    a, b = beta_params(0, 0, baseline=0.02, strength=20)
    assert a / (a + b) == pytest.approx(0.02)


def test_lots_of_data_overrides_the_prior():
    a, b = beta_params(events=3_000, impressions=10_000, baseline=0.02, strength=20)
    assert a / (a + b) == pytest.approx(0.30, abs=0.005)


def test_a_few_lucky_events_are_pulled_towards_the_baseline():
    a, b = beta_params(events=3, impressions=3, baseline=0.02, strength=20)
    mean = a / (a + b)
    assert 0.02 < mean < 0.20  # nowhere near "100% click rate"


def test_malformed_counts_are_clamped():
    a, b = beta_params(events=99, impressions=10, baseline=0.5, strength=20)
    assert a > 0 and b > 0
    a, b = beta_params(events=-5, impressions=-5, baseline=0.5, strength=20)
    assert a == pytest.approx(10) and b == pytest.approx(10)


# --------------------------------------------------------------------------
# Affinity
# --------------------------------------------------------------------------


def _affinities(score, days_ago=0, dimension="advertiser", value="7"):
    last_at = (NOW - timedelta(days=days_ago)).isoformat()
    return {dimension: {value: {"score": score, "n": 3, "last_at": last_at}}}


FEATURES = [("advertiser", "7"), ("ad_type", "native")]


def test_a_response_halves_every_half_life():
    fresh, _ = affinity_raw(FEATURES, _affinities(2.0, days_ago=0), NOW, 14)
    aged, _ = affinity_raw(FEATURES, _affinities(2.0, days_ago=14), NOW, 14)
    old, _ = affinity_raw(FEATURES, _affinities(2.0, days_ago=28), NOW, 14)
    assert fresh == pytest.approx(2.0)
    assert aged == pytest.approx(1.0)
    assert old == pytest.approx(0.5)


def test_an_entry_with_no_usable_time_is_not_decayed():
    affinities = {"advertiser": {"7": {"score": 2.0, "last_at": "not a time"}}}
    assert affinity_raw(FEATURES, affinities, NOW, 14) == (2.0, 1)
    affinities = {"advertiser": {"7": {"score": 2.0}}}
    assert affinity_raw(FEATURES, affinities, NOW, 14) == (2.0, 1)


def test_only_matching_features_count_and_they_are_summed():
    affinities = {
        "advertiser": {"7": {"score": 1.0}},
        "ad_type": {"native": {"score": 0.5}, "display": {"score": 9.0}},
        "tag": {"sale": {"score": 9.0}},
    }
    assert affinity_raw(FEATURES, affinities, NOW, 14) == (1.5, 2)
    assert affinity_raw(FEATURES, {}, NOW, 14) == (0.0, 0)


def test_a_dismissal_makes_the_raw_affinity_negative():
    raw, matched = affinity_raw(FEATURES, _affinities(-2.0), NOW, 14)
    assert raw < 0 and matched == 1


def test_unusable_scores_are_ignored():
    affinities = {"advertiser": {"7": {"score": "lots"}}}
    assert affinity_raw(FEATURES, affinities, NOW, 14) == (0.0, 0)


def test_the_factor_is_neutral_at_zero_and_clipped_at_both_ends():
    assert affinity_factor(0.0, CONFIG) == pytest.approx(1.0)
    assert affinity_factor(100.0, CONFIG) == CONFIG.clip_max == 2.0
    assert affinity_factor(-100.0, CONFIG) == CONFIG.clip_min == 0.5
    assert affinity_factor(1.0, CONFIG) > 1.0 > affinity_factor(-1.0, CONFIG)
    assert affinity_factor(1.0, CONFIG) == pytest.approx(math.exp(0.15))


# --------------------------------------------------------------------------
# One ad
# --------------------------------------------------------------------------


def _score(candidate, request_id="req-1", stats=None, affinities=None, baselines=None, extra=()):
    return score_ad(
        candidate,
        stats=stats,
        baselines=baselines or DEFAULT_BASELINES,
        affinities=affinities,
        now=NOW,
        config=CONFIG,
        request_id=request_id,
        extra_signals=extra,
    )


def test_a_score_is_reproducible_for_the_same_request_and_ad():
    stats = AdStats(impressions=200, clicks=8)
    assert _score(ad(), "req-A", stats) == _score(ad(), "req-A", stats)


def test_the_exploration_draw_differs_between_requests_and_between_ads():
    draws_by_request = {signal(_score(ad(1), f"req-{i}"), "exploration").contribution for i in range(20)}
    assert len(draws_by_request) > 15
    assert signal(_score(ad(1), "req-1"), "exploration").contribution != signal(
        _score(ad(2), "req-1"), "exploration"
    ).contribution


def test_the_score_is_the_product_of_its_named_signals():
    scored = _score(ad(weight=1.5, advertiser_id=7), stats=AdStats(impressions=500, clicks=20),
                    affinities=_affinities(1.0))
    names = [s.name for s in scored.signals]
    assert names == ["objective_rate_lift", "exploration", "visitor_affinity", "base_weight"]
    assert scored.score == pytest.approx(math.prod(s.contribution for s in scored.signals))
    assert all(s.note for s in scored.signals)


def test_extra_signals_multiply_in_and_are_reported():
    base = _score(ad())
    boosted = _score(ad(), extra=[Signal("page_context", 1.2, "keywords overlap")])
    assert boosted.score == pytest.approx(base.score * 1.2)
    assert boosted.signals[-1].name == "page_context"


def test_a_missing_or_malformed_weight_counts_as_one():
    assert signal(_score(ad(weight=None)), "base_weight").contribution == 1.0
    assert signal(_score(ad(weight="heavy")), "base_weight").contribution == 1.0
    assert signal(_score(ad(weight=2.5)), "base_weight").contribution == 2.5


def test_lift_is_comparable_across_objectives():
    """An ad twice as good as usual at its own objective gets the same lift, whatever the metric."""
    baselines = {"clicks": 0.02, "views": 0.30, "conversions": 0.002}
    traffic = _score(ad(1, "traffic"), stats=AdStats(impressions=10_000, clicks=400), baselines=baselines)
    awareness = _score(ad(2, "awareness"), stats=AdStats(impressions=10_000, views=6_000), baselines=baselines)
    conversion = _score(ad(3, "conversions"), stats=AdStats(impressions=10_000, conversions=40), baselines=baselines)

    lifts = [signal(s, "objective_rate_lift").contribution for s in (traffic, awareness, conversion)]
    assert lifts == pytest.approx([2.0, 2.0, 2.0], rel=0.02)


def test_a_positive_affinity_raises_the_score_and_a_dismissal_lowers_it():
    candidate = ad(advertiser_id=7)
    neutral = signal(_score(candidate), "visitor_affinity").contribution
    liked = signal(_score(candidate, affinities=_affinities(2.0)), "visitor_affinity").contribution
    disliked = signal(_score(candidate, affinities=_affinities(-2.0)), "visitor_affinity").contribution
    assert neutral == pytest.approx(1.0)
    assert disliked < neutral < liked


# --------------------------------------------------------------------------
# Exploration
# --------------------------------------------------------------------------


def _share_above_new_ad(target_stats, requests=600):
    """How often a measured ad ranks above a brand-new one (fixed request ids: deterministic).

    The placement's baseline is measured from the candidates themselves, so a lone
    measured ad would always look "typical". Two reference ads at 2% over 20,000
    impressions each give the placement a realistic usual rate to compare against.
    """
    candidates = [ad(1), ad(2), ad(10), ad(11)]  # 1 = target, 2 = brand new, 10/11 = references
    stats = {
        1: target_stats,
        10: AdStats(impressions=20_000, clicks=400),
        11: AdStats(impressions=20_000, clicks=400),
    }
    above = 0
    for i in range(requests):
        ranked = rank_ads(candidates, stats_by_ad=stats, affinities=None, now=NOW,
                          config=CONFIG, request_id=f"req-{i}")
        order = [s.ad_id for s in ranked]
        above += order.index(1) < order.index(2)
    return above / requests


def test_a_proven_strong_ad_usually_beats_a_new_one_but_the_new_one_still_gets_chances():
    share = _share_above_new_ad(AdStats(impressions=10_000, clicks=400))  # 4% vs ~2% usual
    assert 0.70 < share < 0.92


def test_a_typical_ad_beats_a_new_one_less_often_than_a_strong_one_does():
    strong = _share_above_new_ad(AdStats(impressions=10_000, clicks=400))
    typical = _share_above_new_ad(AdStats(impressions=10_000, clicks=200))
    weak = _share_above_new_ad(AdStats(impressions=10_000, clicks=50))
    assert strong > typical > weak
    assert 0.60 < typical < 0.85


def test_a_proven_weak_ad_barely_beats_a_new_one():
    share = _share_above_new_ad(AdStats(impressions=10_000, clicks=50))  # 0.5% vs ~2% usual
    assert share < 0.65


def test_two_unmeasured_ads_split_roughly_evenly():
    share = _share_above_new_ad(AdStats())
    assert 0.40 < share < 0.60


def test_a_new_ad_always_gets_a_real_share_of_the_top_slot():
    """SC-007: new ads must be shown enough to be measured (at least 5% of the time)."""
    candidates = [ad(1), ad(2)]
    stats = {1: AdStats(impressions=10_000, clicks=400)}  # a proven ad against a brand-new one
    top = [
        rank_ads(candidates, stats_by_ad=stats, affinities=None, now=NOW,
                 config=CONFIG, request_id=f"req-{i}")[0].ad_id
        for i in range(1_000)
    ]
    assert top.count(2) / 1_000 > 0.05


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_a_visitors_affinity_decides_between_otherwise_identical_ads():
    candidates = [ad(1, advertiser_id=7), ad(2, advertiser_id=8)]
    stats = {1: AdStats(impressions=10_000, clicks=200), 2: AdStats(impressions=10_000, clicks=200)}
    affinities = _affinities(2.0, value="7")

    first_place = [
        rank_ads(candidates, stats_by_ad=stats, affinities=affinities, now=NOW,
                 config=CONFIG, request_id=f"req-{i}")[0].ad_id
        for i in range(200)
    ]
    assert first_place.count(1) / 200 > 0.9


def test_a_ranking_is_deterministic_for_a_request():
    candidates = [ad(i) for i in range(1, 6)]
    runs = [
        [s.ad_id for s in rank_ads(candidates, stats_by_ad={}, affinities=None, now=NOW,
                                   config=CONFIG, request_id="req-fixed")]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


def test_ties_break_on_weight_then_ad_id(monkeypatch):
    def tied(candidate, **_kwargs):
        return ScoredAd(ad_id=candidate["ad_id"], score=1.0)

    monkeypatch.setattr(scoring, "score_ad", tied)
    candidates = [ad(3, weight=1.0), ad(1, weight=1.0), ad(2, weight=5.0), ad(4, weight=1.0)]

    ranked = rank_ads(candidates, stats_by_ad={}, affinities=None, now=NOW,
                      config=CONFIG, request_id="r")

    assert [s.ad_id for s in ranked] == [2, 1, 3, 4]


def test_extra_signals_are_applied_per_ad_when_ranking():
    candidates = [ad(1), ad(2)]
    boost = {2: [Signal("page_context", 100.0, "strong context match")]}
    ranked = rank_ads(candidates, stats_by_ad={}, affinities=None, now=NOW,
                      config=CONFIG, request_id="r", extra_signals=boost)
    assert ranked[0].ad_id == 2
    assert ranked[0].signals[-1].name == "page_context"
