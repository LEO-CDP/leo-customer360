"""Buffered writer: bounded, batched, retrying, and loud about overflow."""

import threading
import time

import pytest

from personalization.buffered_writer import BufferedWriter, BufferOverflow


def _wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_flush_writes_everything_in_batches_of_the_configured_size():
    batches = []
    writer = BufferedWriter(batches.append, max_size=100, batch_size=3, start=False)
    writer.put_many(range(7))

    assert writer.flush() == 7

    assert batches == [[0, 1, 2], [3, 4, 5], [6]]
    assert writer.pending == 0
    assert writer.flushed == 7


def test_the_buffer_is_bounded_and_overflow_is_all_or_nothing():
    writer = BufferedWriter(lambda batch: None, max_size=5, start=False)
    writer.put_many([1, 2, 3])

    with pytest.raises(BufferOverflow):
        writer.put_many([4, 5, 6])  # would exceed the bound: none of them are added

    assert writer.pending == 3
    writer.put_many([4, 5])  # exactly fits
    with pytest.raises(BufferOverflow):
        writer.put(6)


def test_a_failed_flush_keeps_the_items_in_order():
    calls = []

    def flaky(batch):
        calls.append(list(batch))
        if len(calls) == 1:
            raise RuntimeError("database down")

    writer = BufferedWriter(flaky, max_size=100, batch_size=10, start=False)
    writer.put_many([1, 2, 3])

    with pytest.raises(RuntimeError):
        writer.flush()
    assert writer.pending == 3  # nothing lost

    assert writer.flush() == 3
    assert calls == [[1, 2, 3], [1, 2, 3]]


def test_the_background_thread_flushes_without_being_asked():
    seen = []
    writer = BufferedWriter(
        seen.extend, max_size=100, batch_size=100, flush_interval_seconds=0.02, name="test"
    )
    try:
        writer.put_many(["a", "b", "c"])
        assert _wait_for(lambda: seen == ["a", "b", "c"])
    finally:
        writer.close()


def test_a_full_batch_wakes_the_flusher_before_the_interval():
    seen = []
    writer = BufferedWriter(
        seen.extend, max_size=100, batch_size=2, flush_interval_seconds=30.0, name="test"
    )
    try:
        writer.put_many([1, 2])  # reaches the batch size: flushes now, not in 30 seconds
        assert _wait_for(lambda: seen == [1, 2])
    finally:
        writer.close()


def test_background_retries_recover_from_a_transient_failure(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)  # no real back-off in the test
    attempts = {"n": 0}
    written = []

    def flaky(batch):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise RuntimeError("temporary")
        written.extend(batch)

    writer = BufferedWriter(
        flaky, max_size=100, batch_size=10, flush_interval_seconds=0.02, name="test"
    )
    try:
        writer.put_many([1, 2, 3])
        assert _wait_for(lambda: written == [1, 2, 3])
        assert writer.failures == 2
        assert writer.dropped == 0
    finally:
        writer.close()


def test_a_permanently_failing_flush_drops_the_batch_instead_of_growing_forever(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def always_fails(batch):
        raise RuntimeError("down for good")

    writer = BufferedWriter(
        always_fails,
        max_size=100,
        batch_size=10,
        flush_interval_seconds=0.02,
        max_flush_failures=3,
        name="test",
    )
    try:
        writer.put_many([1, 2, 3])
        assert _wait_for(lambda: writer.dropped == 3)
        assert writer.pending == 0
    finally:
        writer.close()


def test_close_drains_what_is_left_and_then_refuses_new_items():
    seen = []
    writer = BufferedWriter(
        seen.extend, max_size=100, batch_size=100, flush_interval_seconds=30.0, name="test"
    )
    writer.put_many([1, 2, 3])

    writer.close()

    assert seen == [1, 2, 3]
    with pytest.raises(BufferOverflow):
        writer.put(4)


def test_concurrent_producers_never_lose_or_duplicate_items():
    seen = []
    lock = threading.Lock()

    def collect(batch):
        with lock:
            seen.extend(batch)

    writer = BufferedWriter(
        collect, max_size=10_000, batch_size=50, flush_interval_seconds=0.01, name="test"
    )

    def produce(start):
        for n in range(start, start + 500):
            writer.put(n)

    threads = [threading.Thread(target=produce, args=(i * 500,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    writer.close()

    assert sorted(seen) == list(range(2000))


def test_invalid_sizes_are_rejected():
    with pytest.raises(ValueError):
        BufferedWriter(lambda b: None, max_size=0, start=False)
    with pytest.raises(ValueError):
        BufferedWriter(lambda b: None, max_size=10, batch_size=0, start=False)
