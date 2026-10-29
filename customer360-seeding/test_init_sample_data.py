"""Tests for the reset demo's generated identity population."""

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "dev-backend-seeding"))
import init_sample_data as seed  # noqa: E402


def test_default_demo_generates_more_than_5000_unique_customers_in_six_domains():
    profiles = seed.generate_raw_profiles()
    installs = [profile for profile in profiles if profile["event_name"] == "install"]
    devices = {profile["device_id"] for profile in installs}
    domain_counts = Counter(profile["domain"] for profile in installs)

    assert len(profiles) == 8_000
    assert len(installs) == len(devices) == 5_600
    assert set(domain_counts) == {
        "retail", "education", "real_estate", "travel", "banking", "media",
    }
    assert min(domain_counts.values()) > 0
    assert {profile["device_id"] for profile in profiles} == devices
    assert {profile["source_system"] for profile in profiles} == {
        "Adjust", "OneSignal", "WebTracking",
    }


@pytest.mark.parametrize("count,rate", [(0, 0.3), (10, -0.1), (10, 1)])
def test_demo_generator_rejects_invalid_counts(count, rate):
    with pytest.raises(ValueError):
        seed.generate_raw_profiles(count=count, duplicate_rate=rate)
