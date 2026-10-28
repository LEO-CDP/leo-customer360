"""Scoring: how likely is this visitor to respond to this ad, in a form that can be explained.

Each eligible ad gets a score that is a PRODUCT of named factors, so every rank can
be explained (FR-013) and no model artefact or training pipeline is needed:

    score = objective_rate_lift x exploration x visitor_affinity x base_weight

``objective_rate_lift``
    The ad's smoothed rate of its OWN objective event divided by the placement's
    baseline for that same event. The objective follows the ad's campaign:
    awareness -> view, traffic -> click, conversions -> conversion; anything else
    (including no objective) -> click. Dividing by the baseline turns "2.6% clicks"
    and "60% views" into the same unit (how many times better than usual), which is
    what makes ads with different objectives comparable in one placement.

    The rate is a Beta posterior centred on the baseline (the prior has
    ``beta_prior_strength`` pseudo-impressions), so ads with little data are pulled
    towards the baseline instead of trusting a lucky 3/3.

``exploration``
    Thompson sampling: the rate used is a random draw from that posterior rather
    than its mean, and this factor is draw / mean. Ads with few impressions have a
    wide posterior and so get lucky draws often enough to be shown and measured;
    well-measured ads barely move. The draw is seeded from (request, ad), so a
    request is reproducible and two ads are never correlated.

``visitor_affinity``
    How this visitor has responded to features the ad shares with ads they saw
    (advertiser, ad type, format, tags). Responses decay by half-life; the result is
    squashed and clipped to ``[clip_min, clip_max]`` so one habit cannot dominate.

``base_weight``
    The ad's configured ``score_weight`` (a business prior; 1.0 when unset).

Everything here is pure: no I/O, no clock, no global randomness.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from personalization.features import ad_features

# Objective -> the event type that measures it. Anything else measures clicks.
# The campaign objective is free text in the catalogue (the seed data uses
# "conversion", "awareness", "reach" and "retargeting"), so the three objective
# types the engine knows each accept their obvious aliases. An objective that is
# not here, such as "retargeting", falls back to clicks.
OBJECTIVE_METRICS = {
    "awareness": "views",
    "reach": "views",
    "brand_awareness": "views",
    "traffic": "clicks",
    "conversions": "conversions",
    "conversion": "conversions",
}
DEFAULT_METRIC = "clicks"

# Used for a placement baseline until there is enough data to measure one.
DEFAULT_BASELINES = {"views": 0.50, "clicks": 0.02, "conversions": 0.002}
BASELINE_MIN_IMPRESSIONS = 100
MIN_BASELINE = 1e-4

# Signal names (the admin contract's Signal.name enum).
SIGNAL_OBJECTIVE_RATE_LIFT = "objective_rate_lift"
SIGNAL_VISITOR_AFFINITY = "visitor_affinity"
SIGNAL_COHORT_PATTERN = "cohort_pattern"
SIGNAL_EXPLORATION = "exploration"
SIGNAL_BASE_WEIGHT = "base_weight"
SIGNAL_PAGE_CONTEXT = "page_context"


def objective_metric(objective: Optional[str]) -> str:
    """The metric ("views", "clicks" or "conversions") an objective is measured by."""
    return OBJECTIVE_METRICS.get((objective or "").strip().lower(), DEFAULT_METRIC)


@dataclass(frozen=True)
class AdStats:
    """Counts of unflagged events for one ad over the measurement window."""

    impressions: int = 0
    views: int = 0
    clicks: int = 0
    conversions: int = 0

    def events(self, metric: str) -> int:
        return int(getattr(self, metric))


@dataclass(frozen=True)
class ScoringConfig:
    """Tunable constants (see core/config.py for their defaults)."""

    beta_prior_strength: float = 20.0
    half_life_days: float = 14.0
    clip_min: float = 0.5
    clip_max: float = 2.0
    affinity_scale: float = 0.15


@dataclass(frozen=True)
class Signal:
    """One named factor of a score, with a human-readable note."""

    name: str
    contribution: float
    note: str = ""


@dataclass(frozen=True)
class ScoredAd:
    ad_id: int
    score: float
    signals: tuple[Signal, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# Baselines and the Beta posterior
# --------------------------------------------------------------------------


def placement_baselines(
    candidates: Iterable[Mapping[str, Any]], stats_by_ad: Mapping[int, AdStats]
) -> dict[str, float]:
    """Per-metric baseline rate across the candidates that share that objective metric.

    A metric with fewer than BASELINE_MIN_IMPRESSIONS behind it keeps its default.
    """
    totals: dict[str, list[int]] = {m: [0, 0] for m in DEFAULT_BASELINES}
    for candidate in candidates:
        metric = objective_metric(candidate.get("campaign_objective"))
        stats = stats_by_ad.get(candidate["ad_id"])
        if stats is None:
            continue
        totals[metric][0] += min(stats.events(metric), stats.impressions)
        totals[metric][1] += stats.impressions

    baselines = dict(DEFAULT_BASELINES)
    for metric, (events, impressions) in totals.items():
        if impressions >= BASELINE_MIN_IMPRESSIONS:
            baselines[metric] = max(events / impressions, MIN_BASELINE)
    return baselines


def beta_params(events: int, impressions: int, baseline: float, strength: float) -> tuple[float, float]:
    """Beta(a, b) for an ad's rate: a prior centred on the baseline plus its observed counts."""
    impressions = max(int(impressions), 0)
    events = min(max(int(events), 0), impressions)
    baseline = min(max(baseline, MIN_BASELINE), 1 - MIN_BASELINE)
    return baseline * strength + events, (1 - baseline) * strength + (impressions - events)


