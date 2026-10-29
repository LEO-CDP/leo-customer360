"""Tests for the reset demo's generated identity population."""

import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "dev-backend-seeding"))
import init_sample_data as seed  # noqa: E402
import seed_full_demo_data as full_seed  # noqa: E402


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


def test_raw_profiles_use_registered_source_id_not_a_synthetic_uuid():
    cursor = MagicMock()
    profile = {"source_system": "Adjust", "domain": "banking"}
    seed.seed_raw_profiles(cursor, [profile], {"adjust-mobile-attribution": "registered-id"})
    assert profile["data_source_id"] == "registered-id"
    values = cursor.execute.call_args.args[1]
    assert values[1] == "registered-id"
    assert values[2].adapted == {"registered-id": {"total_tracked_events": 1}}


def test_raw_and_behavioral_source_mappings_agree():
    assert {
        source.lower(): slug for source, slug in seed.SOURCE_SLUGS.items()
    } == {
        source: slug
        for source, slug in full_seed.BEHAVIORAL_SOURCE_SLUGS.items()
        if source != "googleanalytics"
    }


def test_raw_profiles_reject_missing_or_mismatched_registered_source():
    cursor = MagicMock()
    with pytest.raises(ValueError, match="No registered demo data source"):
        seed.seed_raw_profiles(cursor, [{"source_system": "Adjust"}], {})
    with pytest.raises(ValueError, match="does not match"):
        seed.seed_raw_profiles(
            cursor, [{"source_system": "Adjust", "data_source_id": "wrong"}],
            {"adjust-mobile-attribution": "registered-id"},
        )
    cursor.execute.assert_not_called()
