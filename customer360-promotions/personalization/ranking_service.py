"""Ranking service: decide the order of ads for one serve request.

Order of decisions (cheap and certain first):

    settings   -> personalisation off for this scope?          default_disabled
    identity   -> no visitor id (or no key secret)?            default_no_history / default_fallback
    consent    -> visitor may not be profiled?                 default_no_consent
    hold-out   -> visitor is in the measurement hold-out?      holdout
    eligibility-> drop ads that may not be served (ALL groups)
    history    -> visitor has no profile yet?                  default_no_history
    scoring    -> rank the eligible ads                        personalised

Every group other than ``personalised`` serves the eligible ads in their default
order (score_weight, then ad_id). Anything unexpected after the candidates are
loaded, including running past the time budget, returns that same default order
with group ``default_fallback``: ranking never raises and never makes an ad
request fail because personalisation did.

Only a key derived by ``identity.visitor_key`` is ever used, and it is only
attached to the stored decision (and used to read a profile) when the visitor is
eligible under their consent state.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence
from uuid import uuid4

from personalization import decision_log
from personalization import signals as signals_module
from personalization.consent import DENIED, GRANTED, is_eligible
from personalization.eligibility import (
    RequestContext,
    filter_eligible,
    shown_counter_name,
)
from personalization.holdout import is_holdout
from personalization.hot_state import HotStateUnavailable
from personalization.identity import VisitorKeyUnavailable, describe_error, visitor_key
from personalization.cohorts import cohort_key
from personalization.scoring import (
    SIGNAL_EXPLORATION,
    AdStats,
    ScoredAd,
    ScoringConfig,
    Signal,
    cohort_signal,
    page_context_signal,
    placement_baselines,
    rank_ads,
)

logger = logging.getLogger(__name__)

PERSONALISED = "personalised"
HOLDOUT = "holdout"
NO_HISTORY = "default_no_history"
NO_CONSENT = "default_no_consent"
DISABLED = "default_disabled"
FALLBACK = "default_fallback"


@dataclass
class RankingResult:
    """The outcome of one ranking: ordered candidates plus how they were chosen."""

    request_id: str
    group_code: str
    reason: Optional[str]
    ads: list[dict[str, Any]]
    placement: Optional[dict[str, Any]] = None
    tenant_id: Optional[int] = None
    scored: list[ScoredAd] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return self.group_code == PERSONALISED


class RankingService:
    """Ranks the ads of one placement for one visitor. See the module docstring."""

    def __init__(
        self,
        *,
        ad_repository: Any,
        settings_resolver: Any,
        consent_repository: Any,
        profile_repository: Any,
        stats_repository: Any,
        hot_state: Any,
        decisions: Any,
        config: Any,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ads = ad_repository
        self._settings = settings_resolver
        self._consent = consent_repository
        self._profiles = profile_repository
        self._stats = stats_repository
        self._hot_state = hot_state
        self._decisions = decisions
        self._config = config
        self._now = now
        self._monotonic = monotonic

    # ------------------------------------------------------------------

    def rank(
        self,
        *,
        tenant_key: str,
        placement_ref: str,
        limit: int,
        leovid: Optional[str] = None,
        site: Optional[str] = None,
        consent: Optional[str] = None,
        context: Optional[RequestContext] = None,
        request_id: Optional[str] = None,
    ) -> RankingResult:
        """Rank for one request. ``consent`` is the site-reported state ("granted"/"denied")."""
        request_id = request_id or str(uuid4())

        # Loading candidates is the one step whose failure is the caller's problem:
        # without them there is nothing to serve, personalised or not.
        placement, candidates = self._ads.get_candidate_ads(tenant_key, placement_ref)
        # The time budget bounds what personalisation ADDS to a serve, so it starts
        # once the candidates (which every request needs) have been loaded.
        started = self._monotonic()
        tenant_id = _tenant_id_of(placement, candidates)
        if tenant_id is None:
            return RankingResult(request_id, NO_HISTORY, "no_eligible_ads", [], placement)

        default_order = list(candidates)
        context = context or RequestContext()
        try:
            return self._rank(
                tenant_id=tenant_id,
                placement=placement,
                candidates=default_order,
                limit=limit,
                leovid=leovid,
                site=site,
                consent=consent,
                context=context,
                request_id=request_id,
                started=started,
            )
        except Exception as exc:  # noqa: BLE001 - ranking must never fail the request
            # Type names only: the message or traceback of a database error can quote
            # the visitor key the query ran for.
            logger.error("ranking failed (%s); serving default order", describe_error(exc))
            return self._finish(
                tenant_id, placement, request_id, FALLBACK, "error",
                self._fallback_eligible(default_order, context), default_order, None, [], limit,
            )

    def _fallback_eligible(
        self, candidates: list[dict[str, Any]], context: RequestContext
    ) -> list[dict[str, Any]]:
        """Eligibility for the failure path: pure, so it cannot fail for the same reason.

        Eligibility holds in every group, including ``default_fallback``. Without
        the hot state the per-visitor frequency cap cannot be checked, so only the
        rules that need no visitor state are applied.
        """
        if not self._config.eligibility_enforced:
            return candidates
        eligible, _ = filter_eligible(candidates, context, self._now())
        return eligible

    # ------------------------------------------------------------------

    def _rank(
        self,
        *,
        tenant_id: int,
        placement: Optional[dict[str, Any]],
        candidates: list[dict[str, Any]],
        limit: int,
        leovid: Optional[str],
        site: Optional[str],
        consent: Optional[str],
        context: RequestContext,
        request_id: str,
        started: float,
    ) -> RankingResult:
        now = self._now()
        placement_key = (placement or {}).get("placement_key")
        settings = self._settings.resolve(tenant_id, placement_key, site)

        user_key: Optional[bytes] = None
        group, reason = PERSONALISED, None

        if not settings.enabled:
            group, reason = DISABLED, "disabled"
        elif not leovid:
            group, reason = NO_HISTORY, "no_visitor_id"
        else:
            try:
                user_key = visitor_key(tenant_id, leovid)
            except VisitorKeyUnavailable:
                group, reason = FALLBACK, "key_unavailable"
            except ValueError:
                group, reason = NO_HISTORY, "invalid_visitor_id"

        if user_key is not None and group == PERSONALISED:
            if self._consent.has_open_erasure(tenant_id, user_key):
                status = DENIED  # a site-reported "granted" cannot override a pending erasure
            elif consent in (GRANTED, DENIED):
                status = consent
            else:
                status = self._consent.get_status(tenant_id, user_key)
            decision = is_eligible(status, settings.consent_mode)
            if not decision.eligible:
                user_key, group, reason = None, decision.reason, "no_consent"
            elif is_holdout(tenant_id, user_key, settings.holdout_salt, settings.holdout_pct):
                group, reason = HOLDOUT, "holdout"

        eligible = self._eligible(tenant_id, user_key, candidates, context, now)

        if group == PERSONALISED:
            outcome = self._personalise(
                tenant_id, user_key, eligible, candidates, placement, context,
                now, request_id, started, limit,
            )
            if outcome is not None:
                return outcome
            group, reason = NO_HISTORY, "no_history"

        if not eligible and reason is None:
            reason = "no_eligible_ads"
        return self._finish(
            tenant_id, placement, request_id, group, reason,
            eligible, candidates, user_key, [], limit,
        )

    # ------------------------------------------------------------------

    def _eligible(
        self,
        tenant_id: int,
        user_key: Optional[bytes],
        candidates: list[dict[str, Any]],
        context: RequestContext,
        now: datetime,
    ) -> list[dict[str, Any]]:
        if not self._config.eligibility_enforced:
            return candidates

        shown: dict[int, int] = {}
        capped = [c for c in candidates if (c.get("frequency_cap") or 0) > 0]
        if user_key is not None and capped:
            names = {shown_counter_name(c["ad_id"], now): c["ad_id"] for c in capped}
            try:
                counts = self._hot_state.get_counters(tenant_id, user_key, list(names))
                shown = {names[name]: count for name, count in counts.items()}
            except HotStateUnavailable:
                logger.warning("frequency caps not enforced: hot state unavailable")

        eligible, _ = filter_eligible(candidates, context, now, shown)
        return eligible

    def _personalise(
        self,
        tenant_id: int,
        user_key: bytes,
        eligible: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        placement: Optional[dict[str, Any]],
        context: RequestContext,
        now: datetime,
        request_id: str,
        started: float,
        limit: int,
    ) -> Optional[RankingResult]:
        """Rank the eligible ads for a visitor who may be personalised.

        Returns None when there is nothing to personalise with (no history of their
        own, no cohort pattern and no page context): the caller then serves default
        order as ``default_no_history``.
        """
        config = self._config
        keys = self._linked_keys(tenant_id, user_key)
        now_ts = now.timestamp()
        try:
            recent = self._recent_signals(tenant_id, keys)
        except HotStateUnavailable:
            # Behaviour data is unavailable: default ranking rather than a profile that
            # is missing the visitor's latest clicks and dismissals (FR-017).
            return self._finish(
                tenant_id, placement, request_id, FALLBACK, "redis_unavailable",
                eligible, candidates, user_key, [], limit,
            )
        profile = self._profiles.get_profile(tenant_id, keys, timeout_ms=self._remaining_ms(started))
        signals = signals_module.in_window(recent, now_ts)
        fresh = signals_module.not_yet_in_profile(signals, (profile or {}).get("updated_at"))

        has_history = bool(profile and profile.get("event_count")) or bool(fresh)
        event_total = int((profile or {}).get("event_count") or 0) + len(fresh)

        profile = dict(profile or {})
        profile["affinities"] = signals_module.merge_affinities(
            profile.get("affinities"), fresh, now_ts, config.affinity_half_life_days
        )
        withheld = signals_module.dismissed_ad_ids(signals, profile.get("recent_dismissed"), now_ts)
        eligible = [c for c in eligible if c["ad_id"] not in withheld]

        stats = self._stats.get_ad_stats(
            tenant_id, [c["ad_id"] for c in eligible], timeout_ms=self._remaining_ms(started)
        )
        scoring_config = ScoringConfig(
            beta_prior_strength=config.beta_prior_strength,
            half_life_days=config.affinity_half_life_days,
            clip_min=config.affinity_weight_clip_min,
            clip_max=config.affinity_weight_clip_max,
        )

        extra = self._extra_signals(
            tenant_id, eligible, stats, profile["affinities"], context, scoring_config,
            low_history=event_total < config.low_history_event_threshold,
            started=started,
        )
        if not has_history and not any(
            s.contribution != 1.0 for found in extra.values() for s in found
        ):
            return None

        scored = rank_ads(
            eligible,
            stats_by_ad=stats,
            affinities=profile["affinities"],
            now=now,
            config=scoring_config,
            request_id=request_id,
            extra_signals=extra,
        )
        scored = self._reserve_new_ad_share(scored, stats, request_id)

        if self._over_budget(started):
            return self._finish(
                tenant_id, placement, request_id, FALLBACK, "time_budget",
                eligible, candidates, user_key, [], limit,
            )
        by_id = {c["ad_id"]: c for c in eligible}
        return self._finish(
            tenant_id, placement, request_id, PERSONALISED, None,
            [by_id[s.ad_id] for s in scored], candidates, user_key, scored, limit,
        )

    # ------------------------------------------------------------------
    # Extra signals, new ads and cross-device identity
    # ------------------------------------------------------------------

    def _extra_signals(
        self,
        tenant_id: int,
        eligible: Sequence[dict[str, Any]],
        stats: dict[int, AdStats],
        affinities: dict[str, Any],
        context: RequestContext,
        scoring_config: ScoringConfig,
        *,
        low_history: bool,
        started: float,
    ) -> dict[int, list[Signal]]:
        """Cohort pattern (low-history visitors only) and page context, per ad."""
        extra: dict[int, list[Signal]] = {}

        rows: dict[int, dict[str, int]] = {}
        if low_history and eligible:
            rows = self._cohort_rows(tenant_id, cohort_key(affinities, context.device_type), started)
        baselines = placement_baselines(eligible, stats) if rows else {}

        for candidate in eligible:
            found: list[Signal] = []
            if rows:
                signal = cohort_signal(
                    candidate,
                    stats=stats.get(candidate["ad_id"]),
                    cohort_row=rows.get(candidate["ad_id"]),
                    baselines=baselines,
                    config=scoring_config,
                )
                if signal:
                    found.append(signal)
            signal = page_context_signal(candidate, context.keywords)
            if signal:
                found.append(signal)
            if found:
                extra[candidate["ad_id"]] = found
        return extra

    def _cohort_rows(self, tenant_id: int, key: str, started: float) -> dict[int, dict[str, int]]:
        """A cohort's published per-ad response, cached briefly. Empty on any trouble."""
        cache_name = ("cohort", key)
        try:
            if self._hot_state is not None:
                cached = self._hot_state.get_json(tenant_id, *cache_name)
                if cached is not None:
                    return {int(ad_id): row for ad_id, row in cached.items()}
        except HotStateUnavailable:
            pass
        try:
            rows = self._stats.get_cohort_patterns(
                tenant_id, key, timeout_ms=self._remaining_ms(started)
            )
        except Exception:  # noqa: BLE001 - the signal is optional
            logger.warning("cohort patterns unavailable; ranking without them")
            return {}
        try:
            if self._hot_state is not None:
                self._hot_state.set_json(
                    tenant_id, cache_name, {str(a): r for a, r in rows.items()},
                    self._config.candidate_cache_ttl_seconds,
                )
        except HotStateUnavailable:
            pass
        return rows

    def _reserve_new_ad_share(
        self, scored: list[ScoredAd], stats: dict[int, AdStats], request_id: str
    ) -> list[ScoredAd]:
        """Give new ads a guaranteed minimum share of first position.

        Thompson sampling already explores ads with little data, but only as a
        chance. This reserves ``new_ad_min_share`` of requests (chosen by a hash of
        the request id, so it is reproducible) for one eligible new ad, and labels
        the effect as the ``exploration`` signal. It applies only to the
        personalised group: hold-out visitors must keep default order.
        """
        config = self._config
        if config.new_ad_min_share <= 0 or not scored:
            return scored
        draw = int.from_bytes(hashlib.sha256(f"newad:{request_id}".encode()).digest()[:4], "big") / 2**32
        if draw >= config.new_ad_min_share:
            return scored

        new_ids = {
            s.ad_id for s in scored
            if stats.get(s.ad_id, AdStats()).impressions < config.new_ad_impression_threshold
        }
        if not new_ids or scored[0].ad_id in new_ids:
            return scored

        pick = min(
            (s for s in scored if s.ad_id in new_ids),
            key=lambda s: hashlib.sha256(f"{request_id}:{s.ad_id}".encode()).digest(),
        )
        ratio = scored[0].score / pick.score if pick.score > 0 else 1.0
        signals, boosted = [], False
        for signal in pick.signals:
            if signal.name == SIGNAL_EXPLORATION:
                signal = Signal(
                    SIGNAL_EXPLORATION, signal.contribution * ratio,
                    "reserved share for new ads (fewer than "
                    f"{config.new_ad_impression_threshold} impressions)",
                )
                boosted = True
            signals.append(signal)
        if not boosted:
            signals.append(Signal(SIGNAL_EXPLORATION, ratio, "reserved share for new ads"))
        promoted = ScoredAd(pick.ad_id, scored[0].score, tuple(signals))
        return [promoted] + [s for s in scored if s.ad_id != pick.ad_id]

    def _linked_keys(self, tenant_id: int, user_key: bytes) -> list[bytes]:
        """All keys of the same known visitor (their other devices); just their own otherwise.

        The profile view is the union of these keys' profiles, computed at read time
        (FR-018), so unlinking a key removes its contribution without rewriting rows.
        """
        name = ("linked", bytes(user_key))
        try:
            if self._hot_state is not None:
                cached = self._hot_state.get_json(tenant_id, *name)
                if cached is not None:
                    return [bytes.fromhex(k) for k in cached]
        except HotStateUnavailable:
            pass
        try:
            keys = list(self._profiles.linked_keys(tenant_id, user_key))
        except Exception:  # noqa: BLE001 - identity linking is optional
            return [user_key]
        if bytes(user_key) not in {bytes(k) for k in keys}:
            keys.append(user_key)
        try:
            if self._hot_state is not None:
                self._hot_state.set_json(
                    tenant_id, name, [bytes(k).hex() for k in keys],
                    self._config.settings_cache_ttl_seconds,
                )
        except HotStateUnavailable:
            pass
        return keys

    def _recent_signals(self, tenant_id: int, keys: Sequence[bytes]) -> list[dict[str, Any]]:
        """The visitor's fresh signals (all linked keys) from hot state.

        Raises HotStateUnavailable when hot state is configured but cannot be read, so
        the caller can fall back to default ranking. No hot state at all (tests, or a
        deployment without Redis wired in) simply means no fresh signals.
        """
        if self._hot_state is None:
            return []
        found: list[dict[str, Any]] = []
        for key in keys:
            found.extend(self._hot_state.get_signals(tenant_id, key))
        return found

    # ------------------------------------------------------------------

    def _remaining_ms(self, started: float) -> int:
        budget = float(self._config.personalization_time_budget_ms)
        return max(int(budget - (self._monotonic() - started) * 1000), 1)

    def _over_budget(self, started: float) -> bool:
        return (self._monotonic() - started) * 1000 > self._config.personalization_time_budget_ms

    def _finish(
        self,
        tenant_id: int,
        placement: Optional[dict[str, Any]],
        request_id: str,
        group: str,
        reason: Optional[str],
        ordered: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        user_key: Optional[bytes],
        scored: list[ScoredAd],
        limit: int,
    ) -> RankingResult:
        ads = ordered[: max(limit, 0)]
        chosen = scored[: len(ads)] if scored else _unscored(ads)
        self._decisions.log(
            decision_log.build_record(
                request_id=request_id,
                decided_at=self._now(),
                tenant_id=tenant_id,
                group_code=group,
                candidate_count=len(candidates),
                placement_id=(placement or {}).get("placement_id"),
                user_key=user_key,
                chosen=chosen,
            )
        )
        return RankingResult(request_id, group, reason, ads, placement, tenant_id, list(chosen))


def _tenant_id_of(
    placement: Optional[dict[str, Any]], candidates: Sequence[dict[str, Any]]
) -> Optional[int]:
    if placement and placement.get("tenant_id") is not None:
        return int(placement["tenant_id"])
    if candidates and candidates[0].get("tenant_id") is not None:
        return int(candidates[0]["tenant_id"])
    return None


def _unscored(ads: Sequence[dict[str, Any]]) -> list[ScoredAd]:
    """Default-order ads as ScoredAd rows (score = score_weight, no signals) for the log."""
    return [ScoredAd(ad_id=a["ad_id"], score=float(a.get("score_weight") or 1.0)) for a in ads]
