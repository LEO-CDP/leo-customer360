"""Read-only behavioral event compatibility API backed by S3/MinIO."""

import uuid
from datetime import datetime
from typing import Optional, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from core.cache import cache_response
from leo_customer360_dao.config import settings
from core.database import get_db
from leo_customer360_dao.repositories.event_query_repository import (
    EventDataSourceError,
    EventQueryError,
    EventQueryRepository,
)
from leo_customer360_dao.schemas.event_query import EventDeviceTypeVolumeRead, EventVolumeRead

router = APIRouter(prefix="/events", tags=["Behavioral Events"])


class EventQueryFilters(TypedDict):
    master_profile_id: Optional[uuid.UUID]
    domain: Optional[str]
    channel: Optional[str]
    event_category: Optional[str]
    event_name: Optional[str]
    data_source_id: Optional[uuid.UUID]


def get_event_query_repository() -> EventQueryRepository:
    return EventQueryRepository(settings)


def _tenant_id_from_request(request: Request) -> uuid.UUID:
    value = getattr(request.state, "tenant_id", None)
    if not value:
        raise HTTPException(status_code=401, detail="Tenant context is required")
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid tenant context") from exc


def _validate_days(days: int) -> None:
    if days > settings.event_query_max_days:
        raise HTTPException(
            status_code=422,
            detail=f"days may not exceed {settings.event_query_max_days}",
        )


def _query_filters(
    *,
    master_profile_id: Optional[uuid.UUID],
    domain: Optional[str],
    channel: Optional[str],
    event_category: Optional[str],
    event_name: Optional[str],
    data_source_id: Optional[uuid.UUID],
) -> EventQueryFilters:
    return {
        "master_profile_id": master_profile_id,
        "domain": domain,
        "channel": channel,
        "event_category": event_category,
        "event_name": event_name,
        "data_source_id": data_source_id,
    }


@router.get("/", response_model=list[EventVolumeRead])
@cache_response("events/s3/daily", ttl=settings.cache_ttl_seconds)
def list_event_volume_from_s3(
    request: Request,
    event_time_from: Optional[datetime] = None,
    days: int = Query(default=settings.event_query_max_days, ge=1),
    master_profile_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = None,
    channel: Optional[str] = None,
    event_category: Optional[str] = None,
    event_name: Optional[str] = None,
    data_source_id: Optional[uuid.UUID] = None,
    tenant_id: uuid.UUID = Depends(_tenant_id_from_request),
    db: Session = Depends(get_db),
    repository: EventQueryRepository = Depends(get_event_query_repository),
) -> list[EventVolumeRead]:
    """Return complete UTC-day totals for the event-volume chart."""
    _validate_days(days)
    try:
        rows = repository.query_daily_totals(
            db,
            tenant_id,
            event_time_from=event_time_from,
            days=days,
            **_query_filters(
                master_profile_id=master_profile_id,
                domain=domain,
                channel=channel,
                event_category=event_category,
                event_name=event_name,
                data_source_id=data_source_id,
            ),
        )
    except EventDataSourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EventQueryError as exc:
        raise HTTPException(status_code=503, detail="Event lake query failed") from exc
    return [EventVolumeRead.model_validate(row) for row in rows]


@router.get("/device-types", response_model=list[EventDeviceTypeVolumeRead])
@cache_response("events/s3/device-types", ttl=settings.cache_ttl_seconds)
def list_event_device_type_volume_from_s3(
    request: Request,
    days: int = Query(default=settings.event_query_max_days, ge=1),
    event_time_from: Optional[datetime] = None,
    data_source_id: Optional[uuid.UUID] = None,
    tenant_id: uuid.UUID = Depends(_tenant_id_from_request),
    db: Session = Depends(get_db),
    repository: EventQueryRepository = Depends(get_event_query_repository),
) -> list[EventDeviceTypeVolumeRead]:
    """Return complete event totals grouped by normalized device type."""
    _validate_days(days)
    try:
        rows = repository.query_device_type_totals(
            db,
            tenant_id,
            event_time_from=event_time_from,
            days=days,
            data_source_id=data_source_id,
        )
    except EventDataSourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EventQueryError as exc:
        raise HTTPException(status_code=503, detail="Event lake query failed") from exc
    return [EventDeviceTypeVolumeRead.model_validate(row) for row in rows]


all_events_routers = [router]
