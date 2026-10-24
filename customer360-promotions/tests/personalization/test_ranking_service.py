"""
Tests for the ranking service (no database: repositories are fakes).

Covers the decision order (disabled, no visitor, consent, hold-out, eligibility,
history, scoring), that different histories give different rankings, the
fail-open paths and what is written to the decision log.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from personalization import ranking_service as rs
from personalization.consent import GRANTED, OPT_IN, OPT_OUT
from personalization.decision_log import DecisionLog
from personalization.eligibility import RequestContext, shown_counter_name
from personalization.holdout import is_holdout
from personalization.identity import visitor_key
from personalization.ranking_service import RankingService
from personalization.scoring import AdStats
from personalization.settings import EffectiveSettings

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
TENANT = 7
PLACEMENT = {"tenant_id": TENANT, "placement_id": 70, "placement_key": "homepage_top"}

CONFIG = SimpleNamespace(
    personalization_time_budget_ms=25,
    eligibility_enforced=True,
    beta_prior_strength=20,
    affinity_half_life_days=14,
    affinity_weight_clip_min=0.5,
    affinity_weight_clip_max=2.0,
    low_history_event_threshold=5,
    new_ad_min_share=0.0,  # the reserved new-ad share has its own tests (test_exploration.py)
    new_ad_impression_threshold=1000,
    candidate_cache_ttl_seconds=60,
    settings_cache_ttl_seconds=60,
)


def ad(ad_id, advertiser_id, **extra):
    return {
        "ad_id": ad_id,
        "tenant_id": TENANT,
        "advertiser_id": advertiser_id,
        "score_weight": 1.0,
        "campaign_id": None,
        "rules": [],
        **extra,
    }


def profile_liking(advertiser_id, score=2.0):
    return {
        "affinities": {
            "advertiser": {
                str(advertiser_id): {"score": score, "n": 5, "last_at": NOW.isoformat()}
            }
        },
        "recent_dismissed": {},
        "event_count": 5,
    }


class FakeAds:
    def __init__(self, candidates, placement=PLACEMENT):
        self.candidates = candidates
        self.placement = placement

    def get_candidate_ads(self, tenant_key, placement_ref, max_candidates=100):
        return self.placement, list(self.candidates)


class FakeSettings:
    def __init__(self, **overrides):
        base = {"enabled": True, "holdout_pct": 0.0, "holdout_salt": "s", "consent_mode": OPT_OUT}
        self.value = EffectiveSettings(**{**base, **overrides})

    def resolve(self, tenant_id, placement_key=None, site_key=None):
        return self.value


class FakeConsent:
    def __init__(self, status=None):
        self.status = status

    def get_status(self, tenant_id, user_key):
        return self.status

    def has_open_erasure(self, tenant_id, user_key):
        return False


class FakeProfiles:
    def __init__(self, by_key=None, default=None, error=None):
        self.by_key = by_key or {}
        self.default = default
        self.error = error
        self.calls = []

    def get_profile(self, tenant_id, user_keys, timeout_ms=None):
        self.calls.append((tenant_id, list(user_keys)))
        if self.error:
            raise self.error
        return self.by_key.get(bytes(user_keys[0]), self.default)


class FakeStats:
    def __init__(self, stats=None):
        self.stats = stats or {}

    def get_ad_stats(self, tenant_id, ad_ids, window_days=14, timeout_ms=None):
        return {i: s for i, s in self.stats.items() if i in ad_ids}

    def get_cohort_patterns(self, tenant_id, cohort_key, ad_ids=None, timeout_ms=None):
        return {}


class Sink:
    def __init__(self):
        self.records = []

    def put(self, record):
        self.records.append(record)


def build(candidates, *, settings=None, consent=None, profiles=None, stats=None,
          hot_state=None, monotonic=None, config=CONFIG):
    sink = Sink()
    kwargs = {"monotonic": monotonic} if monotonic else {}
    service = RankingService(
        ad_repository=FakeAds(candidates),
        settings_resolver=settings or FakeSettings(),
        consent_repository=consent or FakeConsent(),
        profile_repository=profiles or FakeProfiles(),
        stats_repository=stats or FakeStats(),
        hot_state=hot_state,
        decisions=DecisionLog(sink),
        config=config,
        now=lambda: NOW,
        **kwargs,
    )
    return service, sink


def rank(service, leovid="visitor-1", **extra):
    return service.rank(
        tenant_key="demo", placement_ref="homepage_top", limit=5, leovid=leovid, **extra
    )


def ids(result):
    return [a["ad_id"] for a in result.ads]


CANDIDATES = [ad(1, 100, score_weight=2.0), ad(2, 200), ad(3, 300)]


# --------------------------------------------------------------------------
# Personalised ranking
# --------------------------------------------------------------------------


def test_two_visitors_with_different_histories_get_different_rankings():
    key_a = visitor_key(TENANT, "visitor-a")
    key_b = visitor_key(TENANT, "visitor-b")
    # Equal weights and well-measured ads (tight exploration), so only the visitor's
    # history can decide the order.
    candidates = [ad(1, 100), ad(2, 200), ad(3, 300)]
    measured = {i: AdStats(impressions=100_000, clicks=2_000) for i in (1, 2, 3)}
    service, _ = build(
        candidates,
        stats=FakeStats(measured),
        profiles=FakeProfiles({key_a: profile_liking(300, 4.0), key_b: profile_liking(200, 4.0)}),
    )

    a = rank(service, "visitor-a")
    b = rank(service, "visitor-b")

    assert a.group_code == b.group_code == rs.PERSONALISED
    assert ids(a)[0] == 3
    assert ids(b)[0] == 2
    assert ids(a) != ids(b)
    assert a.applied and b.applied


def test_personalised_decision_is_logged_with_rank_score_and_signals():
    key = visitor_key(TENANT, "visitor-a")
    service, sink = build(CANDIDATES, profiles=FakeProfiles({key: profile_liking(300, 4.0)}))

    result = rank(service, "visitor-a")

    (record,) = sink.records
    assert record["group_code"] == rs.PERSONALISED
    assert record["user_key_hash"] == key
    assert record["candidate_count"] == 3
    assert record["placement_id"] == 70
    assert [c["ad_id"] for c in record["chosen"]] == ids(result)
    assert [c["rank"] for c in record["chosen"]] == [1, 2, 3]
    assert 1 <= len(record["chosen"][0]["signals"]) <= 3


def test_limit_truncates_ads_and_chosen():
    key = visitor_key(TENANT, "v")
    service, sink = build(CANDIDATES, profiles=FakeProfiles({key: profile_liking(300)}))

    result = service.rank(tenant_key="demo", placement_ref="x", limit=2, leovid="v")

    assert len(result.ads) == 2
    assert len(sink.records[0]["chosen"]) == 2
    assert sink.records[0]["candidate_count"] == 3


def test_repeat_request_with_the_same_request_id_is_reproducible():
    key = visitor_key(TENANT, "v")
    service, _ = build(CANDIDATES, profiles=FakeProfiles({key: profile_liking(300)}))
    first = rank(service, "v", request_id="req-1")
    second = rank(service, "v", request_id="req-1")
    assert ids(first) == ids(second)


# --------------------------------------------------------------------------
# Default groups
# --------------------------------------------------------------------------


def test_visitor_with_no_history_gets_default_order():
    service, sink = build(CANDIDATES)

    result = rank(service)

    assert result.group_code == rs.NO_HISTORY
    assert result.reason == "no_history"
    assert ids(result) == [1, 2, 3]
    assert not result.applied
    assert sink.records[0]["user_key_hash"] == visitor_key(TENANT, "visitor-1")


def test_profile_with_zero_events_counts_as_no_history():
    key = visitor_key(TENANT, "v")
    service, _ = build(CANDIDATES, profiles=FakeProfiles({key: {"affinities": {}, "event_count": 0}}))
    assert rank(service, "v").group_code == rs.NO_HISTORY


def test_no_visitor_id_gets_default_order_and_no_key():
    profiles = FakeProfiles()
    service, sink = build(CANDIDATES, profiles=profiles)

    result = rank(service, leovid=None)

    assert result.group_code == rs.NO_HISTORY
    assert result.reason == "no_visitor_id"
    assert ids(result) == [1, 2, 3]
    assert profiles.calls == []
    assert sink.records[0]["user_key_hash"] is None


def test_disabled_scope_gets_default_order_and_never_reads_a_profile():
    profiles = FakeProfiles(default=profile_liking(300))
    service, sink = build(CANDIDATES, settings=FakeSettings(enabled=False), profiles=profiles)

    result = rank(service)

    assert result.group_code == rs.DISABLED
    assert ids(result) == [1, 2, 3]
    assert profiles.calls == []
    assert sink.records[0]["user_key_hash"] is None


def test_denied_consent_gets_default_order_without_a_key_or_profile_read():
    profiles = FakeProfiles(default=profile_liking(300))
    service, sink = build(CANDIDATES, consent=FakeConsent("denied"), profiles=profiles)

    result = rank(service)

    assert result.group_code == rs.NO_CONSENT
    assert ids(result) == [1, 2, 3]
    assert profiles.calls == []
    assert sink.records[0]["user_key_hash"] is None


def test_opt_in_mode_without_a_recorded_choice_is_not_personalised():
    profiles = FakeProfiles(default=profile_liking(300))
    service, _ = build(
        CANDIDATES, settings=FakeSettings(consent_mode=OPT_IN), consent=FakeConsent(None), profiles=profiles
    )
    assert rank(service).group_code == rs.NO_CONSENT
    assert profiles.calls == []


def test_opt_in_mode_with_granted_choice_is_personalised():
    service, _ = build(
        CANDIDATES,
        settings=FakeSettings(consent_mode=OPT_IN),
        consent=FakeConsent(GRANTED),
        profiles=FakeProfiles(default=profile_liking(300)),
    )
    assert rank(service).group_code == rs.PERSONALISED


def test_site_reported_denied_overrides_a_stored_grant():
    profiles = FakeProfiles(default=profile_liking(300))
    service, _ = build(CANDIDATES, consent=FakeConsent(GRANTED), profiles=profiles)

    result = rank(service, consent="denied")

    assert result.group_code == rs.NO_CONSENT
    assert profiles.calls == []


def test_holdout_visitor_gets_default_order_and_is_logged_as_holdout():
    holdout_visitor = next(
        f"v{i}" for i in range(500)
        if is_holdout(TENANT, visitor_key(TENANT, f"v{i}"), "s", 50.0)
    )
    profiles = FakeProfiles(default=profile_liking(300, 4.0))
    service, sink = build(CANDIDATES, settings=FakeSettings(holdout_pct=50.0), profiles=profiles)

    result = rank(service, holdout_visitor)

    assert result.group_code == rs.HOLDOUT
    assert result.reason == "holdout"
    assert ids(result) == [1, 2, 3]
    assert profiles.calls == []
    assert sink.records[0]["group_code"] == "holdout"


# --------------------------------------------------------------------------
# Eligibility applies to every group
# --------------------------------------------------------------------------


def test_ineligible_ads_are_dropped_in_the_default_path_too():
    candidates = [ad(1, 100, campaign_id=5, campaign_status="paused"), ad(2, 200)]
    service, _ = build(candidates, settings=FakeSettings(enabled=False))
    assert ids(rank(service)) == [2]


def test_ineligible_ads_are_dropped_before_scoring():
    key = visitor_key(TENANT, "v")
    candidates = [ad(1, 100, creative_status="archived"), ad(2, 200)]
    service, _ = build(candidates, profiles=FakeProfiles({key: profile_liking(100, 4.0)}))

    result = rank(service, "v")

    assert result.group_code == rs.PERSONALISED
    assert ids(result) == [2]


def test_targeting_mismatch_excludes_but_unknown_context_does_not():
    candidates = [ad(1, 100, rules=[{"countries": ["VN"]}]), ad(2, 200)]
    service, _ = build(candidates, settings=FakeSettings(enabled=False))

    assert ids(rank(service, context=RequestContext(country="US"))) == [2]
    assert ids(rank(service, context=RequestContext())) == [1, 2]


def test_no_eligible_ads_returns_empty_and_logs_an_empty_decision():
    service, sink = build([ad(1, 100, creative_status="archived")])

    result = rank(service, leovid=None)

    assert result.ads == []
    assert sink.records[0]["chosen"] == []
    assert sink.records[0]["candidate_count"] == 1


def test_frequency_cap_uses_the_visitors_counter(hot_state):
    key = visitor_key(TENANT, "v")
    hot_state.incr_counter(TENANT, key, shown_counter_name(1, NOW), ttl_seconds=3600, amount=3)
    candidates = [ad(1, 100, frequency_cap=3), ad(2, 200)]
    service, _ = build(candidates, hot_state=hot_state)

    assert ids(rank(service, "v")) == [2]
    # A visitor who has not seen it is still served it.
    assert ids(rank(service, "someone-else")) == [1, 2]


def test_frequency_cap_fails_open_when_hot_state_is_down(hot_state):
    hot_state.available = False
    candidates = [ad(1, 100, frequency_cap=1), ad(2, 200)]
    service, _ = build(candidates, hot_state=hot_state)

    assert ids(rank(service)) == [1, 2]


def test_eligibility_can_be_switched_off():
    config = SimpleNamespace(**{**CONFIG.__dict__, "eligibility_enforced": False})
    service, _ = build(
        [ad(1, 100, creative_status="archived")], settings=FakeSettings(enabled=False), config=config
    )
    assert ids(rank(service)) == [1]


# --------------------------------------------------------------------------
# Fail open
# --------------------------------------------------------------------------


def test_profile_read_failure_returns_default_order_as_fallback():
    service, sink = build(CANDIDATES, profiles=FakeProfiles(error=RuntimeError("db down")))

    result = rank(service)

    assert result.group_code == rs.FALLBACK
    assert result.reason == "error"
    assert ids(result) == [1, 2, 3]
    assert sink.records[0]["group_code"] == "default_fallback"
    assert sink.records[0]["user_key_hash"] is None


def test_unavailable_hot_state_falls_back_to_default_order_even_for_a_visitor_with_a_profile(hot_state):
    # FR-017: if behaviour data is unavailable, serve default ranking rather than a
    # profile that is missing the visitor's latest clicks and dismissals.
    key = visitor_key(TENANT, "v")
    candidates = [ad(1, 100, creative_status="archived"), ad(2, 200), ad(3, 300)]
    service, sink = build(
        candidates, profiles=FakeProfiles({key: profile_liking(300, 4.0)}), hot_state=hot_state
    )
    hot_state.available = False

    result = rank(service, "v")

    assert result.group_code == rs.FALLBACK
    assert result.reason == "redis_unavailable"
    assert ids(result) == [2, 3]  # default order, and still only the eligible ads
    assert sink.records[0]["group_code"] == "default_fallback"


def test_personalisation_resumes_when_hot_state_returns(hot_state):
    key = visitor_key(TENANT, "v")
    service, _ = build(
        [ad(1, 100), ad(2, 200), ad(3, 300)], profiles=FakeProfiles({key: profile_liking(300, 4.0)}), hot_state=hot_state
    )
    hot_state.available = False
    assert rank(service, "v").group_code == rs.FALLBACK

    hot_state.available = True

    assert rank(service, "v").group_code == rs.PERSONALISED


def test_the_error_fallback_still_drops_ineligible_ads():
    # Eligibility holds in every group: a failure must never serve an ad that
    # eligibility would have withheld.
    candidates = [ad(1, 100, creative_status="archived"), ad(2, 200), ad(3, 300, campaign_id=9, campaign_status="paused")]
    service, sink = build(candidates, profiles=FakeProfiles(error=RuntimeError("db down")))

    result = rank(service)

    assert result.group_code == rs.FALLBACK
    assert ids(result) == [2]
    assert [c["ad_id"] for c in sink.records[0]["chosen"]] == [2]


def test_the_time_budget_fallback_still_drops_ineligible_ads():
    key = visitor_key(TENANT, "v")
    ticks = iter(range(0, 1000, 100))
    candidates = [ad(1, 100, creative_status="archived"), ad(2, 200)]
    service, _ = build(
        candidates,
        profiles=FakeProfiles({key: profile_liking(200, 4.0)}),
        monotonic=lambda: next(ticks),
    )

    result = rank(service, "v")

    assert result.reason == "time_budget"
    assert ids(result) == [2]


def test_running_past_the_time_budget_returns_default_order_as_fallback():
    key = visitor_key(TENANT, "v")
    ticks = iter(range(0, 1000, 100))  # each monotonic() call advances 100 s: far over 25 ms
    service, _ = build(
        CANDIDATES,
        profiles=FakeProfiles({key: profile_liking(300, 4.0)}),
        monotonic=lambda: next(ticks),
    )

    result = rank(service, "v")

    assert result.group_code == rs.FALLBACK
    assert result.reason == "time_budget"
    assert ids(result) == [1, 2, 3]


def test_missing_key_secret_falls_back_without_failing(monkeypatch):
    from core.config import promotions_settings

    monkeypatch.setattr(promotions_settings, "environment", "production")
    monkeypatch.setattr(promotions_settings, "visitor_key_master_secret", None)
    service, _ = build(CANDIDATES)

    result = rank(service)

    assert result.group_code == rs.FALLBACK
    assert result.reason == "key_unavailable"
    assert ids(result) == [1, 2, 3]


def test_decision_log_overflow_never_fails_the_request():
    from personalization.buffered_writer import BufferOverflow

    class FullSink:
        def put(self, record):
            raise BufferOverflow("full")

    service, _ = build(CANDIDATES)
    service._decisions = DecisionLog(FullSink())

    result = rank(service, leovid=None)

    assert ids(result) == [1, 2, 3]
    assert service._decisions.dropped == 1


def test_unknown_placement_and_no_ads_returns_empty():
    service = RankingService(
        ad_repository=FakeAds([], placement=None),
        settings_resolver=FakeSettings(),
        consent_repository=FakeConsent(),
        profile_repository=FakeProfiles(),
        stats_repository=FakeStats(),
        hot_state=None,
        decisions=DecisionLog(Sink()),
        config=CONFIG,
        now=lambda: NOW,
    )

    result = rank(service)

    assert result.ads == []
    assert result.tenant_id is None
