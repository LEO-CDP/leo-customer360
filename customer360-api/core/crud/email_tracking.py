"""Write side of email engagement capture + suppression.

Called by the PUBLIC tracking/webhook routes, which have NO auth/tenant header
-- the tenant comes from the verified tracking token. So these helpers open
their OWN session and set ``app.tenant_id`` from that decoded tenant (they do
NOT use the request-scoped ``get_db``), then RLS enforces isolation as usual.

Email events normalize into the existing ``cdp_raw_events`` fact table. That
table's ``raw_profile_id`` is NOT NULL, so we resolve one from
``cdp_profile_links`` for the master profile; if a profile has no linked raw
profile we skip the event row (still applying suppression). ``cdp_raw_events``
is partitioned by ``event_time`` (its dedup unique index therefore includes
event_time and can't dedup across arrival times), so duplicate callbacks are
filtered in the application via an ``event_dedup_key`` existence check.
"""

import json
import logging
from typing import Optional

from sqlalchemy import text

from core.database import SessionLocal

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "EmailEngine"


def _session_for_tenant(tenant_id: str):
    """A fresh session with RLS tenant context bound to ``tenant_id``."""
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
    return db


def _resolve_raw_profile_id(db, tenant_id: str, master_profile_id: str) -> Optional[str]:
    # Prefer an ACTIVE link but fall back to any link, so engagement from
    # sync-created leads/contacts (which may have no ACTIVE link) isn't dropped.
    row = db.execute(
        text(
            "SELECT raw_profile_id FROM customer360.cdp_profile_links "
            "WHERE tenant_id = :t AND master_profile_id = :m "
            "ORDER BY (status = 'ACTIVE') DESC, created_at LIMIT 1"
        ),
        {"t": tenant_id, "m": master_profile_id},
    ).first()
    return str(row[0]) if row else None


def _dedup_exists(db, tenant_id: str, dedup_key: str) -> bool:
    row = db.execute(
        text(
            "SELECT 1 FROM customer360.cdp_raw_events "
            "WHERE tenant_id = :t AND source_system = :s AND event_dedup_key = :k LIMIT 1"
        ),
        {"t": tenant_id, "s": SOURCE_SYSTEM, "k": dedup_key},
    ).first()
    return row is not None


def resolve_recipient_email(tenant_id: str, campaign_id: str, master_profile_id: str) -> Optional[str]:
    """The address this campaign actually mailed the recipient at (from the
    dispatch ledger), falling back to the profile's email. Needed because the
    tracking token carries the master_profile_id, not the email, while
    suppression is keyed on the email."""
    db = _session_for_tenant(tenant_id)
    try:
        row = db.execute(
            text(
                "SELECT recipient_email FROM customer360.cdp_campaign_dispatch_logs "
                "WHERE tenant_id = :t AND campaign_id = :c AND master_profile_id = :m "
                "AND recipient_email IS NOT NULL LIMIT 1"
            ),
            {"t": tenant_id, "c": campaign_id, "m": master_profile_id},
        ).first()
        if row and row[0]:
            return row[0]
        row = db.execute(
            text("SELECT email FROM customer360.cdp_master_profiles WHERE master_profile_id = :m"),
            {"m": master_profile_id},
        ).first()
        return row[0] if row and row[0] else None
    finally:
        db.close()


def record_engagement_event(
    tenant_id: str,
    campaign_id: str,
    master_profile_id: str,
    event_name: str,
    *,
    dedup_key: Optional[str] = None,
    event_payload: Optional[dict] = None,
) -> str:
    """Record one email engagement event in cdp_raw_events (deduped). Returns
    'inserted' | 'duplicate' | 'skipped_no_raw_profile'."""
    db = _session_for_tenant(tenant_id)
    try:
        if dedup_key:
            # Serialize same-key callbacks (xact advisory lock, classid 2) so a
            # concurrent duplicate can't slip past the check-then-insert and
            # inflate metrics; the lock releases on commit/rollback.
            db.execute(text("SELECT pg_advisory_xact_lock(2, hashtext(:k))"), {"k": dedup_key})
            if _dedup_exists(db, tenant_id, dedup_key):
                return "duplicate"

        raw_profile_id = _resolve_raw_profile_id(db, tenant_id, master_profile_id)
        if raw_profile_id is None:
            logger.warning("email event %s: no profile link for master %s; event row skipped (metric under-count)",
                           event_name, master_profile_id)
            return "skipped_no_raw_profile"

        payload = dict(event_payload or {})
        payload.setdefault("campaign_id", campaign_id)
        db.execute(
            text(
                """
                INSERT INTO customer360.cdp_raw_events
                    (tenant_id, master_profile_id, raw_profile_id, source_system, event_dedup_key,
                     channel, campaign, event_category, event_name, event_time, event_payload)
                VALUES
                    (:tenant_id, :master_profile_id, :raw_profile_id, :source_system, :dedup_key,
                     'email', :campaign_id, 'GENERAL', :event_name, now(), CAST(:payload AS jsonb))
                """
            ),
            {
                "tenant_id": tenant_id, "master_profile_id": master_profile_id,
                "raw_profile_id": raw_profile_id, "source_system": SOURCE_SYSTEM,
                "dedup_key": dedup_key, "campaign_id": campaign_id, "event_name": event_name,
                "payload": json.dumps(payload),
            },
        )
        db.commit()
        return "inserted"
    finally:
        db.close()


def add_suppression(
    tenant_id: str,
    email: str,
    reason: str,
    *,
    campaign_id: Optional[str] = None,
    source: Optional[str] = None,
) -> bool:
    """Add an email to the compliance suppression list. Idempotent (unique per
    tenant + lower(email)); returns True only if newly inserted."""
    if not email:
        return False
    db = _session_for_tenant(tenant_id)
    try:
        result = db.execute(
            text(
                """
                INSERT INTO customer360.cdp_email_suppression
                    (tenant_id, email, reason, campaign_id, source)
                VALUES (:tenant_id, :email, :reason, :campaign_id, :source)
                ON CONFLICT (tenant_id, lower(email)) DO NOTHING
                """
            ),
            {"tenant_id": tenant_id, "email": email, "reason": reason,
             "campaign_id": campaign_id, "source": source},
        )
        db.commit()
        return result.rowcount > 0
    finally:
        db.close()
