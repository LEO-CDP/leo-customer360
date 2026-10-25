"""
Fresh signals in hot state: idempotence, order-independence and the rule that the
most recent clear signal wins. No database needed.
"""

import itertools
import json
from pathlib import Path

import pytest

from personalization import signals
from personalization.hot_state import SIGNAL_CAP, HotStateUnavailable, InMemoryHotState

TENANT = 7
KEY = b"\x01" * 16
OTHER_KEY = b"\x02" * 16
NOW = 1_800_000_000.0
HALF_LIFE = 14

# Two ads from the same advertiser and format (similar), one unrelated.
SIMILAR_FEATURES = [("advertiser", "A"), ("format", "banner"), ("tag", "outdoor")]


def event(event_id, ad_id, event_type, at):
    return {"event_id": event_id, "ad_id": ad_id, "event_type": event_type, "at": at}


def apply(hot, ev, features=SIMILAR_FEATURES, key=KEY, tenant=TENANT):
    return hot.apply_event(tenant, key, ev, features)


def affinity(merged, dimension, value):
    return merged.get(dimension, {}).get(value, {}).get("score", 0.0)


# --------------------------------------------------------------------------
# Effect of a signal
# --------------------------------------------------------------------------


def test_a_click_raises_ads_that_share_features():
    hot = InMemoryHotState()
    apply(hot, event("e1", 1, "click", NOW - 5))

    merged = signals.merge_affinities({}, hot.get_signals(TENANT, KEY), NOW, HALF_LIFE)

    assert affinity(merged, "advertiser", "A") == pytest.approx(1.0, abs=0.01)
    assert affinity(merged, "advertiser", "B") == 0.0


def test_a_dismissal_lowers_similar_ads_and_withholds_that_ad():
    hot = InMemoryHotState()
    apply(hot, event("e1", 1, "dismiss", NOW - 5))
    recent = hot.get_signals(TENANT, KEY)

    merged = signals.merge_affinities({}, recent, NOW, HALF_LIFE)

    assert affinity(merged, "advertiser", "A") < 0
    assert signals.dismissed_ad_ids(recent, None, NOW) == {1}


def test_signals_add_to_the_durable_profile_and_do_not_mutate_it():
    profile = {"advertiser": {"A": {"score": 2.0, "n": 4, "last_at": "2026-10-17T00:00:00+00:00"}}}
    snapshot = json.dumps(profile, sort_keys=True)
    sig = [{"ad": 1, "type": "click", "at": NOW - 5, "f": [["advertiser", "A"]]}]

    merged = signals.merge_affinities(profile, sig, NOW, HALF_LIFE)

    assert json.dumps(profile, sort_keys=True) == snapshot
    assert merged["advertiser"]["A"]["n"] == 5
    assert merged["advertiser"]["A"]["score"] > 1.0


def test_an_impression_carries_no_weight():
    sig = [{"ad": 1, "type": "impression", "at": NOW, "f": [["advertiser", "A"]]}]
    assert signals.merge_affinities({}, sig, NOW, HALF_LIFE) == {}


def test_older_signals_count_for_less():
    old = [{"ad": 1, "type": "click", "at": NOW - 14 * 86_400, "f": [["tag", "x"]]}]
    new = [{"ad": 1, "type": "click", "at": NOW, "f": [["tag", "x"]]}]
    assert affinity(signals.merge_affinities({}, old, NOW, HALF_LIFE), "tag", "x") == pytest.approx(0.5, abs=0.01)
    assert affinity(signals.merge_affinities({}, new, NOW, HALF_LIFE), "tag", "x") == pytest.approx(1.0, abs=0.01)


# --------------------------------------------------------------------------
# Idempotence and order
# --------------------------------------------------------------------------


def test_replaying_an_event_changes_nothing():
    hot = InMemoryHotState()
    ev = event("e1", 1, "click", NOW - 5)

    assert apply(hot, ev) is True
    assert apply(hot, ev) is False
    assert apply(hot, ev) is False

    assert len(hot.get_signals(TENANT, KEY)) == 1


def test_every_delivery_order_gives_the_same_state():
    events = [
        event("e1", 1, "click", NOW - 50),
        event("e2", 2, "dismiss", NOW - 40),
        event("e3", 1, "dismiss", NOW - 30),
        event("e4", 2, "click", NOW - 20),
        event("e5", 3, "view", NOW - 10),
    ]
    results = set()
    for order in itertools.permutations(events):
        hot = InMemoryHotState()
        for ev in order:
            apply(hot, ev)
            apply(hot, ev)  # and a duplicate delivery of each
        recent = hot.get_signals(TENANT, KEY)
        merged = signals.merge_affinities({}, recent, NOW, HALF_LIFE)
        results.add(
            (
                json.dumps(merged, sort_keys=True),
                tuple(sorted(signals.dismissed_ad_ids(recent, None, NOW))),
            )
        )
    assert len(results) == 1


# --------------------------------------------------------------------------
# The most recent clear signal takes precedence
# --------------------------------------------------------------------------


def dismissed(recent):
    return signals.dismissed_ad_ids(recent, None, NOW)


