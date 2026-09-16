"""Read-only behavioral event compatibility API backed by S3/MinIO."""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_db
from core.repositories.event_query_repository import EventQueryError, EventQueryRepository
from core.schemas.event_query import EventQueryRead

router = APIRouter(prefix="/events", tags=["Behavioral Events"])


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


@router.get("/", response_model=list[EventQueryRead])
def list_events_from_s3(
    request: Request,
    event_time_from: Optional[datetime] = None,
    days: int = Query(default=settings.event_query_max_days, ge=1),
    limit: int = Query(default=settings.api_default_page_size, ge=1, le=settings.api_max_page_size),
    master_profile_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = None,
    channel: Optional[str] = None,
    event_category: Optional[str] = None,
    event_name: Optional[str] = None,
    db: Session = Depends(get_db),
    repository: EventQueryRepository = Depends(get_event_query_repository),
) -> list[EventQueryRead]:
    """Query canonical event envelopes from the tenant's S3/MinIO source buckets."""
    tenant_id = _tenant_id_from_request(request)
    if days > settings.event_query_max_days:
        raise HTTPException(
            status_code=422,
            detail=f"days may not exceed {settings.event_query_max_days}",
        )
    try:
        rows = repository.query(
            db,
            tenant_id,
            event_time_from=event_time_from,
            days=days,
            limit=limit,
            master_profile_id=master_profile_id,
            domain=domain,
            channel=channel,
            event_category=event_category,
            event_name=event_name,
        )
    except EventQueryError as exc:
        raise HTTPException(status_code=503, detail="Event lake query failed") from exc
    return [EventQueryRead.model_validate(row) for row in rows]


all_events_routers = [router]
