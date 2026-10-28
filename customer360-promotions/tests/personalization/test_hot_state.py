"""Hot state: tenant-prefixed keys, TTLs, dedupe, counters, and Redis failure handling."""

from types import SimpleNamespace

import pytest
import redis

from personalization.hot_state import (
    HotState,
    HotStateUnavailable,
    InMemoryHotState,
    make_key,
)

# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------


def test_keys_are_prefixed_with_the_tenant():
    assert make_key(7, "settings") == "c360ads:7:settings"
    assert make_key(7, "visitor", b"\xab\xcd", "counter", "shown") == (
        "c360ads:7:visitor:abcd:counter:shown"
    )


@pytest.mark.parametrize(
    "args",
    [("7", "x"), (True, "x"), (7,), (7, ""), (None, "x")],
)
def test_malformed_keys_are_rejected(args):
    with pytest.raises(ValueError):
        make_key(*args)


# --------------------------------------------------------------------------
# A tiny fake of the parts of redis.Redis that HotState uses
# --------------------------------------------------------------------------


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def incrby(self, key, amount):
        self.ops.append(("incrby", key, amount))
        return self

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))
        return self

    def execute(self):
        self.client._check()
        results = []
        for op, key, arg in self.ops:
            if op == "incrby":
                self.client.store[key] = str(int(self.client.store.get(key, 0)) + arg)
                results.append(int(self.client.store[key]))
            else:
                self.client.ttls[key] = arg
                results.append(True)
        return results


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}
        self.error = None

    def _check(self):
        if self.error:
            raise self.error

    def get(self, key):
        self._check()
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    def delete(self, key):
        self._check()
        self.store.pop(key, None)

    def mget(self, keys):
        self._check()
        return [self.store.get(k) for k in keys]

    def pipeline(self):
        return FakePipeline(self)

    def ping(self):
        self._check()
        return True


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def redis_state(fake_redis):
    return HotState(fake_redis)


# --------------------------------------------------------------------------
# The Redis-backed implementation
# --------------------------------------------------------------------------


def test_json_values_round_trip_and_get_a_ttl(redis_state, fake_redis):
    redis_state.set_json(7, ("settings",), [{"enabled": True}], ttl_seconds=60)
    assert redis_state.get_json(7, "settings") == [{"enabled": True}]
    assert fake_redis.ttls["c360ads:7:settings"] == 60


def test_a_missing_value_is_none(redis_state):
    assert redis_state.get_json(7, "nothing") is None


def test_a_corrupt_value_is_ignored_not_raised(redis_state, fake_redis):
    fake_redis.store["c360ads:7:settings"] = "{not json"
    assert redis_state.get_json(7, "settings") is None


def test_an_event_id_is_a_duplicate_the_second_time_only(redis_state):
    assert redis_state.seen_event(7, "evt-1") is False
    assert redis_state.seen_event(7, "evt-1") is True


def test_the_same_event_id_in_another_tenant_is_not_a_duplicate(redis_state):
    redis_state.seen_event(7, "evt-1")
    assert redis_state.seen_event(8, "evt-1") is False


def test_counters_count_per_visitor_with_a_sliding_ttl(redis_state, fake_redis):
    key_a, key_b = b"\x01" * 16, b"\x02" * 16
    assert redis_state.incr_counter(7, key_a, "shown:ad1", ttl_seconds=3600) == 1
    assert redis_state.incr_counter(7, key_a, "shown:ad1", ttl_seconds=3600) == 2
    redis_state.incr_counter(7, key_b, "shown:ad1", ttl_seconds=3600)

    assert redis_state.get_counters(7, key_a, ["shown:ad1", "shown:ad2"]) == {
        "shown:ad1": 2,
        "shown:ad2": 0,
    }
    assert redis_state.get_counters(7, key_b, ["shown:ad1"]) == {"shown:ad1": 1}
    assert redis_state.get_counters(7, key_a, []) == {}
    assert fake_redis.ttls[f"c360ads:7:visitor:{key_a.hex()}:counter:shown:ad1"] == 3600


