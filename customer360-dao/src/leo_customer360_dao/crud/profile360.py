"""Aggregate Customer 360 profile dashboard metrics from S3 events and PostgreSQL.

Behavioral events are read from the tenant-scoped S3 event lake. PostgreSQL is
used only for resolved profiles, CRM transactions, and customer contacts.
"""

from collections import Counter
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.models.identity import CdpMasterProfile
from leo_customer360_dao.models.system import SysDataSource
from leo_customer360_dao.repositories.event_query_repository import EventDataSourceError
from leo_customer360_dao.repositories.event_query_repository import EventQueryRepository
from leo_customer360_dao.repositories.master_profile_event_repository import (
    MasterProfileEventStore,
)

_SCHEMA = settings.db_schema

# Friendly display labels for canonical S3 event categories.
EVENT_CATEGORY_LABELS = {
    "GENERAL": "General Activity",
    "EDUCATION": "Education",
    "COMMERCE": "Shopping",
    "FEEDBACK": "Feedback",
    "FINANCE": "Finance",
    "STOCK_TRADING": "Investments",
    "TRAVEL": "Travel",
    "REAL_ESTATE": "Real Estate",
    "SERVICE_INDUSTRY": "Service Industry",
}

# Friendly display titles for canonical S3 event names.
EVENT_NAME_TITLES = {
    "user-login": "Logged in",
    "page-view": "Viewed a page",
    "search": "Searched",
    "add-to-cart": "Added item to cart",
    "purchase": "Made a purchase",
    "pay-bill": "Paid a bill",
    "transfer-money": "Transferred funds",
    "view-portfolio": "Viewed investment portfolio",
    "submit-nps-form": "Submitted NPS feedback",
    "submit-csat-form": "Submitted CSAT feedback",
    "booking": "Made a booking",
    "search-flight": "Searched for flights",
    "view-property": "Viewed a property listing",
    "request-property-tour": "Requested a property tour",
}