# --------------------------------------------------------------------------
# Visitor affinity
# --------------------------------------------------------------------------


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def affinity_raw(
    features: Sequence[tuple[str, str]],
    affinities: Mapping[str, Mapping[str, Mapping[str, Any]]],
    now: datetime,
    half_life_days: float,
) -> tuple[float, int]:
    """Sum of decayed response scores over the ad's features, and how many matched.

    ``affinities`` is ``{dimension: {value: {"score": float, "last_at": iso8601}}}``.
    A score halves every ``half_life_days``; one with no usable time is not decayed.
    """
    total = 0.0
    matched = 0
    for dimension, value in features:
        entry = (affinities.get(dimension) or {}).get(value)
        if not entry:
            continue
        try:
            score = float(entry.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        last_at = _parse_time(entry.get("last_at"))
        if last_at is not None and half_life_days > 0:
            age_days = max((now - last_at).total_seconds() / 86_400.0, 0.0)
            score *= 0.5 ** (age_days / half_life_days)
        total += score
        matched += 1
    return total, matched


def affinity_factor(raw: float, config: ScoringConfig) -> float:
    """Squash a raw affinity into a multiplier in ``[clip_min, clip_max]`` (1.0 when raw is 0)."""
    return min(max(math.exp(config.affinity_scale * raw), config.clip_min), config.clip_max)


# --------------------------------------------------------------------------
# One ad, and a whole ranking
# --------------------------------------------------------------------------


def score_ad(
    candidate: Mapping[str, Any],
    *,
    stats: Optional[AdStats],
    baselines: Mapping[str, float],
    affinities: Optional[Mapping[str, Any]],
    now: datetime,
    config: ScoringConfig,
    request_id: str,
    extra_signals: Sequence[Signal] = (),
) -> ScoredAd:
    """Score one eligible ad for one request. Deterministic for a given request id."""
    stats = stats or AdStats()
    metric = objective_metric(candidate.get("campaign_objective"))
    baseline = max(baselines.get(metric, DEFAULT_BASELINES[metric]), MIN_BASELINE)

    a, b = beta_params(stats.events(metric), stats.impressions, baseline, config.beta_prior_strength)
    mean = a / (a + b)
    rng = random.Random(f"{request_id}:{candidate['ad_id']}")
    draw = rng.betavariate(a, b)

    lift = mean / baseline
    exploration = draw / mean if mean > 0 else 1.0

    raw, matched = affinity_raw(
        ad_features(candidate), affinities or {}, now, config.half_life_days
    )
    affinity = affinity_factor(raw, config)

    try:
        weight = float(candidate.get("score_weight") if candidate.get("score_weight") is not None else 1.0)
    except (TypeError, ValueError):
        weight = 1.0

    signals = [
        Signal(
            SIGNAL_OBJECTIVE_RATE_LIFT,
            lift,
            f"{metric[:-1]} rate {mean:.2%} vs placement baseline {baseline:.2%}",
        ),
        Signal(
            SIGNAL_EXPLORATION,
            exploration,
            f"sampled from {stats.impressions} impressions",
        ),
        Signal(
            SIGNAL_VISITOR_AFFINITY,
            affinity,
            f"{matched} matching feature{'s' if matched != 1 else ''}" if matched else "no history on this ad's features",
        ),
        Signal(SIGNAL_BASE_WEIGHT, weight, f"ad score_weight {weight:g}"),
        *extra_signals,
    ]

    score = 1.0
    for signal in signals:
        score *= signal.contribution
    return ScoredAd(ad_id=candidate["ad_id"], score=score, signals=tuple(signals))


# --------------------------------------------------------------------------
# Signals for visitors with little history of their own
# --------------------------------------------------------------------------

# A cohort row with fewer impressions than this says too little to act on.
COHORT_MIN_IMPRESSIONS = 100
# Each matching page keyword lifts an ad by this factor, up to PAGE_CONTEXT_MAX_MATCHES.
PAGE_CONTEXT_BOOST = 1.2
PAGE_CONTEXT_MAX_MATCHES = 2


def cohort_signal(
    candidate: Mapping[str, Any],
    *,
    stats: Optional[AdStats],
    cohort_row: Optional[Mapping[str, Any]],
    baselines: Mapping[str, float],
    config: ScoringConfig,
) -> Optional[Signal]:
    """How much better (or worse) similar visitors respond to this ad than visitors overall.

    Both rates are smoothed toward the ad's own overall rate, so a thin cohort row
    stays close to 1.0 instead of swinging the ranking. None when there is nothing
    usable (no row, too few impressions, or no overall rate to compare with).
    """
    if not cohort_row or int(cohort_row.get("impressions", 0)) < COHORT_MIN_IMPRESSIONS:
        return None
    metric = objective_metric(candidate.get("campaign_objective"))
    baseline = max(baselines.get(metric, DEFAULT_BASELINES[metric]), MIN_BASELINE)
    stats = stats or AdStats()
    a, b = beta_params(stats.events(metric), stats.impressions, baseline, config.beta_prior_strength)
    overall = a / (a + b)

    impressions = int(cohort_row["impressions"])
    events = min(int(cohort_row.get("objective_events", 0)), impressions)
    strength = config.beta_prior_strength
    cohort_rate = (events + strength * overall) / (impressions + strength)
    factor = min(max(cohort_rate / overall, config.clip_min), config.clip_max)
    return Signal(
        SIGNAL_COHORT_PATTERN,
        factor,
        f"similar visitors: {metric[:-1]} rate {cohort_rate:.2%} vs {overall:.2%} overall",
    )


def page_context_signal(
    candidate: Mapping[str, Any], keywords: Sequence[str]
) -> Optional[Signal]:
    """A boost when the page's keywords overlap the ad's targeting ``context_keywords``.

    Context only ever helps an ad; a page that does not match never excludes it.
    """
    if not keywords:
        return None
    wanted: set[str] = set()
    for rule in candidate.get("rules") or []:
        wanted |= {str(k).strip().lower() for k in (rule.get("context_keywords") or []) if str(k).strip()}
    matched = sorted(wanted & {k.strip().lower() for k in keywords})
    if not matched:
        return None
    factor = PAGE_CONTEXT_BOOST ** min(len(matched), PAGE_CONTEXT_MAX_MATCHES)
    return Signal(SIGNAL_PAGE_CONTEXT, factor, "page matches " + ", ".join(matched))


def rank_ads(
    candidates: Sequence[Mapping[str, Any]],
    *,
    stats_by_ad: Mapping[int, AdStats],
    affinities: Optional[Mapping[str, Any]],
    now: datetime,
    config: ScoringConfig,
    request_id: str,
    extra_signals: Optional[Mapping[int, Sequence[Signal]]] = None,
) -> list[ScoredAd]:
    """Score every candidate and order them best first.

    Ties break on the ad's ``score_weight`` (higher first) and then ``ad_id``
    (lower first), so the order is always deterministic.
    """
    baselines = placement_baselines(candidates, stats_by_ad)
    weight_of = {c["ad_id"]: float(c.get("score_weight") or 1.0) for c in candidates}
    scored = [
        score_ad(
            candidate,
            stats=stats_by_ad.get(candidate["ad_id"]),
            baselines=baselines,
            affinities=affinities,
            now=now,
            config=config,
            request_id=request_id,
            extra_signals=(extra_signals or {}).get(candidate["ad_id"], ()),
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda s: (-s.score, -weight_of[s.ad_id], s.ad_id))
    return scored
