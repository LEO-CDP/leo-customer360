"""Tests for full-demo event-lake partitioning."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from seed_full_demo_data import (  # noqa: E402
    _behavioral_event_hour,
    _behavioral_object_key,
)


SOURCE_ID = "15dc39d4-ae42-5c60-9c77-66f05dcae448"


def test_behavioral_event_hour_uses_utc_partition():
    event_time = datetime(2026, 9, 18, 7, 45, tzinfo=timezone.utc)

    assert _behavioral_event_hour(event_time) == "2026-09-18-07"


def test_behavioral_object_key_matches_hourly_event_lake_contract():
    key = _behavioral_object_key(SOURCE_ID, "2026-09-18-07")

    assert key == (
        "events/2026-09-18-07/"
        "demo-behavioral-15dc39d4-ae42-5c60-9c77-66f05dcae448.jsonl.gz"
    )