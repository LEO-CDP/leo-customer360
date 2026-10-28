"""Cohort keys: anonymous "visitors like this one" groups for low-history visitors.

A visitor with little history of their own can still be shown ads that similar
visitors responded to. "Similar" is deliberately coarse so cohorts are large:

    cohort key = the visitor's two strongest positive interests + their device class

    interests: the top two ``tag:<value>`` or ``advertiser:<value>`` affinities with a
               positive score, strongest first (ties broken alphabetically), then
               written in alphabetical order so the key does not depend on rank
    device:    mobile, tablet or desktop; ``any`` when unknown

for example ``advertiser:5+tag:travel@mobile``, or ``none@desktop`` for a visitor
with no positive interests yet.

No per-visitor membership is ever stored. The backend ``cohort_patterns`` job
computes the same key for each eligible visitor from their stored profile, groups
visitors by it, and publishes per-ad response only for cohorts of at least
``cohort_min_size`` visitors. The two implementations are tied together by
``contracts/cohort-key-test-vectors.json``, which both services' tests load.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

DIMENSIONS = ("tag", "advertiser")
INTEREST_COUNT = 2
SCORE_DECIMALS = 3
UNKNOWN_DEVICE = "any"
NO_INTERESTS = "none"
KNOWN_DEVICES = ("mobile", "tablet", "desktop")


def top_interests(affinities: Optional[Mapping[str, Any]]) -> list[str]:
    """The strongest positive ``dimension:value`` buckets, alphabetical, at most two."""
    scored: list[tuple[float, str]] = []
    for dimension in DIMENSIONS:
        for value, entry in ((affinities or {}).get(dimension) or {}).items():
            try:
                score = round(float(entry.get("score", 0.0)), SCORE_DECIMALS)
            except (AttributeError, TypeError, ValueError):
                continue
            if score > 0:
                scored.append((score, f"{dimension}:{value}"))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return sorted(name for _, name in scored[:INTEREST_COUNT])


def device_class(device_type: Optional[str]) -> str:
    device = (device_type or "").strip().lower()
    return device if device in KNOWN_DEVICES else UNKNOWN_DEVICE


def cohort_key(affinities: Optional[Mapping[str, Any]], device_type: Optional[str]) -> str:
    """The deterministic cohort of a visitor with these affinities on this kind of device."""
    interests = "+".join(top_interests(affinities)) or NO_INTERESTS
    return f"{interests}@{device_class(device_type)}"
