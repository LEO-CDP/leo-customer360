"""Eligibility: every rule on its own and combined, and the request-context helpers."""

from datetime import datetime, timedelta, timezone

import pytest

from personalization.eligibility import (
    RequestContext,
    build_context,
    excluded_by_targeting,
    filter_eligible,
    parse_user_agent,
    rejection_reason,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def cand(**overrides):
    """A candidate that is eligible unless an override says otherwise."""
    base = {
        "ad_id": 1,
        "ad_key": "ad_1",
        "campaign_id": 10,
        "campaign_status": "active",
        "campaign_starts_at": NOW - DAY,
        "campaign_ends_at": NOW + DAY,
        "creative_status": "active",
        "creative_starts_at": None,
        "creative_ends_at": None,
        "serving_index_active": None,  # no placement_ad row
        "serving_index_valid_from": None,
        "serving_index_valid_to": None,
        "frequency_cap": None,
        "rules": [],
    }
    base.update(overrides)
    return base


CTX = RequestContext()


def reason(candidate, context=CTX, shown=0):
    return rejection_reason(candidate, context, NOW, shown)


# --------------------------------------------------------------------------
# Creative, campaign and serving-index windows
# --------------------------------------------------------------------------


def test_a_plain_candidate_is_eligible():
    assert reason(cand()) is None


@pytest.mark.parametrize("status", ["draft", "paused", "archived"])
def test_a_creative_that_is_not_active_is_rejected(status):
    assert reason(cand(creative_status=status)) == "creative_not_active"


def test_a_creative_outside_its_window_is_rejected():
    assert reason(cand(creative_starts_at=NOW + HOUR)) == "creative_outside_window"
    assert reason(cand(creative_ends_at=NOW - HOUR)) == "creative_outside_window"
    assert reason(cand(creative_starts_at=NOW - HOUR, creative_ends_at=NOW + HOUR)) is None


@pytest.mark.parametrize("status", ["draft", "paused", "completed", "archived"])
def test_a_campaign_that_is_not_active_is_rejected(status):
    assert reason(cand(campaign_status=status)) == "campaign_not_active"


def test_a_campaign_outside_its_dates_is_rejected():
    assert reason(cand(campaign_starts_at=NOW + HOUR)) == "campaign_outside_window"
    assert reason(cand(campaign_ends_at=NOW - HOUR)) == "campaign_outside_window"


def test_an_ad_without_a_campaign_ignores_campaign_fields():
    assert reason(cand(campaign_id=None, campaign_status=None, campaign_ends_at=NOW - DAY)) is None


def test_a_serving_index_row_can_switch_an_ad_off_or_bound_its_window():
    assert reason(cand(serving_index_active=False)) == "serving_index_inactive"
    assert reason(cand(serving_index_active=True)) is None
    assert reason(cand(serving_index_valid_from=NOW + HOUR)) == "serving_index_outside_window"
    assert reason(cand(serving_index_valid_to=NOW - HOUR)) == "serving_index_outside_window"
    assert reason(cand(serving_index_valid_from=NOW - HOUR, serving_index_valid_to=NOW + HOUR)) is None


# --------------------------------------------------------------------------
# Targeting
# --------------------------------------------------------------------------


def rule(**fields):
    return {"exclude_predicates": {}, **fields}


def test_no_rules_means_no_targeting_exclusion():
    assert not excluded_by_targeting([], RequestContext(country="VN"))


def test_a_known_mismatch_excludes():
    rules = [rule(countries=["VN"])]
    assert excluded_by_targeting(rules, RequestContext(country="US"))
    assert not excluded_by_targeting(rules, RequestContext(country="VN"))


def test_an_unknown_attribute_never_excludes():
    rules = [rule(countries=["VN"], device_types=["mobile"], languages=["vi"])]
    assert not excluded_by_targeting(rules, RequestContext())  # nothing supplied
    assert not excluded_by_targeting(rules, RequestContext(country="VN"))  # others unknown


@pytest.mark.parametrize(
    "criterion,attribute,value",
    [
        ("countries", "country", "US"),
        ("regions", "region", "north"),
        ("device_types", "device_type", "desktop"),
        ("os_types", "os", "windows"),
        ("browser_types", "browser", "firefox"),
        ("languages", "language", "en"),
    ],
)
def test_each_criterion_excludes_on_a_known_mismatch(criterion, attribute, value):
    rules = [rule(**{criterion: ["zz-not-this"]})]
    assert excluded_by_targeting(rules, RequestContext(**{attribute: value}))


def test_matching_is_case_insensitive():
    assert not excluded_by_targeting([rule(countries=["vn"])], RequestContext(country="VN"))
    assert not excluded_by_targeting([rule(device_types=["Mobile"])], RequestContext(device_type="mobile"))


def test_exclude_predicates_exclude_on_a_known_value():
    rules = [rule(exclude_predicates={"countries": ["US", "CA"]})]
    assert excluded_by_targeting(rules, RequestContext(country="US"))
    assert not excluded_by_targeting(rules, RequestContext(country="VN"))
    assert not excluded_by_targeting(rules, RequestContext())


def test_an_ad_is_excluded_only_when_every_rule_excludes_it():
    rules = [rule(countries=["VN"]), rule(countries=["US"])]
    assert not excluded_by_targeting(rules, RequestContext(country="US"))  # the second rule matches
    assert not excluded_by_targeting(rules, RequestContext(country="VN"))
    assert excluded_by_targeting(rules, RequestContext(country="FR"))  # both exclude


def test_criteria_the_server_cannot_evaluate_never_exclude():
    rules = [
        rule(
            audience_key="retargeting-users",
            min_age=30,
            max_age=40,
            gender_codes=["F"],
            context_keywords=["fashion"],
            custom_predicates={"cartValue": ">100000", "retargeting": True},
        )
    ]
    assert not excluded_by_targeting(rules, RequestContext(country="VN", keywords=("sports",)))


def test_a_targeting_exclusion_surfaces_as_the_rejection_reason():
    candidate = cand(rules=[rule(countries=["VN"])])
    assert reason(candidate, RequestContext(country="US")) == "excluded_by_targeting"
    assert reason(candidate, RequestContext(country="VN")) is None


# --------------------------------------------------------------------------
# Frequency cap
# --------------------------------------------------------------------------


def test_the_frequency_cap_is_enforced_per_visitor_per_day():
    capped = cand(frequency_cap=3)
    assert reason(capped, shown=0) is None
    assert reason(capped, shown=2) is None
    assert reason(capped, shown=3) == "frequency_cap_reached"
    assert reason(capped, shown=10) == "frequency_cap_reached"


@pytest.mark.parametrize("cap", [None, 0])
def test_no_cap_or_a_cap_of_zero_means_unlimited(cap):
    assert reason(cand(frequency_cap=cap), shown=1_000) is None


# --------------------------------------------------------------------------
# Combined behaviour
# --------------------------------------------------------------------------


def test_filter_eligible_keeps_order_and_reports_each_reason():
    candidates = [
        cand(ad_id=1),
        cand(ad_id=2, campaign_status="paused"),
        cand(ad_id=3, frequency_cap=1),
        cand(ad_id=4, rules=[rule(countries=["VN"])]),
        cand(ad_id=5),
    ]

    eligible, rejected = filter_eligible(
        candidates, RequestContext(country="US"), NOW, shown_today={3: 1}
    )

    assert [c["ad_id"] for c in eligible] == [1, 5]
    assert rejected == {
        2: "campaign_not_active",
        3: "frequency_cap_reached",
        4: "excluded_by_targeting",
    }


def test_the_first_failing_rule_is_the_reported_reason():
    candidate = cand(creative_status="archived", campaign_status="paused", frequency_cap=1)
    assert reason(candidate, shown=5) == "creative_not_active"


# --------------------------------------------------------------------------
# Request context helpers
# --------------------------------------------------------------------------

IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
)
IPAD = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Safari/604.1"
WINDOWS_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
WINDOWS_EDGE = WINDOWS_CHROME + " Edg/120.0"
MAC_FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0"


