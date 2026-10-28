"""Eligibility: which candidate ads may be served at all.

Personalisation only ever RE-ORDERS ads that are eligible here; it can never make
an ineligible ad eligible. The rules, in the order they are checked:

1. Creative: status ``active`` and inside its start/end window.
2. Campaign (when the ad has one): status ``active`` and inside its start/end window.
3. Placement serving index (``placement_ad``), when a row exists for the ad: active
   and inside its ``valid_from``/``valid_to`` window.
4. Targeting rules, excluding only on a KNOWN mismatch (see below).
5. Frequency cap: ``ad.frequency_cap`` impressions per visitor per UTC day. A cap
   of 0 or none means no cap.

(The ad's own ``status`` is checked by the candidate query.)

Targeting
    An ad with rules is excluded only when EVERY rule excludes it, and a rule
    excludes the request only on a known mismatch: the rule restricts countries,
    regions, device types, operating systems, browsers or languages and the request
    supplies that attribute with a value outside the list; or the request value is
    inside the rule's ``exclude_predicates``. An attribute the request does not
    supply never excludes. This keeps delivery working where the caller (or CDN)
    sends little context, and enforces the rules where it does.

    Not evaluated by the ad server in this version, and so never used to exclude:
    audience membership (``audience_key``, ``ad_audience``), ``custom_predicates``,
    age and gender, and ``context_keywords`` (which only influence ranking).

The kill switch ``eligibility_enforced`` (see core/config.py) switches the whole
stage off to restore the previous behaviour (status and score_weight only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class RequestContext:
    """What is known about the request, for targeting. ``None`` means "not supplied"."""

    country: Optional[str] = None
    region: Optional[str] = None
    device_type: Optional[str] = None  # mobile, tablet or desktop
    os: Optional[str] = None
    browser: Optional[str] = None
    language: Optional[str] = None  # primary subtag, lower case ("vi", "en")
    keywords: tuple[str, ...] = ()


# Headers CDNs and proxies commonly use to carry the viewer's country.
COUNTRY_HEADERS = (
    "x-country-code",
    "cloudfront-viewer-country",
    "cf-ipcountry",
    "x-appengine-country",
)

_MOBILE = re.compile(r"mobi|iphone|ipod|android.*mobile|windows phone", re.I)
_TABLET = re.compile(r"ipad|tablet|android(?!.*mobile)|kindle|silk", re.I)


def parse_user_agent(user_agent: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort ``(device_type, os, browser)`` from a User-Agent string.

    Deliberately simple: it only needs to be right often enough to EXCLUDE on a
    known mismatch, so anything unrecognised is returned as None (no exclusion).
    """
    if not user_agent:
        return None, None, None
    ua = user_agent

    if _TABLET.search(ua):
        device = "tablet"
    elif _MOBILE.search(ua):
        device = "mobile"
    else:
        device = "desktop"

    if re.search(r"iphone|ipad|ipod|ios", ua, re.I):
        os_name = "ios"
    elif re.search(r"android", ua, re.I):
        os_name = "android"
    elif re.search(r"windows", ua, re.I):
        os_name = "windows"
    elif re.search(r"mac os x|macintosh", ua, re.I):
        os_name = "macos"
    elif re.search(r"linux|x11", ua, re.I):
        os_name = "linux"
    else:
        os_name = None

    if re.search(r"edg(e|a|ios)?/", ua, re.I):
        browser = "edge"
    elif re.search(r"opr/|opera", ua, re.I):
        browser = "opera"
    elif re.search(r"firefox|fxios", ua, re.I):
        browser = "firefox"
    elif re.search(r"chrome|crios", ua, re.I):
        browser = "chrome"
    elif re.search(r"safari", ua, re.I):
        browser = "safari"
    else:
        browser = None

    return device, os_name, browser


def build_context(
    headers: Mapping[str, str],
    language: Optional[str] = None,
    keywords: Optional[str] = None,
) -> RequestContext:
    """Assemble a RequestContext from request headers and the ``lang`` / ``ctx`` params."""
    lowered = {k.lower(): v for k, v in headers.items()}

    country = None
    for name in COUNTRY_HEADERS:
        value = (lowered.get(name) or "").strip()
        if len(value) == 2 and value.isalpha():
            country = value.upper()
            break

    lang = (language or "").strip()
    if not lang:
        accept = (lowered.get("accept-language") or "").split(",")[0].split(";")[0].strip()
        lang = accept
    primary_language = lang.split("-")[0].split("_")[0].lower() or None

    device, os_name, browser = parse_user_agent(lowered.get("user-agent"))

    words = tuple(
        w for w in (part.strip().lower() for part in (keywords or "").split(",")) if w
    )[:20]

    return RequestContext(
        country=country,
        device_type=device,
        os=os_name,
        browser=browser,
        language=primary_language,
        keywords=words,
    )


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