def test_counters_are_isolated_between_tenants(redis_state):
    key = b"\x01" * 16
    redis_state.incr_counter(7, key, "shown:ad1", ttl_seconds=60)
    assert redis_state.get_counters(8, key, ["shown:ad1"]) == {"shown:ad1": 0}


@pytest.mark.parametrize(
    "error",
    [redis.exceptions.ConnectionError("down"), redis.exceptions.TimeoutError("slow"), OSError("reset")],
)
def test_any_redis_failure_becomes_hot_state_unavailable(redis_state, fake_redis, error):
    fake_redis.error = error
    with pytest.raises(HotStateUnavailable):
        redis_state.get_json(7, "settings")
    with pytest.raises(HotStateUnavailable):
        redis_state.set_json(7, ("settings",), {}, 60)
    with pytest.raises(HotStateUnavailable):
        redis_state.seen_event(7, "evt")
    with pytest.raises(HotStateUnavailable):
        redis_state.incr_counter(7, b"k" * 16, "n", 60)
    with pytest.raises(HotStateUnavailable):
        redis_state.get_counters(7, b"k" * 16, ["n"])
    assert redis_state.ping() is False


def test_a_disabled_hot_state_always_reports_unavailable():
    disabled = HotState(None)
    with pytest.raises(HotStateUnavailable):
        disabled.get_json(7, "settings")
    assert disabled.ping() is False


def test_from_settings_respects_the_cache_switch():
    settings = SimpleNamespace(cache_enabled=False)
    with pytest.raises(HotStateUnavailable):
        HotState.from_settings(settings).get_json(7, "x")


def test_an_unreachable_redis_fails_fast_within_the_time_budget():
    """No Redis on this port: the call must fail quickly with the fallback signal."""
    import time

    settings = SimpleNamespace(
        cache_enabled=True,
        redis_host="127.0.0.1",
        redis_port=1,
        redis_db=0,
        redis_password=None,
        personalization_time_budget_ms=25,
    )
    state = HotState.from_settings(settings)
    started = time.monotonic()
    with pytest.raises(HotStateUnavailable):
        state.get_json(7, "settings")
    assert time.monotonic() - started < 1.0


# --------------------------------------------------------------------------
# The in-memory twin used by the other tests
# --------------------------------------------------------------------------


def test_in_memory_values_expire(hot_state, clock):
    hot_state.set_json(7, ("settings",), {"a": 1}, ttl_seconds=60)
    assert hot_state.get_json(7, "settings") == {"a": 1}
    clock.advance(61)
    assert hot_state.get_json(7, "settings") is None


def test_in_memory_counters_slide_their_ttl(hot_state, clock):
    key = b"\x01" * 16
    hot_state.incr_counter(7, key, "n", ttl_seconds=60)
    clock.advance(40)
    hot_state.incr_counter(7, key, "n", ttl_seconds=60)  # slides the expiry
    clock.advance(40)
    assert hot_state.get_counters(7, key, ["n"]) == {"n": 2}
    clock.advance(30)
    assert hot_state.get_counters(7, key, ["n"]) == {"n": 0}


def test_in_memory_dedupe_and_isolation(hot_state):
    assert hot_state.seen_event(7, "e") is False
    assert hot_state.seen_event(7, "e") is True
    assert hot_state.seen_event(8, "e") is False


def test_in_memory_unavailable_switch_raises_everywhere(hot_state):
    hot_state.available = False
    with pytest.raises(HotStateUnavailable):
        hot_state.get_json(7, "x")
    with pytest.raises(HotStateUnavailable):
        hot_state.incr_counter(7, b"k" * 16, "n", 60)
    assert hot_state.ping() is False


def test_every_in_memory_key_carries_a_tenant_prefix(hot_state):
    hot_state.set_json(7, ("settings",), {}, 60)
    hot_state.incr_counter(7, b"k" * 16, "n", 60)
    hot_state.seen_event(7, "e")
    assert hot_state.keys()
    assert all(k.startswith("c360ads:7:") for k in hot_state.keys())


def test_the_two_implementations_share_one_interface():
    for name in ("get_json", "set_json", "delete", "seen_event", "incr_counter", "get_counters", "ping"):
        assert callable(getattr(HotState, name))
        assert callable(getattr(InMemoryHotState, name))