def test_a_dismissal_after_a_click_withholds_the_ad():
    recent = [
        {"ad": 1, "type": "click", "at": NOW - 100, "f": []},
        {"ad": 1, "type": "dismiss", "at": NOW - 50, "f": []},
    ]
    assert dismissed(recent) == {1}


def test_a_click_after_a_dismissal_lifts_the_exclusion():
    recent = [
        {"ad": 1, "type": "dismiss", "at": NOW - 100, "f": []},
        {"ad": 1, "type": "click", "at": NOW - 50, "f": []},
    ]
    assert dismissed(recent) == set()


def test_event_time_decides_not_arrival_time():
    hot = InMemoryHotState()
    apply(hot, event("late-click", 1, "click", NOW - 10))  # happened last, arrives first
    apply(hot, event("early-dismiss", 1, "dismiss", NOW - 90))  # happened first, arrives last

    assert dismissed(hot.get_signals(TENANT, KEY)) == set()


def test_a_conversion_also_lifts_a_dismissal():
    recent = [
        {"ad": 1, "type": "dismiss", "at": NOW - 100, "f": []},
        {"ad": 1, "type": "conversion", "at": NOW - 50, "f": []},
    ]
    assert dismissed(recent) == set()


def test_a_durable_dismissal_is_still_honoured_after_the_signals_expire():
    durable = {"1": "2026-10-18T08:00:00+00:00"}
    moment = signals._epoch("2026-10-18T09:00:00+00:00")

    assert signals.dismissed_ad_ids([], durable, moment) == {1}
    assert signals.dismissed_ad_ids([], durable, moment + 25 * 3600) == set()


def test_a_fresh_click_beats_a_durable_dismissal():
    durable = {"1": "2026-10-18T08:00:00+00:00"}
    moment = signals._epoch("2026-10-18T09:00:00+00:00")
    click = [{"ad": 1, "type": "click", "at": moment - 60, "f": []}]

    assert signals.dismissed_ad_ids(click, durable, moment) == set()


def test_dismissals_last_only_for_the_window():
    recent = [{"ad": 1, "type": "dismiss", "at": NOW - signals.SIGNAL_WINDOW_SECONDS - 1, "f": []}]
    assert dismissed(recent) == set()


# --------------------------------------------------------------------------
# Folding into the durable profile
# --------------------------------------------------------------------------


def test_signals_already_in_a_rebuilt_profile_are_not_counted_again():
    rebuilt_at = NOW - 600
    sigs = [
        {"ad": 1, "type": "click", "at": rebuilt_at - 3600, "f": []},  # in the profile
        {"ad": 2, "type": "click", "at": rebuilt_at + 30, "f": []},  # after the rebuild
        {"ad": 3, "type": "click", "at": rebuilt_at - 30, "f": []},  # inside the safety margin
    ]

    kept = signals.not_yet_in_profile(sigs, rebuilt_at)

    assert sorted(s["ad"] for s in kept) == [2, 3]


def test_without_a_profile_every_signal_counts():
    sigs = [{"ad": 1, "type": "click", "at": 1.0, "f": []}]
    assert signals.not_yet_in_profile(sigs, None) == sigs


# --------------------------------------------------------------------------
# Storage behaviour
# --------------------------------------------------------------------------


def test_signals_are_per_visitor_and_per_tenant():
    hot = InMemoryHotState()
    apply(hot, event("e1", 1, "click", NOW))

    assert hot.get_signals(TENANT, OTHER_KEY) == []
    assert hot.get_signals(TENANT + 1, KEY) == []
    assert all(k.startswith("c360ads:7:") for k in hot.keys())


def test_only_the_most_recent_signals_are_kept():
    hot = InMemoryHotState()
    for i in range(SIGNAL_CAP + 25):
        apply(hot, event(f"e{i}", 1, "click", NOW - 10_000 + i))

    kept = hot.get_signals(TENANT, KEY)

    assert len(kept) == SIGNAL_CAP
    assert min(s["at"] for s in kept) == NOW - 10_000 + 25


def test_signals_expire_with_their_ttl(clock):
    hot = InMemoryHotState(clock=clock)
    hot.apply_event(TENANT, KEY, event("e1", 1, "click", NOW), SIMILAR_FEATURES, ttl_seconds=60)
    clock.advance(61)

    assert hot.get_signals(TENANT, KEY) == []


def test_erasing_a_visitor_removes_their_signals():
    hot = InMemoryHotState()
    apply(hot, event("e1", 1, "click", NOW))
    hot.delete_signals(TENANT, KEY)
    assert hot.get_signals(TENANT, KEY) == []


def test_unavailable_hot_state_raises_for_callers_to_fail_open():
    hot = InMemoryHotState()
    hot.available = False
    with pytest.raises(HotStateUnavailable):
        apply(hot, event("e1", 1, "click", NOW))
    with pytest.raises(HotStateUnavailable):
        hot.get_signals(TENANT, KEY)


def test_signal_weights_match_the_shared_contract():
    path = (
        Path(__file__).resolve().parents[3]
        / "docs" / "ad-personalization" / "contracts" / "profile-event-weights.json"
    )
    if not path.exists():
        pytest.skip("contract file not available in this checkout")
    assert signals.EVENT_WEIGHTS == json.loads(path.read_text())["event_weights"]
