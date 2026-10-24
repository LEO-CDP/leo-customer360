"""A bounded in-process buffer with batched background writes.

Ad events and serving decisions arrive at hundreds per second but should be
written to PostgreSQL in batches, not one insert per request. Callers ``put``
items and return immediately; a background thread flushes them in batches
through a callback.

Two behaviours matter for the serving path:

* **Bounded.** The buffer holds at most ``max_size`` items. When it is full,
  ``put`` raises :class:`BufferOverflow` and the caller answers HTTP 503 with
  ``Retry-After`` (events) or simply skips the write (decisions). The server
  never blocks, and memory never grows without bound.
* **Never loses data silently.** A failed flush puts the batch back and retries
  with back-off. After ``max_flush_failures`` consecutive failures the batch is
  dropped and counted, so a database outage cannot grow memory forever.

Same idea as ``customer360-event-api/core/buffered_storage.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Generic, Iterable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _error_types(exc: BaseException) -> str:
    """Exception type names only (see the note where it is used)."""
    original = getattr(exc, "orig", None)
    name = type(exc).__name__
    return f"{name}({type(original).__name__})" if original is not None else name


class BufferOverflow(RuntimeError):
    """The buffer is full; the caller should shed load (HTTP 503 + Retry-After)."""


class BufferedWriter(Generic[T]):
    """Collect items and flush them in batches on a background thread."""

    def __init__(
        self,
        flush: Callable[[list[T]], None],
        *,
        max_size: int,
        batch_size: int = 500,
        flush_interval_seconds: float = 1.0,
        max_flush_failures: int = 5,
        name: str = "buffered-writer",
        start: bool = True,
    ) -> None:
        if max_size < 1 or batch_size < 1:
            raise ValueError("max_size and batch_size must be at least 1")
        self._flush_fn = flush
        self._max_size = max_size
        self._batch_size = batch_size
        self._interval = flush_interval_seconds
        self._max_failures = max_flush_failures
        self._name = name

        self._items: deque[T] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._thread: threading.Thread | None = None

        self.flushed = 0
        self.dropped = 0
        self.failures = 0

        if start:
            self.start()

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def put(self, item: T) -> None:
        self.put_many([item])

    def put_many(self, items: Iterable[T]) -> None:
        """Add items all-or-nothing. Raises BufferOverflow if they would not fit."""
        batch = list(items)
        with self._lock:
            if self._closed:
                raise BufferOverflow(f"{self._name} is closed")
            if len(self._items) + len(batch) > self._max_size:
                raise BufferOverflow(f"{self._name} is full ({self._max_size} items)")
            self._items.extend(batch)
            should_wake = len(self._items) >= self._batch_size
        if should_wake:
            self._wake.set()

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._items)

    # ------------------------------------------------------------------
    # Flushing
    # ------------------------------------------------------------------

    def _take(self, limit: int) -> list[T]:
        with self._lock:
            count = min(limit, len(self._items))
            return [self._items.popleft() for _ in range(count)]

    def _put_back(self, batch: list[T]) -> None:
        with self._lock:
            self._items.extendleft(reversed(batch))

    def flush(self) -> int:
        """Write everything currently buffered, synchronously. Returns items written."""
        written = 0
        while True:
            batch = self._take(self._batch_size)
            if not batch:
                return written
            try:
                self._flush_fn(batch)
            except Exception:  # noqa: BLE001 - the callback is caller-supplied
                self._put_back(batch)
                raise
            written += len(batch)
            self.flushed += len(batch)

    def _flush_with_retry(self) -> None:
        consecutive = 0
        while True:
            batch = self._take(self._batch_size)
            if not batch:
                return
            try:
                self._flush_fn(batch)
            except Exception as exc:  # noqa: BLE001
                consecutive += 1
                self.failures += 1
                # Type names only: a database error can quote the failing row, which
                # carries visitor keys, and a traceback prints that message.
                logger.error(
                    "%s: flush of %d items failed (%d): %s",
                    self._name, len(batch), consecutive, _error_types(exc),
                )
                if consecutive >= self._max_failures:
                    self.dropped += len(batch)
                    logger.error("%s: dropped %d items after repeated failures", self._name, len(batch))
                    consecutive = 0
                    continue
                self._put_back(batch)
                time.sleep(min(0.1 * (2 ** consecutive), 2.0))
                continue
            consecutive = 0
            self.flushed += len(batch)

    def _run(self) -> None:
        while not self._closed:
            self._wake.wait(self._interval)
            self._wake.clear()
            self._flush_with_retry()
        self._flush_with_retry()  # drain what is left on shutdown

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def close(self, timeout: float = 10.0) -> None:
        """Stop accepting items, drain the buffer, and stop the thread."""
        with self._lock:
            self._closed = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        else:
            try:
                self.flush()
            except Exception as exc:  # noqa: BLE001
                logger.error("%s: final flush failed: %s", self._name, _error_types(exc))