_RULE_CRITERIA = (
    # (rule column, RequestContext attribute)
    ("countries", "country"),
    ("regions", "region"),
    ("device_types", "device_type"),
    ("os_types", "os"),
    ("browser_types", "browser"),
    ("languages", "language"),
)


def _as_lower_set(values: Any) -> set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    return {str(v).strip().lower() for v in values if str(v).strip()}


def _rule_excludes(rule: Mapping[str, Any], context: RequestContext) -> bool:
    """True when this rule rules the request out, on a known mismatch only."""
    exclude = rule.get("exclude_predicates") or {}

    for column, attribute in _RULE_CRITERIA:
        actual = getattr(context, attribute)
        if actual is None:
            continue  # unknown never excludes
        actual = str(actual).lower()

        allowed = _as_lower_set(rule.get(column))
        if allowed and actual not in allowed:
            return True

        denied = _as_lower_set(exclude.get(column))
        if denied and actual in denied:
            return True

    return False


def excluded_by_targeting(
    rules: Sequence[Mapping[str, Any]], context: RequestContext
) -> bool:
    """An ad with rules is excluded only when every rule excludes the request."""
    if not rules:
        return False
    return all(_rule_excludes(rule, context) for rule in rules)


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------


SHOWN_COUNTER_TTL_SECONDS = 2 * 86_400


def shown_counter_name(ad_id: int, now: datetime) -> str:
    """Name of the per-visitor counter of today's (UTC) impressions of an ad.

    Event ingest increments it on every impression and the ranking service reads
    it for frequency caps, so both use this one definition.
    """
    return f"shown:{int(ad_id)}:{now.astimezone(timezone.utc).strftime('%Y%m%d')}"


def _window_closed(starts_at: Optional[datetime], ends_at: Optional[datetime], now: datetime) -> bool:
    if starts_at is not None and now < starts_at:
        return True
    if ends_at is not None and now > ends_at:
        return True
    return False


def rejection_reason(
    candidate: Mapping[str, Any],
    context: RequestContext,
    now: datetime,
    shown_today: int = 0,
) -> Optional[str]:
    """Why a candidate may not be served, or None when it is eligible.

    ``shown_today`` is how many times this visitor has already seen the ad today
    (0 when the visitor is unknown or not eligible for tracking).
    """
    if candidate.get("creative_status") not in (None, "active"):
        return "creative_not_active"
    if _window_closed(candidate.get("creative_starts_at"), candidate.get("creative_ends_at"), now):
        return "creative_outside_window"

    if candidate.get("campaign_id") is not None:
        if candidate.get("campaign_status") not in (None, "active"):
            return "campaign_not_active"
        if _window_closed(
            candidate.get("campaign_starts_at"), candidate.get("campaign_ends_at"), now
        ):
            return "campaign_outside_window"

    if candidate.get("serving_index_active") is False:
        return "serving_index_inactive"
    if _window_closed(
        candidate.get("serving_index_valid_from"), candidate.get("serving_index_valid_to"), now
    ):
        return "serving_index_outside_window"

    if excluded_by_targeting(candidate.get("rules") or [], context):
        return "excluded_by_targeting"

    cap = candidate.get("frequency_cap")
    if cap is not None and cap > 0 and shown_today >= cap:
        return "frequency_cap_reached"

    return None


def filter_eligible(
    candidates: Iterable[Mapping[str, Any]],
    context: RequestContext,
    now: datetime,
    shown_today: Optional[Mapping[int, int]] = None,
) -> tuple[list[Mapping[str, Any]], dict[int, str]]:
    """Split candidates into the eligible ones (order kept) and ``{ad_id: reason}``."""
    shown_today = shown_today or {}
    eligible: list[Mapping[str, Any]] = []
    rejected: dict[int, str] = {}
    for candidate in candidates:
        reason = rejection_reason(
            candidate, context, now, shown_today.get(candidate["ad_id"], 0)
        )
        if reason is None:
            eligible.append(candidate)
        else:
            rejected[candidate["ad_id"]] = reason
    return eligible, rejected
