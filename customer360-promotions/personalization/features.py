"""Ad feature dimensions: what a visitor's behaviour is learned over.

Ads and creatives have no category field, so a visitor's affinity is learned over
the dimensions the catalogue does have:

* ``advertiser``  the advertiser behind the creative
* ``ad_type``     display, native, video, carousel, ...
* ``format``      the creative format code (300x250, native, product_carousel, ...)
* ``tag``         optional free-form tags in ``ad.metadata["tags"]`` or the creative's
                  ``content_payload["tags"]``

The batch job that builds visitor profiles (customer360-backend/ad_personalization)
derives the same dimensions in SQL; both sides are pinned to the names in
``specs/003-ad-personalization-engine/contracts/profile-event-weights.json``.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

DIM_ADVERTISER = "advertiser"
DIM_AD_TYPE = "ad_type"
DIM_FORMAT = "format"
DIM_TAG = "tag"

DIMENSIONS = (DIM_ADVERTISER, DIM_AD_TYPE, DIM_FORMAT, DIM_TAG)

MAX_TAGS = 10
MAX_TAG_LENGTH = 64


def _normalise_tags(raw: Any) -> list[str]:
    """Clean a tag list: strings only, lower-case, trimmed, de-duplicated, bounded."""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower()[:MAX_TAG_LENGTH]
        if tag and tag not in seen:
            seen.append(tag)
        if len(seen) >= MAX_TAGS:
            break
    return seen


def ad_features(ad: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The ``(dimension, value)`` pairs describing one candidate ad.

    ``ad`` is a candidate row from ``AdRepository.get_candidate_ads``. Missing
    values are skipped, never invented.
    """
    features: list[tuple[str, str]] = []

    advertiser_id = ad.get("advertiser_id")
    if advertiser_id is not None:
        features.append((DIM_ADVERTISER, str(advertiser_id)))

    ad_type = ad.get("ad_type")
    if ad_type:
        features.append((DIM_AD_TYPE, str(ad_type)))

    format_code = ad.get("format_code")
    if format_code:
        features.append((DIM_FORMAT, str(format_code)))

    tags = _normalise_tags((ad.get("ad_metadata") or {}).get("tags"))
    if not tags:
        tags = _normalise_tags((ad.get("content_payload") or {}).get("tags"))
    features.extend((DIM_TAG, tag) for tag in tags)

    return features


def shared_features(
    left: Iterable[tuple[str, str]], right: Iterable[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Features two ads have in common (used to demote ads similar to a dismissed one)."""
    return set(left) & set(right)