@pytest.mark.parametrize(
    "ua,expected",
    [
        (IPHONE, ("mobile", "ios", "safari")),
        (ANDROID, ("mobile", "android", "chrome")),
        (IPAD, ("tablet", "ios", "safari")),
        (WINDOWS_CHROME, ("desktop", "windows", "chrome")),
        (WINDOWS_EDGE, ("desktop", "windows", "edge")),
        (MAC_FIREFOX, ("desktop", "macos", "firefox")),
        (None, (None, None, None)),
        ("", (None, None, None)),
    ],
)
def test_user_agents_are_classified(ua, expected):
    assert parse_user_agent(ua) == expected


def test_an_unrecognised_user_agent_is_left_unknown_where_it_cannot_tell():
    device, os_name, browser = parse_user_agent("curl/8.4.0")
    assert (os_name, browser) == (None, None)  # so nothing is excluded on them


def test_the_context_reads_country_from_cdn_headers():
    assert build_context({"CloudFront-Viewer-Country": "vn"}).country == "VN"
    assert build_context({"X-Country-Code": "AU"}).country == "AU"
    assert build_context({"CF-IPCountry": "XX1"}).country is None  # malformed is ignored
    assert build_context({}).country is None


def test_the_language_param_beats_accept_language_and_keeps_the_primary_subtag():
    assert build_context({"Accept-Language": "en-AU,en;q=0.9"}).language == "en"
    assert build_context({"Accept-Language": "en-AU"}, language="vi-VN").language == "vi"
    assert build_context({}).language is None


def test_page_keywords_are_normalised_and_bounded():
    context = build_context({}, keywords=" Fashion, SALE ,,menswear ")
    assert context.keywords == ("fashion", "sale", "menswear")
    many = ",".join(f"k{i}" for i in range(50))
    assert len(build_context({}, keywords=many).keywords) == 20


def test_the_context_combines_headers_and_params():
    context = build_context(
        {"User-Agent": ANDROID, "CloudFront-Viewer-Country": "VN", "Accept-Language": "vi"},
        keywords="fashion",
    )
    assert (context.device_type, context.os, context.browser) == ("mobile", "android", "chrome")
    assert (context.country, context.language, context.keywords) == ("VN", "vi", ("fashion",))
