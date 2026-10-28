"""The admin plane purges a visitor's Redis state by the ad server's key format.

The ad server (customer360-promotions/personalization/hot_state.py) names every
per-visitor key ``c360ads:<ad tenant id>:visitor:<hex key>:<what>`` and deletes by
``c360ads:<tenant>:visitor:<hex key>:*``. The two services share no code, so the
format is written out literally here: if either side changes it, a test fails.
"""

import fnmatch

import pytest

from core.utils import ad_hot_state

KEY = bytes.fromhex("0a" * 16)
OTHER_KEY = bytes.fromhex("0b" * 16)


class FakeRedis:
    def __init__(self, keys):
        self.keys = set(keys)
        self.deleted = []

    def scan_iter(self, match=None, count=None):
        return iter(sorted(k for k in self.keys if fnmatch.fnmatchcase(k, match)))

    def delete(self, *keys):
        self.deleted.extend(keys)
        self.keys -= set(keys)
        return len(keys)


def test_the_key_format_and_purge_pattern_are_pinned():
    assert ad_hot_state.KEY_PREFIX == "c360ads"
    assert ad_hot_state.visitor_key_pattern(7, KEY) == "c360ads:7:visitor:" + "0a" * 16 + ":*"


def test_keys_the_ad_server_writes_are_matched():
    # Examples of what hot_state.py writes for a visitor (counter and signals).
    written = [
        "c360ads:7:visitor:" + "0a" * 16 + ":counter:shown:12:20261018",
        "c360ads:7:visitor:" + "0a" * 16 + ":signals",
        "c360ads:7:visitor:" + "0a" * 16 + ":counter:rate:events:29000000",
    ]
    pattern = ad_hot_state.visitor_key_pattern(7, KEY)
    assert all(fnmatch.fnmatchcase(k, pattern) for k in written)


def test_purge_removes_only_that_visitor_in_that_tenant():
    mine = ["c360ads:7:visitor:" + "0a" * 16 + ":signals", "c360ads:7:visitor:" + "0a" * 16 + ":counter:x"]
    others = [
        "c360ads:7:visitor:" + "0b" * 16 + ":signals",  # another visitor
        "c360ads:8:visitor:" + "0a" * 16 + ":signals",  # another tenant
        "c360ads:7:settings",  # tenant-level cache
        "c360ads:7:event:abc",  # de-duplication marker
    ]
    redis = FakeRedis(mine + others)

    removed = ad_hot_state.purge_visitor(redis, 7, KEY)

    assert removed == 2
    assert redis.keys == set(others)


def test_purging_a_visitor_with_nothing_stored_removes_nothing():
    assert ad_hot_state.purge_visitor(FakeRedis([]), 7, KEY) == 0


def test_large_purges_are_deleted_in_batches():
    keys = [f"c360ads:7:visitor:{'0a' * 16}:counter:{i}" for i in range(450)]
    redis = FakeRedis(keys)

    assert ad_hot_state.purge_visitor(redis, 7, KEY) == 450
    assert redis.keys == set()


@pytest.mark.parametrize("tenant", [None, "7", True])
def test_the_tenant_must_be_an_int(tenant):
    with pytest.raises(ValueError):
        ad_hot_state.visitor_key_pattern(tenant, KEY)


def test_a_key_is_required():
    with pytest.raises(ValueError):
        ad_hot_state.visitor_key_pattern(7, b"")