def _window_start(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _profile_event_rows(
    db: Session,
    master_profile_id: uuid.UUID,
    *,
    days: int,
    limit: int | None,
    data_source_id: Optional[uuid.UUID] = None,
) -> list[dict]:
    tenant_id = db.execute(
        select(CdpMasterProfile.tenant_id).where(
            CdpMasterProfile.master_profile_id == master_profile_id
        )
    ).scalar_one_or_none()
    if tenant_id is None:
        return []
    return EventQueryRepository(settings).query(
        db,
        tenant_id,
        days=days,
        limit=limit,
        master_profile_id=master_profile_id,
        data_source_id=data_source_id,
    )


def _as_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_timeline_text(value: object, max_length: int = 512) -> Optional[str]:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value[:max_length] if text_value else None


def _safe_timeline_event_data(value: object) -> Optional[dict[str, object]]:
    if not isinstance(value, dict):
        return None
    safe: dict[str, object] = {}
    for key, item in list(value.items())[:20]:
        if not isinstance(key, str) or not key.strip():
            continue
        if item is None or isinstance(item, (bool, int, float)):
            safe[key[:64]] = item
        elif isinstance(item, str):
            safe[key[:64]] = item[:256]
    return safe or None


def get_engagement_summary(
    db: Session, master_profile_id: uuid.UUID, days: int = 90
) -> dict:
    since = _window_start(days)
    events = _profile_event_rows(db, master_profile_id, days=days, limit=None)
    logins = sum(
        1
        for event in events
        if event.get("event_name") == "user-login"
        and (_as_datetime(event.get("event_time")) or datetime.min.replace(tzinfo=timezone.utc)) >= since
    )

    txn_row = db.execute(
        text(
            f"SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total, "
            f"AVG(amount) AS avg_amount, MAX(currency) AS currency "
            f"FROM {_SCHEMA}.crm_transactions "
            f"WHERE master_profile_id = :mpid AND transaction_time >= :since"
        ),
        {"mpid": str(master_profile_id), "since": since},
    ).mappings().first()

    event_times = [
        event_time
        for event in events
        if (event_time := _as_datetime(event.get("event_time"))) is not None
    ]
    transaction_time = db.execute(
        text(
            f"SELECT MAX(transaction_time) FROM {_SCHEMA}.crm_transactions "
            "WHERE master_profile_id = :mpid"
        ),
        {"mpid": str(master_profile_id)},
    ).scalar_one()
    contact_time = db.execute(
        text(
            f"SELECT MAX(contact_date) FROM {_SCHEMA}.crm_customer_contacts "
            "WHERE master_profile_id = :mpid"
        ),
        {"mpid": str(master_profile_id)},
    ).scalar_one()
    interaction_times = [
        *event_times,
        *[
            normalized
            for value in (transaction_time, contact_time)
            if (normalized := _as_datetime(value)) is not None
        ],
    ]
    last_interaction = max(interaction_times, default=None)

    return {
        "period_days": days,
        "total_logins": logins,
        "total_transactions": txn_row["cnt"],
        "total_spent": txn_row["total"],
        "avg_transaction_amount": txn_row["avg_amount"],
        "currency": txn_row["currency"] or "USD",
        "last_interaction_at": last_interaction,
    }


def get_channel_activity(db: Session, master_profile_id: uuid.UUID, days: int = 90) -> dict:
    since = _window_start(days)
    events = _profile_event_rows(db, master_profile_id, days=days, limit=None)
    recent_events = [
        event
        for event in events
        if (_as_datetime(event.get("event_time")) or datetime.min.replace(tzinfo=timezone.utc)) >= since
    ]
    app_sessions = sum(1 for event in recent_events if event.get("channel") == "mobile_app")
    web_sessions = sum(1 for event in recent_events if event.get("channel") == "web")

    customer_service_contacts = db.execute(
        text(
            f"SELECT COUNT(*) FROM {_SCHEMA}.crm_customer_contacts "
            f"WHERE master_profile_id = :mpid AND contact_date >= :since"
        ),
        {"mpid": str(master_profile_id), "since": since},
    ).scalar_one()

    transactions = db.execute(
        text(
            f"SELECT COUNT(*) FROM {_SCHEMA}.crm_transactions "
            f"WHERE master_profile_id = :mpid AND transaction_time >= :since"
        ),
        {"mpid": str(master_profile_id), "since": since},
    ).scalar_one()

    return {
        "period_days": days,
        "app_sessions": app_sessions,
        "web_sessions": web_sessions,
        "customer_service_contacts": customer_service_contacts,
        "transactions": transactions,
    }


def get_top_interests(db: Session, master_profile_id: uuid.UUID, limit: int = 5) -> list[dict]:
    counts = Counter(
        event.get("event_category", "GENERAL")
        for event in _profile_event_rows(
            db,
            master_profile_id,
            days=settings.event_query_max_days,
            limit=None,
        )
    )
    rows = counts.most_common(limit)
    if not rows:
        return []
    max_count = rows[0][1]
    return [
        {
            "category": category,
            "label": EVENT_CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
            "count": cnt,
            "percentage": round((cnt / max_count) * 100, 1) if max_count else 0.0,
        }
        for category, cnt in rows
    ]


def get_timeline(
    db: Session,
    master_profile_id: uuid.UUID,
    limit: int = 20,
    data_source_id: Optional[uuid.UUID] = None,
    from_event_time: Optional[datetime] = None,
    to_event_time: Optional[datetime] = None,
) -> list[dict]:
    mpid = str(master_profile_id)

    tenant_id = db.execute(
        select(CdpMasterProfile.tenant_id).where(
            CdpMasterProfile.master_profile_id == master_profile_id
        )
    ).scalar_one_or_none()
    if tenant_id is None:
        return []
    if data_source_id is not None:
        source_exists = db.execute(
            select(SysDataSource.data_source_id).where(
                SysDataSource.tenant_id == tenant_id,
                SysDataSource.data_source_id == data_source_id,
                SysDataSource.status == 1,
            )
        ).scalar_one_or_none()
        if source_exists is None:
            raise EventDataSourceError(
                "Data source is invalid, inactive, or not owned by the tenant"
            )

    events = MasterProfileEventStore(settings).query(
        tenant_id,
        master_profile_id,
        from_event_time=from_event_time,
        to_event_time=to_event_time,
        data_source_id=data_source_id,
        limit=limit,
    )
    timeline_start, timeline_end = _timeline_bounds(from_event_time, to_event_time)

    transactions = []
    contacts = []
    if data_source_id is None:
        transactions = db.execute(
            text(
                f"""
                SELECT transaction_type, entity_name, amount, currency, channel, transaction_time
                FROM {_SCHEMA}.crm_transactions
                                WHERE master_profile_id = :mpid
                                    AND transaction_time >= :timeline_start
                                    AND transaction_time <= :timeline_end
                ORDER BY transaction_time DESC LIMIT :limit
                """
            ),
            {
                "mpid": mpid,
                "limit": limit,
                "timeline_start": timeline_start,
                "timeline_end": timeline_end,
            },
        ).mappings().all()

        contacts = db.execute(
            text(
                f"""
                SELECT contact_type, contact_channel, contact_content, contact_date
                FROM {_SCHEMA}.crm_customer_contacts
                                WHERE master_profile_id = :mpid
                                    AND contact_date >= :timeline_start
                                    AND contact_date <= :timeline_end
                ORDER BY contact_date DESC LIMIT :limit
                """
            ),
            {
                "mpid": mpid,
                "limit": limit,
                "timeline_start": timeline_start,
                "timeline_end": timeline_end,
            },
        ).mappings().all()

    entries: list[dict] = []
    for row in events:
        payload = row.get("payload") or row.get("event") or {}
        if not isinstance(payload, dict):
            payload = {}
        event_name = _bounded_timeline_text(
            row.get("event_name") or payload.get("event_name") or "event", 128
        ) or "event"
        event_category = _bounded_timeline_text(
            row.get("event_category") or payload.get("event_category") or "GENERAL", 128
        ) or "GENERAL"
        title = EVENT_NAME_TITLES.get(event_name, event_name.replace("-", " ").title())
        entries.append(
            {
                "kind": "event",
                "title": title,
                "subtitle": EVENT_CATEGORY_LABELS.get(event_category, event_category),
                "channel": row.get("channel") or payload.get("channel"),
                "amount": row.get("event_value") or payload.get("event_value"),
                "currency": row.get("currency") or payload.get("currency"),
                "occurred_at": _as_datetime(row.get("event_time")),
                "event_id": row.get("event_id"),
                "data_source_id": row.get("data_source_id"),
                "raw_profile_id": row.get("raw_profile_id") or payload.get("raw_profile_id"),
                "source_system": _bounded_timeline_text(
                    row.get("source_system") or payload.get("source_system"), 128
                ),
                "domain": _bounded_timeline_text(row.get("domain") or payload.get("domain"), 128),
                "device_type": _bounded_timeline_text(
                    row.get("device_type") or payload.get("device_type"), 128
                ),
                "event_category": event_category,
                "event_name": _bounded_timeline_text(event_name, 128),
                "page_url": _bounded_timeline_text(payload.get("page_url"), 2048),
                "page_title": _bounded_timeline_text(payload.get("page_title"), 512),
                "referrer_url": _bounded_timeline_text(payload.get("referrer_url"), 2048),
                "event_data": _safe_timeline_event_data(payload.get("event_data")),
            }
        )
    for row in transactions:
        entries.append(
            {
                "kind": "transaction",
                "title": row["entity_name"] or (row["transaction_type"] or "Transaction").replace("_", " ").title(),
                "subtitle": (row["transaction_type"] or "").replace("_", " ").title(),
                "channel": row["channel"],
                "amount": row["amount"],
                "currency": row["currency"],
                "occurred_at": row["transaction_time"],
                "event_name": row["transaction_type"],
            }
        )
    for row in contacts:
        entries.append(
            {
                "kind": "contact",
                "title": (row["contact_type"] or "Contact").replace("_", " ").title(),
                "subtitle": row["contact_content"],
                "channel": row["contact_channel"],
                "amount": None,
                "currency": None,
                "occurred_at": row["contact_date"],
            }
        )

    def _sort_key(entry: dict) -> datetime:
        occurred_at = _as_datetime(entry["occurred_at"])
        if occurred_at is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if occurred_at.tzinfo is None:
            return occurred_at.replace(tzinfo=timezone.utc)
        return occurred_at

    entries.sort(key=_sort_key, reverse=True)
    return entries[:limit]


def _timeline_bounds(
    from_event_time: Optional[datetime],
    to_event_time: Optional[datetime],
) -> tuple[datetime, datetime]:
    """Return an order-independent range with a seven-day default."""
    now = datetime.now(timezone.utc)
    default_start = now - timedelta(days=7)
    values = [
        _as_datetime(value)
        for value in (from_event_time, to_event_time)
        if value is not None
    ]
    if not values:
        return default_start, now
    if len(values) == 1:
        return min(values[0], now), max(values[0], now)
    return min(values), max(values)
