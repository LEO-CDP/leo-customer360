"""Tests for User-Agent device-type classification."""

from core.device import parse_device_type


DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_4) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.104 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
TABLET_UA = (
    "Mozilla/5.0 (iPad; CPU OS 13_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0 Mobile/15E148 Safari/604.1"
)


def test_parse_device_type_classifies_common_user_agents():
    assert parse_device_type(DESKTOP_UA) == "desktop"
    assert parse_device_type(MOBILE_UA) == "mobile"
    assert parse_device_type(TABLET_UA) == "tablet"


def test_parse_device_type_defaults_unknown_for_missing_or_invalid_user_agent():
    assert parse_device_type(None) == "unknown"
    assert parse_device_type("") == "unknown"
    assert parse_device_type("not a user agent") == "unknown"