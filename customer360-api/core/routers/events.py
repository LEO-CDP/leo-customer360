"""API for the high-volume behavioral/transactional event stream
(``cdp_raw_events``). Powers the Customer 360 profile dashboard's timeline /
cross-channel activity widgets, and supports direct API ingestion for dev
workflows (including hashed-email/hashed-phone identity hints).
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.database import get_db
from core.models.events import CdpRawEvent
from core.models.identity import CdpRawProfileStage
from core.schemas.events import EventCreate, EventRead

router = APIRouter(prefix="/events", tags=["Behavioral Events"])


def _find_existing_by_dedup_key(db: Session, payload: EventCreate) -> Optional[CdpRawEvent]:
    if not payload.event_dedup_key:
        return None
    stmt = select(CdpRawEvent).where(
        CdpRawEvent.tenant_id == payload.tenant_id,
        CdpRawEvent.source_system == payload.source_system,
        CdpRawEvent.event_dedup_key == payload.event_dedup_key,
    )
    return db.execute(stmt).scalars().first()


def _build_event(payload: EventCreate, resolved_raw_profile_id: uuid.UUID) -> CdpRawEvent:
    return CdpRawEvent(
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        domain=payload.domain,
        master_profile_id=payload.master_profile_id,
        raw_profile_id=resolved_raw_profile_id,
        external_customer_id=payload.external_customer_id,
        device_id=payload.device_id,
        advertising_id=payload.advertising_id,
        cookie_id=payload.cookie_id,
        session_id=payload.session_id,
        source_system=payload.source_system,
        event_dedup_key=payload.event_dedup_key,
        channel=payload.channel,
        platform=payload.platform,
        ip_address=payload.ip_address,
        user_agent=payload.user_agent,
        event_category=payload.event_category,
        event_name=payload.event_name,
        is_conversion=payload.is_conversion,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        event_value=payload.event_value,
        currency=payload.currency,
        transaction_id=payload.transaction_id,
        transaction_status=payload.transaction_status,
        location_code=payload.location_code,
        location_name=payload.location_name,
        event_time=payload.event_time or datetime.now(timezone.utc),
        event_payload=payload.event_payload,
    )


def _ingest_one_event(db: Session, payload: EventCreate) -> tuple[CdpRawEvent, bool]:
    existing = _find_existing_by_dedup_key(db, payload)
    if existing is not None:
        return existing, False

    resolved_raw_profile_id = _resolve_raw_profile_id(db, payload)
    event = _build_event(payload, resolved_raw_profile_id)
    db.add(event)
    db.flush()
    return event, True


def _resolve_raw_profile_id(db: Session, payload: EventCreate) -> uuid.UUID:
    if payload.raw_profile_id is not None:
        stmt = select(CdpRawProfileStage).where(
            CdpRawProfileStage.raw_profile_id == payload.raw_profile_id,
            CdpRawProfileStage.tenant_id == payload.tenant_id,
            CdpRawProfileStage.domain == payload.domain,
        )
        obj = db.execute(stmt).scalars().first()
        if obj is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "raw_profile_id was provided but no matching cdp_raw_profiles_stage "
                    "row exists for the same tenant/domain."
                ),
            )
        return obj.raw_profile_id

    identity_predicates = []
    if payload.email:
        identity_predicates.append(CdpRawProfileStage.email == payload.email)
    if payload.phone_number:
        identity_predicates.append(CdpRawProfileStage.phone_number == payload.phone_number)
    if payload.external_customer_id:
        identity_predicates.append(CdpRawProfileStage.external_customer_id == payload.external_customer_id)
    if payload.device_id:
        identity_predicates.append(CdpRawProfileStage.device_id == payload.device_id)
    if payload.advertising_id:
        identity_predicates.append(CdpRawProfileStage.advertising_id == payload.advertising_id)
    if payload.cookie_id:
        identity_predicates.append(CdpRawProfileStage.cookie_id == payload.cookie_id)
    if payload.session_id:
        identity_predicates.append(CdpRawProfileStage.session_id == payload.session_id)

    existing_stmt = (
        select(CdpRawProfileStage)
        .where(CdpRawProfileStage.tenant_id == payload.tenant_id, CdpRawProfileStage.domain == payload.domain)
        .where(or_(*identity_predicates))
        .order_by(CdpRawProfileStage.created_at.desc())
        .limit(1)
    )
    existing = db.execute(existing_stmt).scalars().first()
    if existing is not None:
        return existing.raw_profile_id

    raw_profile = CdpRawProfileStage(
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        domain=payload.domain,
        source_system=payload.source_system,
        channel=payload.channel,
        external_customer_id=payload.external_customer_id,
        email=payload.email,
        phone_number=payload.phone_number,
        device_id=payload.device_id,
        advertising_id=payload.advertising_id,
        cookie_id=payload.cookie_id,
        session_id=payload.session_id,
        platform=payload.platform,
        ip_address=payload.ip_address,
        user_agent=payload.user_agent,
        event_name=payload.event_name,
        event_time=payload.event_time,
        event_payload=payload.event_payload,
    )
    db.add(raw_profile)
    db.flush()
    return raw_profile.raw_profile_id


@router.get("/", response_model=list[EventRead])
@cache_response("events/list", ttl=settings.cache_ttl_seconds)
def list_events(
    tenant_id: Optional[uuid.UUID] = None,
    master_profile_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None, pattern="^(retail|banking|real_estate|travel)$"),
    channel: Optional[str] = None,
    event_category: Optional[str] = None,
    event_name: Optional[str] = None,
    event_time_from: Optional[datetime] = None,
    event_time_to: Optional[datetime] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    stmt = select(CdpRawEvent)
    if tenant_id is not None:
        stmt = stmt.where(CdpRawEvent.tenant_id == tenant_id)
    if master_profile_id is not None:
        stmt = stmt.where(CdpRawEvent.master_profile_id == master_profile_id)
    if domain is not None:
        stmt = stmt.where(CdpRawEvent.domain == domain)
    if channel is not None:
        stmt = stmt.where(CdpRawEvent.channel == channel)
    if event_category is not None:
        stmt = stmt.where(CdpRawEvent.event_category == event_category)
    if event_name is not None:
        stmt = stmt.where(CdpRawEvent.event_name == event_name)
    if event_time_from is not None:
        stmt = stmt.where(CdpRawEvent.event_time >= event_time_from)
    if event_time_to is not None:
        stmt = stmt.where(CdpRawEvent.event_time <= event_time_to)
    stmt = stmt.order_by(CdpRawEvent.event_time.desc()).offset(skip).limit(limit)
    return db.execute(stmt).scalars().all()


@router.post("/bulk", response_model=list[EventRead], status_code=201)
def create_events_bulk(payloads: list[EventCreate], db: Session = Depends(get_db)):
    if not payloads:
        raise HTTPException(status_code=400, detail="payloads must contain at least one event")

    events: list[CdpRawEvent] = []
    created_events: list[CdpRawEvent] = []

    try:
        for payload in payloads:
            event, created = _ingest_one_event(db, payload)
            events.append(event)
            if created:
                created_events.append(event)

        if created_events:
            db.commit()
            for event in created_events:
                db.refresh(event)
            invalidate_prefix("events")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return events


@router.get("/{event_id}", response_model=EventRead)
@cache_response("events/item", ttl=settings.cache_ttl_seconds)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)):
    stmt = select(CdpRawEvent).where(CdpRawEvent.event_id == event_id)
    obj = db.execute(stmt).scalars().first()
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRawEvent '{event_id}' not found")
    return obj


@router.post("/", response_model=EventRead, status_code=201)
def create_event(payload: EventCreate, response: Response, db: Session = Depends(get_db)):
    try:
        event, created = _ingest_one_event(db, payload)
        if not created:
            response.status_code = 200
            return event

        db.commit()
        db.refresh(event)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    invalidate_prefix("events")
    return event


all_events_routers = [router]
