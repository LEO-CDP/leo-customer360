"""Buffered FIFO writer that flushes tracking logs to object storage on a timer."""

import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any
from uuid import UUID

from core.storage import S3ObjectStorage, StoredTrackingLog, build_tracking_object

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedTrackingObject:
    """One tracking object queued for asynchronous S3 upload."""

    data_source_id: UUID
    bucket: str
    object_key: str
    body: bytes
    event_count: int
    received_at: datetime


class BufferedTrackingStorage:
    """Queue-backed storage that delays S3 writes until flush intervals."""

    def __init__(
        self,
        storage: S3ObjectStorage,
        flush_interval_seconds: int,
        max_queue_size: int,
        flush_batch_size: int,
    ):
        self.storage = storage
        self.flush_interval_seconds = max(1, int(flush_interval_seconds))
        self.flush_batch_size = max(1, int(flush_batch_size))
        self._queue: Queue[QueuedTrackingObject] = Queue(maxsize=max(1, int(max_queue_size)))
        self._stop_event = Event()
        self._state_lock = Lock()
        self._closed = False
        self._pending: deque[QueuedTrackingObject] = deque()
        self._worker = Thread(target=self._run, name="tracking-log-flusher", daemon=True)
        self._worker.start()

    def store_tracking_logs(
        self,
        data_source_id: UUID,
        events: list[dict[str, Any]],
        received_at: datetime,
    ) -> StoredTrackingLog:
        """Queue one tracking object and return quickly to the caller.

        If the queue is full, write synchronously to avoid dropping logs.
        """
        bucket, object_key, body = build_tracking_object(data_source_id, events, received_at)
        queued = QueuedTrackingObject(
            data_source_id=data_source_id,
            bucket=bucket,
            object_key=object_key,
            body=body,
            event_count=len(events),
            received_at=received_at,
        )

        with self._state_lock:
            if self._closed:
                raise RuntimeError("Tracking queue is closed")

        try:
            self._queue.put_nowait(queued)
        except Full:
            logger.warning("Tracking queue full; writing synchronously")
            return self.storage.store_prebuilt_tracking_object(
                data_source_id=queued.data_source_id,
                bucket=queued.bucket,
                object_key=queued.object_key,
                body=queued.body,
                event_count=queued.event_count,
                received_at=queued.received_at,
            )

        return StoredTrackingLog(
            data_source_id=queued.data_source_id,
            bucket=queued.bucket,
            object_key=queued.object_key,
            event_count=queued.event_count,
            received_at=queued.received_at,
        )

    def close(self, timeout_seconds: int = 30) -> None:
        """Stop the worker and flush all remaining queued tracking logs."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True

        self._stop_event.set()
        self._worker.join(timeout=max(1, timeout_seconds))
        if self._worker.is_alive():
            logger.error("Tracking flusher worker did not stop before timeout")

    def pending_count(self) -> int:
        """Return queued + in-flight tracking object count."""
        return int(self._queue.qsize() + len(self._pending))

    def _run(self) -> None:
        last_flush_at = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            elapsed = now - last_flush_at
            timeout = max(0.05, self.flush_interval_seconds - elapsed)

            try:
                queued = self._queue.get(timeout=timeout)
                self._pending.append(queued)
                if len(self._pending) >= self.flush_batch_size:
                    self._flush_pending_once()
                    last_flush_at = time.monotonic()
            except Empty:
                pass

            if self._pending and (time.monotonic() - last_flush_at) >= self.flush_interval_seconds:
                self._flush_pending_once()
                last_flush_at = time.monotonic()

        while True:
            try:
                self._pending.append(self._queue.get_nowait())
            except Empty:
                break

        while self._pending:
            flushed = self._flush_pending_once()
            if not flushed:
                logger.error(
                    "Tracking flusher stopped with %d unflushed objects",
                    len(self._pending),
                )
                break

    def _flush_pending_once(self) -> bool:
        """Flush all currently pending objects in FIFO order.

        Returns False when S3 write fails so the caller can retry later.
        """
        while self._pending:
            queued = self._pending[0]
            try:
                self.storage.store_prebuilt_tracking_object(
                    data_source_id=queued.data_source_id,
                    bucket=queued.bucket,
                    object_key=queued.object_key,
                    body=queued.body,
                    event_count=queued.event_count,
                    received_at=queued.received_at,
                )
            except Exception:
                logger.exception(
                    "Failed to flush tracking object %s/%s",
                    queued.bucket,
                    queued.object_key,
                )
                return False
            self._pending.popleft()
        return True
