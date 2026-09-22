"""HTTP routes for CDP tracking-log ingestion."""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from core.buffered_storage import BufferedTrackingStorage, TrackingQueueError
from core.config import Settings, settings
from core.device import parse_device_type
from core.redis_cache import TrackingRequestProtection, build_redis_client
from core.redis_queue import RedisStreamTrackingStorage
from core.metrics import tracking_metrics
from core.schemas import TrackingLogRequest, TrackingLogResponse
from core.service import IdentityValidationError, TrackingLogService
from core.storage import ObjectStorageError, S3ObjectStorage, StoredTrackingLog, build_storage

router = APIRouter(prefix="/tracking", tags=["Tracking Logs"])
_storage: S3ObjectStorage | None = None
_protection: TrackingRequestProtection | None = None
_tracking_storage: BufferedTrackingStorage | RedisStreamTrackingStorage | None = None


def _stream_socket_timeout_seconds(config: Settings) -> float:
    return max(1.0, config.tracking_stream_block_ms / 1000 + 1.0)


def get_storage() -> S3ObjectStorage:
    global _storage
    if _storage is None:
        _storage = build_storage(settings)
    return _storage


def get_protection() -> TrackingRequestProtection:
    global _protection
    if _protection is None:
        _protection = TrackingRequestProtection(settings)
    return _protection


def get_tracking_storage(
    storage: S3ObjectStorage = Depends(get_storage),
    protection: TrackingRequestProtection = Depends(get_protection),
) -> BufferedTrackingStorage | RedisStreamTrackingStorage:
    global _tracking_storage
    if _tracking_storage is None:
        if settings.tracking_queue_backend == "redis_stream":
            # XREADGROUP BLOCK may wait for the full block interval. Keep the
            # request cache's short timeout separate from the stream worker.
            _tracking_storage = RedisStreamTrackingStorage(
                storage=storage,
                redis_client=build_redis_client(
                    settings,
                    socket_timeout=_stream_socket_timeout_seconds(settings),
                ),
                stream_name=settings.tracking_stream_name,
                consumer_group=settings.tracking_stream_group,
                max_stream_length=settings.tracking_stream_max_length,
                flush_batch_size=settings.tracking_log_flush_batch_size,
                block_ms=settings.tracking_stream_block_ms,
                claim_idle_ms=settings.tracking_stream_claim_idle_ms,
                retry_seconds=settings.tracking_stream_retry_seconds,
                schema_version=settings.event_schema_version,
                ingestion_version=settings.event_ingestion_version,
                idempotency_ttl_seconds=settings.tracking_idempotency_ttl_seconds,
            )
        else:
            _tracking_storage = BufferedTrackingStorage(
                storage=storage,
                flush_interval_seconds=settings.time_to_flush_log,
                max_queue_size=settings.tracking_log_queue_max_size,
                flush_batch_size=settings.tracking_log_flush_batch_size,
                schema_version=settings.event_schema_version,
                ingestion_version=settings.event_ingestion_version,
            )
    return _tracking_storage


def get_tracking_service(
    storage: BufferedTrackingStorage = Depends(get_tracking_storage),
    protection: TrackingRequestProtection = Depends(get_protection),
) -> TrackingLogService:
    return TrackingLogService(storage, protection.session_cache)


def shutdown_tracking_storage() -> None:
    """Flush and stop the in-process tracking queue worker."""
    global _tracking_storage
    if _tracking_storage is not None:
        _tracking_storage.close()
        _tracking_storage = None


@router.get("/queue-status")
def tracking_queue_status(
    tracking_storage: BufferedTrackingStorage | RedisStreamTrackingStorage = Depends(
        get_tracking_storage
    ),
) -> dict[str, float | int]:
    """Expose bounded queue depth and oldest-pending age for operations."""
    return {
        "queue_depth": tracking_storage.pending_count(),
        "oldest_pending_age_seconds": tracking_storage.oldest_pending_age_seconds(),
    }


def ingest_tracking_request(
    payload: TrackingLogRequest,
    service: TrackingLogService,
    device_type: Optional[str] = None,
) -> tuple[StoredTrackingLog, int]:
    """Persist one validated tracking request through the S3-backed service."""
    return service.ingest(
        payload.data_source_id,
        payload.events,
        session_id=payload.session_id,
        anonymous_id=payload.anonymous_id,
        device_id=payload.device_id,
        device_fingerprint=payload.device_fingerprint,
        user_id=payload.user_id,
        device_type=device_type,
        metadata=payload.metadata,
    )


def build_tracking_request(
    data_source_id: UUID,
    events: list[dict[str, Any]],
    *,
    session_id: Optional[str] = None,
    anonymous_id: Optional[str] = None,
    device_id: Optional[str] = None,
    device_fingerprint: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> TrackingLogRequest:
    """Build the canonical request envelope used by every tracking source."""
    return TrackingLogRequest(
        data_source_id=data_source_id,
        session_id=session_id,
        anonymous_id=anonymous_id,
        device_id=device_id,
        device_fingerprint=device_fingerprint,
        user_id=user_id,
        metadata=metadata or {},
        events=events,
    )


@router.post("/logs", response_model=TrackingLogResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_tracking_logs(
    payload: TrackingLogRequest,
    request: Request,
    response: Response,
    protection: TrackingRequestProtection = Depends(get_protection),
    service: TrackingLogService = Depends(get_tracking_service),
) -> TrackingLogResponse:
    """Store a batch of source events in the current UTC hour partition."""
    tracking_metrics.increment("tracking_ingestion_requests_total")
    if len(payload.events) > settings.max_events_per_request:
        tracking_metrics.increment("tracking_ingestion_rejections_total")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"A request may contain at most {settings.max_events_per_request} events",
        )

    user_agent = request.headers.get("user-agent")
    if protection.is_bot(user_agent):
        tracking_metrics.increment("tracking_ingestion_filtered_total")
        return TrackingLogResponse(
            data_source_id=payload.data_source_id,
            accepted=False,
            filtered=True,
            filter_reason="bot_user_agent",
            event_count=0,
            received_at=datetime.now(timezone.utc),
        )

    decision = protection.allow_request(request, payload.data_source_id)
    if not decision.allowed:
        tracking_metrics.increment("tracking_ingestion_rate_limited_total")
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Tracking request rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    try:
        stored, cached_session_count = ingest_tracking_request(
            payload,
            service,
            device_type=parse_device_type(user_agent),
        )
        tracking_metrics.increment("tracking_ingestion_batches_total")
        tracking_metrics.increment("tracking_ingestion_events_total", len(payload.events))
        return TrackingLogResponse(
            data_source_id=stored.data_source_id,
            bucket=stored.bucket,
            object_key=stored.object_key,
            event_count=stored.event_count,
            received_at=stored.received_at,
            cached_session_count=cached_session_count,
            queue_message_id=stored.queue_message_id,
        )
    except IdentityValidationError as exc:
        tracking_metrics.increment("tracking_ingestion_rejections_total")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except (ObjectStorageError, TrackingQueueError) as exc:
        tracking_metrics.increment("tracking_ingestion_errors_total")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tracking ingestion is temporarily unavailable; retry the request",
            headers={"Retry-After": "1"},
        ) from exc
