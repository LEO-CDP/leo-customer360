"""Fresh visitor signals: how a click or dismissal changes the very next request.

The durable profile is rebuilt hourly from stored events, so on its own it is up to
an hour behind. Event ingest therefore also records each eligible visitor signal in
hot state (``HotState.apply_event``), and ranking merges those signals over the
profile. Everything here is a pure function of the profile and the set of signals,
so the result does not depend on the order or the number of times events arrived.

Rules:

* Weights are the profile's (``contracts/profile-event-weights.json``): a click
  raises the features of the ad, a dismissal lowers them, so ads that share an
  advertiser, type, format or tag with a dismissed ad rank lower.
* An ad is EXCLUDED while its latest clear signal is a dismissal (within the
  window). A later click or conversion on that same ad lifts the exclusion; a
  later dismissal restores it. "The most recent clear signal takes precedence."
* The profile is rebuilt from stored events up to its ``updated_at``. A signal
  older than that (less a safety margin for events still in the write buffer when
  the rebuild ran) is already inside the profile and is not counted again; a newer
  one is not, so a rebuild never discards fresh behaviour. The margin means a
  signal within ``FOLD_MARGIN_SECONDS`` of the rebuild can count twice for as long
  as its decayed weight lasts: a small, bounded overstatement chosen over losing it.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

# Mirrors contracts/profile-event-weights.json (a test keeps them identical).
EVENT_WEIGHTS = {
    "impression": 0.0,
    "view": 0.2,
    "click": 1.0,
    "dismiss": -1.0,
    "conversion": 2.0,
    "view_through_conversion": 1.0,
}
POSITIVE_TYPES = ("click", "conversion", "view_through_conversion")

SIGNAL_WINDOW_SECONDS = 24 * 3600
FOLD_MARGIN_SECONDS = 120


def _epoch(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def in_window(signals: Iterable[Mapping[str, Any]], now: float) -> list[Mapping[str, Any]]:
    """Signals no older than the window (a signal slightly in the future is kept)."""
    return [s for s in signals if now - float(s.get("at", 0)) <= SIGNAL_WINDOW_SECONDS]


def not_yet_in_profile(
    signals: Iterable[Mapping[str, Any]], profile_updated_at: Any
) -> list[Mapping[str, Any]]:
    """Signals the durable profile cannot contain yet."""
    built = _epoch(profile_updated_at)
    if built is None:
        return list(signals)
    cutoff = built - FOLD_MARGIN_SECONDS
    return [s for s in signals if float(s.get("at", 0)) > cutoff]


def merge_affinities(
    affinities: Optional[Mapping[str, Any]],
    signals: Sequence[Mapping[str, Any]],
    now: float,
    half_life_days: float,
) -> dict[str, dict[str, dict[str, Any]]]:
    """The profile's affinities plus the decayed contribution of ``signals``."""
    merged: dict[str, dict[str, dict[str, Any]]] = copy.deepcopy(dict(affinities or {}))
    now_iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

    # Sorted so the floating-point sum is the same whatever order signals arrived in.
    for signal in sorted(signals, key=lambda s: (s["at"], s["ad"], s["type"])):
        weight = EVENT_WEIGHTS.get(signal.get("type"), 0.0)
        if weight == 0.0:
            continue
        age_days = max(now - float(signal["at"]), 0.0) / 86_400.0
        contribution = weight * (0.5 ** (age_days / half_life_days) if half_life_days > 0 else 1.0)

        for dimension, value in signal.get("f", []):
            bucket = merged.setdefault(dimension, {})
            entry = bucket.get(value)
            if entry is None:
                bucket[value] = {"score": contribution, "n": 1, "last_at": now_iso}
                continue
            last = _epoch(entry.get("last_at"))
            carried = float(entry.get("score", 0.0))
            if last is not None and half_life_days > 0:
                carried *= 0.5 ** (max(now - last, 0.0) / 86_400.0 / half_life_days)
            bucket[value] = {
                "score": carried + contribution,
                "n": int(entry.get("n", 0)) + 1,
                "last_at": now_iso,
            }
    return merged


def dismissed_ad_ids(
    signals: Sequence[Mapping[str, Any]],
    durable_dismissed: Optional[Mapping[str, Any]],
    now: float,
) -> set[int]:
    """Ads to withhold: their latest clear signal is a dismissal, still within the window."""
    last_dismiss: dict[int, float] = {}
    last_positive: dict[int, float] = {}

    for ad_key, at in (durable_dismissed or {}).items():
        moment = _epoch(at)
        if moment is not None:
            last_dismiss[int(ad_key)] = max(moment, last_dismiss.get(int(ad_key), 0.0))

    for signal in signals:
        ad_id, at = int(signal["ad"]), float(signal["at"])
        if signal.get("type") == "dismiss":
            last_dismiss[ad_id] = max(at, last_dismiss.get(ad_id, 0.0))
        elif signal.get("type") in POSITIVE_TYPES:
            last_positive[ad_id] = max(at, last_positive.get(ad_id, 0.0))

    return {
        ad_id
        for ad_id, dismissed_at in last_dismiss.items()
        if now - dismissed_at <= SIGNAL_WINDOW_SECONDS
        and last_positive.get(ad_id, 0.0) <= dismissed_at
    }
