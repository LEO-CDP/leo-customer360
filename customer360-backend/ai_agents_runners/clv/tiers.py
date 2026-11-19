"""Percentile ranking, frozen cut-offs and hysteresis tiering for CLV labels."""

from __future__ import annotations

import bisect
from collections.abc import Sequence

from .settings import TierBand

QUANTILE_POINTS = 101


def percentile_ranks(values: Sequence[float]) -> list[float]:
    """Return 0-100 percentile ranks; ties share their average rank."""
    count = len(values)
    if count == 0:
        return []
    if count == 1:
        return [100.0]
    order = sorted(range(count), key=lambda index: values[index])
    ranks = [0.0] * count
    position = 0
    while position < count:
        end = position
        while end + 1 < count and values[order[end + 1]] == values[order[position]]:
            end += 1
        average_rank = (position + end) / 2
        for index in order[position : end + 1]:
            ranks[index] = round(100.0 * average_rank / (count - 1), 2)
        position = end + 1
    return ranks


def population_quantiles(values: Sequence[float]) -> list[float]:
    """Return the value at each whole percentile 0..100 (linear interpolation)."""
    if not values:
        return []
    ordered = sorted(values)
    last = len(ordered) - 1
    quantiles = []
    for point in range(QUANTILE_POINTS):
        position = last * point / (QUANTILE_POINTS - 1)
        lower = int(position)
        upper = min(lower + 1, last)
        fraction = position - lower
        quantiles.append(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)
    return quantiles


def percentile_from_quantiles(value: float, quantiles: Sequence[float]) -> float:
    """Place a single re-scored value on the frozen population distribution."""
    if not quantiles:
        raise ValueError("quantiles must not be empty")
    if value <= quantiles[0]:
        return 0.0
    if value >= quantiles[-1]:
        return 100.0
    upper = bisect.bisect_right(quantiles, value)
    lower = upper - 1
    span = quantiles[upper] - quantiles[lower]
    fraction = 0.0 if span == 0 else (value - quantiles[lower]) / span
    step = 100.0 / (len(quantiles) - 1)
    return round(min(100.0, (lower + fraction) * step), 2)


def raw_tier(percentile: float, bands: Sequence[TierBand]) -> str:
    for band in bands:
        if percentile >= band.min_percentile:
            return band.tier
    return bands[-1].tier


def assign_tier(
    percentile: float,
    previous_tier: str | None,
    bands: Sequence[TierBand],
    hysteresis_points: float,
) -> str:
    """Tier with hysteresis: cross a cut-off by the margin before moving.

    ``bands`` is ordered from highest to lowest tier. Promotion into a tier
    needs ``min_percentile + hysteresis``; staying in a tier needs
    ``min_percentile - hysteresis``. The lowest band always qualifies.
    """
    order = [band.tier for band in bands]
    if previous_tier not in order:
        return raw_tier(percentile, bands)
    previous_index = order.index(previous_tier)
    candidate = raw_tier(percentile, bands)
    candidate_index = order.index(candidate)
    if candidate_index == previous_index:
        return previous_tier
    if candidate_index < previous_index:
        for band in bands[candidate_index:previous_index]:
            if percentile >= band.min_percentile + hysteresis_points:
                return band.tier
        return previous_tier
    for index in range(previous_index, len(bands)):
        band = bands[index]
        if index == len(bands) - 1 or percentile >= band.min_percentile - hysteresis_points:
            return band.tier
    return bands[-1].tier


def tier_change_rate(before: Sequence[str | None], after: Sequence[str | None]) -> float:
    if len(before) != len(after):
        raise ValueError("tier sequences must have the same length")
    if not before:
        return 0.0
    changed = sum(1 for old, new in zip(before, after) if old != new)
    return changed / len(before)
