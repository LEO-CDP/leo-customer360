"""
New ads get a guaranteed minimum share of first position, only among eligible ads
and only for personalised visitors. No database: repositories are fakes, and the
request ids are fixed, so every count below is deterministic.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from personalization.decision_log import DecisionLog
from personalization.identity import visitor_key
from personalization.ranking_service import PERSONALISED, RankingService
from personalization.scoring import AdStats
from personalization.settings import EffectiveSettings

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
TENANT = 7
PLACEMENT = {"tenant_id": TENANT, "placement_id": 70, "placement_key": "homepage_top"}
REQUESTS = 4_000


def config(**overrides):
    base = dict(
        personalization_time_budget_ms=10_000,
        eligibility_enforced=True,
        beta_prior_strength=20,
        affinity_half_life_days=14,
        affinity_weight_clip_min=0.5,
        affinity_weight_clip_max=2.0,
        low_history_event_threshold=5,
        new_ad_min_share=0.05,
        new_ad_impression_threshold=1000,
        candidate_cache_ttl_seconds=60,
        settings_cache_ttl_seconds=60,
    )
    return SimpleNamespace(**{**base, **overrides})


def ad(ad_id, **extra):
    return {"ad_id": ad_id, "tenant_id": TENANT, "score_weight": 1.0, "campaign_id": None, "rules": [],
            "advertiser_id": ad_id, **extra}


class Ads:
    def __init__(self, candidates):
        self.candidates = candidates

    def get_candidate_ads(self, *_args, **_kwargs):
        return PLACEMENT, list(self.candidates)


class Settings:
    def __init__(self, holdout_pct=0.0):
        self.value = EffectiveSettings(enabled=True, holdout_pct=holdout_pct, holdout_salt="s", consent_mode="opt_out")

    def resolve(self, *_args, **_kwargs):
        return self.value


class Consent:
    def get_status(self, *_args):
        return None

    def has_open_erasure(self, *_args):
        return False


class Profiles:
    def __init__(self, advertiser_liked):
        self.profile = {
            "affinities": {"advertiser": {str(advertiser_liked): {"score": 5.0, "n": 9, "last_at": NOW.isoformat()}}},
            "event_count": 9,
        }

    def get_profile(self, *_args, **_kwargs):
        return self.profile

    def linked_keys(self, tenant_id, key):
        return [key]


class Stats:
    def __init__(self, by_ad):
        self.by_ad = by_ad

    def get_ad_stats(self, tenant_id, ad_ids, **_kwargs):
        return {i: s for i, s in self.by_ad.items() if i in ad_ids}

    def get_cohort_patterns(self, *_args, **_kwargs):
        return {}


class Sink:
    def put(self, _record):
        pass


def service(candidates, stats, *, holdout_pct=0.0, **config_overrides):
    return RankingService(
        ad_repository=Ads(candidates), settings_resolver=Settings(holdout_pct), consent_repository=Consent(),
        profile_repository=Profiles(advertiser_liked=1), stats_repository=Stats(stats), hot_state=None,
        decisions=DecisionLog(Sink()), config=config(**config_overrides), now=lambda: NOW,
    )


ESTABLISHED = AdStats(impressions=100_000, clicks=2_000)
NEW = AdStats(impressions=10)

# Ad 1 is the visitor's favourite and well measured; ad 3 is brand new.
STATS = {1: ESTABLISHED, 2: ESTABLISHED, 3: NEW}


def run(svc, visitor="v", requests=REQUESTS):
    results = []
    for i in range(requests):
        results.append(svc.rank(tenant_key="demo", placement_ref="x", limit=3, leovid=visitor, request_id=f"req-{i}"))
    return results


def first_ad(result):
    return result.ads[0]["ad_id"]


def test_a_new_ad_gets_at_least_the_reserved_share_of_first_position():
    results = run(service([ad(1), ad(2), ad(3)], STATS))

    first = sum(1 for r in results if first_ad(r) == 3)

    assert all(r.group_code == PERSONALISED for r in results)
    assert first / REQUESTS >= 0.05
    assert first / REQUESTS < 0.20  # a reserved share, not a takeover


def test_the_reservation_only_ever_adds_to_the_new_ads_share():
    # Sampling already lets a new ad lead now and then; the reservation is a floor.
    without = run(service([ad(1), ad(2), ad(3)], STATS, new_ad_min_share=0.0))
    with_it = run(service([ad(1), ad(2), ad(3)], STATS))

    led_without = [i for i, r in enumerate(without) if first_ad(r) == 3]
    led_with = [i for i, r in enumerate(with_it) if first_ad(r) == 3]
    assert set(led_without) <= set(led_with)  # never takes the lead away from a new ad
    assert len(led_with) > len(led_without)


def test_an_ineligible_new_ad_never_gets_the_share():
    candidates = [ad(1), ad(2), ad(3, creative_status="archived")]
    results = run(service(candidates, STATS))

    assert all(3 not in [a["ad_id"] for a in r.ads] for r in results)


def test_an_ad_over_the_impression_threshold_is_not_new():
    stats = {1: ESTABLISHED, 2: ESTABLISHED, 3: AdStats(impressions=1_000, clicks=0)}
    results = run(service([ad(1), ad(2), ad(3)], stats), requests=1_500)

    assert sum(1 for r in results if first_ad(r) == 3) == 0


def test_the_promoted_ad_is_labelled_with_the_exploration_signal():
    results = run(service([ad(1), ad(2), ad(3)], STATS), requests=600)
    promoted = [
        r for r in results
        if first_ad(r) == 3
        and any(s.name == "exploration" and "reserved share" in s.note for s in r.scored[0].signals)
    ]

    assert promoted
    signals = {s.name: s for s in promoted[0].scored[0].signals}
    assert "fewer than 1000 impressions" in signals["exploration"].note
    assert signals["exploration"].contribution >= 1.0


def test_the_choice_is_reproducible_for_the_same_request_id():
    svc = service([ad(1), ad(2), ad(3)], STATS)
    first = [first_ad(svc.rank(tenant_key="demo", placement_ref="x", limit=3, leovid="v", request_id=f"r{i}")) for i in range(300)]
    second = [first_ad(svc.rank(tenant_key="demo", placement_ref="x", limit=3, leovid="v", request_id=f"r{i}")) for i in range(300)]
    assert first == second


def test_when_the_top_ad_is_already_new_nothing_changes():
    stats = {1: NEW, 2: ESTABLISHED, 3: ESTABLISHED}  # the visitor's favourite is the new ad
    without = run(service([ad(1), ad(2), ad(3)], stats, new_ad_min_share=0.0), requests=600)
    with_it = run(service([ad(1), ad(2), ad(3)], stats), requests=600)

    for plain, reserved in zip(without, with_it):
        if first_ad(plain) == 1:
            assert first_ad(reserved) == 1  # a new ad that already leads is never displaced


def test_holdout_visitors_keep_default_order_whatever_the_reservation():
    from personalization.holdout import is_holdout

    held_out = next(f"h{i}" for i in range(200) if is_holdout(TENANT, visitor_key(TENANT, f"h{i}"), "s", 100.0))
    results = run(service([ad(1, score_weight=3.0), ad(2), ad(3)], STATS, holdout_pct=100.0), visitor=held_out, requests=800)

    assert all(r.group_code == "holdout" for r in results)
    assert all(first_ad(r) == 1 for r in results)  # default order: highest score_weight first
