"""Durable Redis Streams handoff for asynchronous tracking-log delivery."""

import json
import logging
from datetime import datetime
from threading import Event, Thread
from typing import Any, Iterable
from uuid import UUID

import redis
from redis.exceptions import ResponseError

from core.buffered_storage import TrackingQueueError, TrackingQueueFullError
from core.storage import S3ObjectStorage, StoredTrackingLog, build_tracking_object

logger = logging.getLogger(__name__)

_PUBLISH_STREAM_SCRIPT = """
local stream_length = redis.call('XLEN', KEYS[1])
local max_length = tonumber(ARGV[2])
if max_length > 0 and stream_length >= max_length then
    return redis.error_reply('TRACKING_STREAM_FULL')
end
return redis.call('XADD', KEYS[1], '*', 'payload', ARGV[1])
"""


class TrackingQueueUnavailableError(TrackingQueueError):
    """Raised when the broker cannot accept a tracking batch."""


class RedisStreamTrackingStorage:
    """Publish batches to Redis Streams and upload them from a worker thread.

    Redis Streams are used instead of Pub/Sub because messages remain pending
    until the S3 write is acknowledged and can be claimed by another worker.
    """

    def __init__(
        self,
        storage: S3ObjectStorage,
        redis_client: Any,
        stream_name: str,
        consumer_group: str,
        max_stream_length: int,
        flush_batch_size: int,
        block_ms: int,
        claim_idle_ms: int,
        retry_seconds: float,
        consumer_name: str | None = None,
    ):
        self.storage = storage
        self.redis = redis_client
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.max_stream_length = max(1, int(max_stream_length))
        self.flush_batch_size = max(1, int(flush_batch_size))
        self.block_ms = max(1, int(block_ms))
        self.claim_idle_ms = max(1, int(claim_idle_ms))
        self.retry_seconds = max(0.1, float(retry_seconds))
        self.consumer_name = consumer_name or f"tracking-worker-{id(self)}"
        self._stop_event = Event()
        self._closed = False
        try:
            self._ensure_consumer_group()
        except redis.RedisError:
            logger.warning("Tracking Redis broker unavailable during worker startup")
        self._worker = Thread(target=self._run, name="tracking-redis-flusher", daemon=True)
        self._worker.start()

    def store_tracking_logs(
        self,
        data_source_id: UUID,
        events: list[dict[str, Any]],
        received_at: datetime,
    ) -> StoredTrackingLog:
        """Build a batch and enqueue it without performing an S3 request."""
        bucket, object_key, body = build_tracking_object(data_source_id, events, received_at)
        payload = json.dumps(
            {
                "data_source_id": str(data_source_id),
                "bucket": bucket,
                "object_key": object_key,
                "body": body.decode("utf-8"),
                "event_count": len(events),
                "received_at": received_at.isoformat(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            message_id = str(
                self.redis.eval(
                    _PUBLISH_STREAM_SCRIPT,
                    1,
                    self.stream_name,
                    payload,
                    str(self.max_stream_length),
                )
            )
        except ResponseError as exc:
            if "TRACKING_STREAM_FULL" in str(exc):
                raise TrackingQueueFullError("Tracking broker queue is full") from exc
            raise TrackingQueueUnavailableError(
                "Tracking broker is temporarily unavailable"
            ) from exc
        except redis.RedisError as exc:
            raise TrackingQueueUnavailableError(
                "Tracking broker is temporarily unavailable"
            ) from exc

        return StoredTrackingLog(
            data_source_id=data_source_id,
            bucket=bucket,
            object_key=object_key,
            event_count=len(events),
            received_at=received_at,
            queue_message_id=message_id,
        )

    def check_connection(self) -> None:
        """Raise when Redis cannot accept the tracking stream handoff."""
        try:
            self.redis.ping()
            self._ensure_consumer_group()
        except redis.RedisError as exc:
            raise TrackingQueueUnavailableError("Tracking broker is unavailable") from exc

    def close(self, timeout_seconds: int = 5) -> None:
        """Stop the worker; unacknowledged messages remain recoverable in Redis."""
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._worker.join(timeout=max(1, timeout_seconds))
        if self._worker.is_alive():
            logger.error("Redis tracking worker did not stop before timeout")

    def pending_count(self) -> int:
        """Return the broker stream length when Redis is reachable."""
        try:
            return int(self.redis.xlen(self.stream_name))
        except redis.RedisError:
            return 0

    def _ensure_consumer_group(self) -> None:
        try:
            self.redis.xgroup_create(
                self.stream_name,
                self.consumer_group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._ensure_consumer_group()
                messages = self._read_pending()
                if not messages:
                    messages = self._read_new_messages()
                if messages and not self._process_messages(messages):
                    self._stop_event.wait(self.retry_seconds)
            except redis.RedisError:
                logger.warning("Tracking Redis worker unavailable; retrying", exc_info=True)
                self._stop_event.wait(self.retry_seconds)

    def _read_pending(self) -> list[tuple[str, dict[str, str]]]:
        pending = self.redis.xreadgroup(
            self.consumer_group,
            self.consumer_name,
            {self.stream_name: "0"},
            count=self.flush_batch_size,
        )
        if pending:
            return _flatten_messages(pending)

        claimed = self.redis.xautoclaim(
            self.stream_name,
            self.consumer_group,
            self.consumer_name,
            self.claim_idle_ms,
            start_id="0-0",
            count=self.flush_batch_size,
        )
        return list(claimed[1]) if claimed else []

    def _read_new_messages(self) -> list[tuple[str, dict[str, str]]]:
        messages = self.redis.xreadgroup(
            self.consumer_group,
            self.consumer_name,
            {self.stream_name: ">"},
            count=self.flush_batch_size,
            block=self.block_ms,
        )
        return _flatten_messages(messages)

    def _process_messages(self, messages: Iterable[tuple[str, dict[str, str]]]) -> bool:
        for message_id, fields in messages:
            try:
                queued = _decode_message(fields)
                self.storage.store_prebuilt_tracking_object(
                    data_source_id=queued["data_source_id"],
                    bucket=queued["bucket"],
                    object_key=queued["object_key"],
                    body=queued["body"],
                    event_count=queued["event_count"],
                    received_at=queued["received_at"],
                )
                self.redis.xack(self.stream_name, self.consumer_group, message_id)
                self.redis.xdel(self.stream_name, message_id)
            except Exception:
                logger.exception("Failed to flush tracking stream message %s", message_id)
                return False
        return True


def _flatten_messages(
    streams: Iterable[tuple[str, Iterable[tuple[str, dict[str, str]]]]],
) -> list[tuple[str, dict[str, str]]]:
    return [message for _stream_name, messages in streams for message in messages]


def _decode_message(fields: dict[str, str]) -> dict[str, Any]:
    payload = fields["payload"]
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    decoded = json.loads(payload)
    return {
        "data_source_id": UUID(decoded["data_source_id"]),
        "bucket": decoded["bucket"],
        "object_key": decoded["object_key"],
        "body": decoded["body"].encode("utf-8"),
        "event_count": int(decoded["event_count"]),
        "received_at": datetime.fromisoformat(decoded["received_at"]),
    }