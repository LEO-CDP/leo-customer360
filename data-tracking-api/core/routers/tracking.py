"""HTTP routes for CDP tracking-log ingestion."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from core.config import settings
from core.schemas import TrackingLogRequest, TrackingLogResponse
from core.service import TrackingLogService
from core.redis_cache import TrackingRequestProtection
from core.storage import ObjectStorageError, S3ObjectStorage, StoredTrackingLog, build_storage

router = APIRouter(prefix="/tracking", tags=["Tracking Logs"])
_storage: S3ObjectStorage | None = None
_protection: TrackingRequestProtection | None = None


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


def get_tracking_service(
    storage: S3ObjectStorage = Depends(get_storage),
    protection: TrackingRequestProtection = Depends(get_protection),
) -> TrackingLogService:
    return TrackingLogService(storage, protection.session_cache)


@router.post("/logs", response_model=TrackingLogResponse, status_code=status.HTTP_201_CREATED)
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

    decision = protection.allow_request(request)
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
            user_id=payload.user_id,
        )
        return TrackingLogResponse(
            data_source_id=stored.data_source_id,
            bucket=stored.bucket,
            object_key=stored.object_key,
            event_count=stored.event_count,
            received_at=stored.received_at,
            cached_session_count=cached_session_count,
        )
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tracking storage is temporarily unavailable",
        ) from exc
