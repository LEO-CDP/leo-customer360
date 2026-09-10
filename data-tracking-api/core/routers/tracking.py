"""HTTP routes for CDP tracking-log ingestion."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from core.buffered_storage import BufferedTrackingStorage, TrackingQueueError
from core.config import settings
from core.redis_cache import TrackingRequestProtection
from core.redis_queue import RedisStreamTrackingStorage
from core.schemas import TrackingLogRequest, TrackingLogResponse
from core.service import IdentityValidationError, TrackingLogService
from core.storage import ObjectStorageError, S3ObjectStorage, StoredTrackingLog, build_storage

router = APIRouter(prefix="/tracking", tags=["Tracking Logs"])
_storage: S3ObjectStorage | None = None
_protection: TrackingRequestProtection | None = None
_tracking_storage: BufferedTrackingStorage | RedisStreamTrackingStorage | None = None


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
            _tracking_storage = RedisStreamTrackingStorage(
                storage=storage,
                redis_client=protection.client,
                stream_name=settings.tracking_stream_name,
                consumer_group=settings.tracking_stream_group,
                max_stream_length=settings.tracking_stream_max_length,
                flush_batch_size=settings.tracking_log_flush_batch_size,
                block_ms=settings.tracking_stream_block_ms,
                claim_idle_ms=settings.tracking_stream_claim_idle_ms,
                retry_seconds=settings.tracking_stream_retry_seconds,
            )
        else:
            _tracking_storage = BufferedTrackingStorage(
                storage=storage,
                flush_interval_seconds=settings.time_to_flush_log,
                max_queue_size=settings.tracking_log_queue_max_size,
                flush_batch_size=settings.tracking_log_flush_batch_size,
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


@router.post("/logs", response_model=TrackingLogResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_tracking_logs(
    payload: TrackingLogRequest,
    request: Request,
    response: Response,
    protection: TrackingRequestProtection = Depends(get_protection),
    service: TrackingLogService = Depends(get_tracking_service),
) -> TrackingLogResponse:
    """Store a batch of source events in the current UTC hour partition."""
    if len(payload.events) > settings.max_events_per_request:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"A request may contain at most {settings.max_events_per_request} events",
        )

    user_agent = request.headers.get("user-agent")
    if protection.is_bot(user_agent):
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
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Tracking request rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    try:
        stored, cached_session_count = service.ingest(
            payload.data_source_id,
            payload.events,
            session_id=payload.session_id,
            anonymous_id=payload.anonymous_id,
            device_id=payload.device_id,
            device_fingerprint=payload.device_fingerprint,
            user_id=payload.user_id,
            metadata=payload.metadata,
        )
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except (ObjectStorageError, TrackingQueueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tracking ingestion is temporarily unavailable; retry the request",
            headers={"Retry-After": "1"},
        ) from exc
