"""
The serving-decision record and its explanation formatter. No database needed.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from personalization.buffered_writer import BufferOverflow
from personalization.decision_log import (
    SIGNAL_NAMES,
    DecisionLog,
    build_record,
    explain_signals,
    top_signals,
)
from personalization.scoring import AdStats, ScoredAd, ScoringConfig, Signal, rank_ads

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def test_signal_names_equal_the_admin_contract():
    contract = (
        Path(__file__).resolve().parents[3]
        / "specs" / "003-ad-personalization-engine" / "contracts" / "admin-plane.openapi.yaml"
    )
    if not contract.exists():
        pytest.skip("contract not available in this checkout")
    match = re.search(r"name: \{ type: string, enum: \[([a-z_, ]+)\] \}", contract.read_text())
    assert match, "could not find Signal.name in the contract"
    assert set(n.strip() for n in match.group(1).split(",")) == set(SIGNAL_NAMES)


def test_every_signal_scoring_emits_uses_a_contract_name():
    candidates = [
        {"ad_id": 1, "advertiser_id": 5, "ad_type": "display", "format_code": "300x250",
         "campaign_objective": "traffic", "score_weight": 1.0},
    ]
    ranked = rank_ads(
        candidates,
        stats_by_ad={1: AdStats(impressions=1000, clicks=26)},
        affinities={"advertiser": {"5": {"score": 1.0, "last_at": NOW.isoformat()}}},
        now=NOW,
        config=ScoringConfig(),
        request_id="r",
    )
    assert {s.name for s in ranked[0].signals} <= set(SIGNAL_NAMES)


def test_notes_are_readable_text_with_the_rates_compared():
    ranked = rank_ads(
        [{"ad_id": 1, "campaign_objective": "traffic", "score_weight": 1.0}],
        stats_by_ad={1: AdStats(impressions=1000, clicks=26)},
        affinities=None,
        now=NOW,
        config=ScoringConfig(),
        request_id="r",
    )
    notes = {s.name: s.note for s in ranked[0].signals}
    assert re.search(r"click rate \d+\.\d+% vs placement baseline \d+\.\d+%", notes["objective_rate_lift"])
    assert notes["base_weight"]


def test_explain_keeps_contract_names_and_fills_a_missing_note():
    explained = explain_signals(
        [
            {"name": "objective_rate_lift", "contribution": 1.3, "note": "click rate 2.6% vs placement baseline 2.0%"},
            {"name": "exploration", "contribution": "0.9"},
            {"name": "made_up_signal", "contribution": 2.0, "note": "x"},
            {"name": "base_weight"},
            {"name": "visitor_affinity", "contribution": "not a number"},
        ]
    )

    assert [s["name"] for s in explained] == ["objective_rate_lift", "exploration"]
    assert explained[0]["note"] == "click rate 2.6% vs placement baseline 2.0%"
    assert explained[1] == {"name": "exploration", "contribution": 0.9, "note": "exploration x0.90"}


def test_explain_handles_nothing():
    assert explain_signals(None) == []
    assert explain_signals([]) == []


def test_top_signals_are_the_strongest_effects_both_ways():
    scored = ScoredAd(
        1, 1.0,
        (Signal("base_weight", 1.0), Signal("exploration", 0.5), Signal("visitor_affinity", 1.8), Signal("objective_rate_lift", 1.1)),
    )
    # Distance from 1.0 in log terms: halving (0.69) outweighs x1.8 (0.59), which outweighs x1.1.
    assert [s["name"] for s in top_signals(scored)] == ["exploration", "visitor_affinity", "objective_rate_lift"]


def test_the_record_stores_only_the_chosen_ads():
    chosen = [ScoredAd(7, 2.0, (Signal("base_weight", 2.0),)), ScoredAd(3, 1.0)]

    record = build_record(
        request_id="r", decided_at=NOW, tenant_id=1, group_code="personalised",
        candidate_count=40, placement_id=9, user_key=b"\x01" * 16, chosen=chosen,
    )

    assert [c["ad_id"] for c in record["chosen"]] == [7, 3]
    assert [c["rank"] for c in record["chosen"]] == [1, 2]
    assert record["candidate_count"] == 40  # a count, never the candidate list
    assert set(record) == {
        "request_id", "decided_at", "tenant_id", "user_key_hash", "placement_id",
        "group_code", "chosen", "candidate_count",
    }


def test_a_full_buffer_drops_the_record_and_counts_it():
    class Full:
        def put(self, _):
            raise BufferOverflow("full")

    log = DecisionLog(Full())
    assert log.log({"x": 1}) is False
    assert log.dropped == 1
