"""Hold-out assignment: deterministic per visitor, the right share, and nothing stored."""

import hashlib
from decimal import Decimal

import pytest

from personalization.holdout import holdout_bucket, is_holdout

SALT = "2026-Q4"


def _key(n: int) -> bytes:
    return hashlib.sha256(f"visitor-{n}".encode()).digest()[:16]


def test_a_visitor_stays_in_the_same_group_across_calls():
    key = _key(1)
    results = {is_holdout(7, key, SALT, 10.0) for _ in range(50)}
    assert len(results) == 1


def test_the_bucket_is_in_range_and_stable():
    for n in range(200):
        bucket = holdout_bucket(7, _key(n), SALT)
        assert 0 <= bucket < 10_000
        assert bucket == holdout_bucket(7, _key(n), SALT)


def test_about_ten_percent_are_held_out_at_the_default():
    held = sum(is_holdout(7, _key(n), SALT, 10.0) for n in range(20_000))
    assert 0.09 <= held / 20_000 <= 0.11


def test_the_share_follows_the_configured_percentage():
    for pct in (1, 5, 25, 50):
        held = sum(is_holdout(7, _key(n), SALT, pct) for n in range(20_000))
        assert abs(held / 20_000 - pct / 100) < 0.015, pct


def test_zero_never_holds_out_and_one_hundred_always_does():
    keys = [_key(n) for n in range(500)]
    assert not any(is_holdout(7, k, SALT, 0) for k in keys)
    assert not any(is_holdout(7, k, SALT, -5) for k in keys)
    assert all(is_holdout(7, k, SALT, 100) for k in keys)
    assert all(is_holdout(7, k, SALT, 150) for k in keys)


def test_a_smaller_share_is_always_a_subset_of_a_larger_one():
    """Raising the share only adds visitors; nobody flips back out."""
    for n in range(2_000):
        key = _key(n)
        if is_holdout(7, key, SALT, 5):
            assert is_holdout(7, key, SALT, 10)


def test_a_new_salt_reshuffles_who_is_held_out():
    keys = [_key(n) for n in range(5_000)]
    before = [is_holdout(7, k, "period-1", 10) for k in keys]
    after = [is_holdout(7, k, "period-2", 10) for k in keys]
    assert before != after
    # Roughly the same size, different people.
    assert abs(sum(before) - sum(after)) < 150


def test_tenants_are_assigned_independently():
    keys = [_key(n) for n in range(5_000)]
    a = [is_holdout(1, k, SALT, 10) for k in keys]
    b = [is_holdout(2, k, SALT, 10) for k in keys]
    assert a != b


@pytest.mark.parametrize("pct", [10, 10.0, Decimal("10.00"), "10"])
def test_the_percentage_can_be_any_numeric_type(pct):
    key = _key(3)
    assert is_holdout(7, key, SALT, pct) == is_holdout(7, key, SALT, 10.0)
