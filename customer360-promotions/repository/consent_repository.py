"""
Consent repository.

Responsible for leo_ads.consent_record (a visitor's current choice) and
leo_ads.consent_history (append-only audit of every change).

Persistence only: whether a visitor may be personalised is decided by
personalization/consent.py from the status read here and the tenant's mode.

The visitor is identified only by the keyed hash from personalization/identity.py,
never by the raw visitor id.

Multi-tenancy:
    leo_ads has no row-level security. Every method takes a required
    ``tenant_id`` and filters by it.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

STATUSES = ("granted", "denied")
DENIED = "denied"
MAX_SOURCE_LENGTH = 40


class ConsentRepository:
    """Read and record visitor consent choices."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def has_open_erasure(self, tenant_id: int, user_key: bytes) -> bool:
        """True while an erasure request for this visitor has not completed.

        From the moment it is requested the visitor must behave as opted out:
        nothing new may be attached to their key while the job is still deleting.
        """
        with self._session_factory() as session:
            return bool(
                session.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM leo_ads.erasure_request
                            WHERE tenant_id = :tenant_id AND user_key_hash = :user_key
                              AND status <> 'completed'
                        )
                        """
                    ),
                    {"tenant_id": tenant_id, "user_key": bytes(user_key)},
                ).scalar_one()
            )

    def get_status(self, tenant_id: int, user_key: bytes) -> Optional[str]:
        """The visitor's current choice, or None when they have not recorded one.

        A visitor with an open erasure request reads as ``denied``.
        """
        if self.has_open_erasure(tenant_id, user_key):
            return DENIED
        with self._session_factory() as session:
            return session.execute(
                text(
                    """
                    SELECT status
                    FROM leo_ads.consent_record
                    WHERE tenant_id = :tenant_id AND user_key_hash = :user_key
                    """
                ),
                {"tenant_id": tenant_id, "user_key": bytes(user_key)},
            ).scalar_one_or_none()

    def record(
        self, tenant_id: int, user_key: bytes, status: str, source: str = "widget"
    ) -> bool:
        """Record a choice. Returns True if it changed, False if it was already set.

        A change updates the current record and appends a history row in one
        transaction. Repeating the current choice is a no-op, so the call is
        idempotent and does not clutter the audit trail.
        """
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if not source or len(source) > MAX_SOURCE_LENGTH:
            raise ValueError(f"source must be 1 to {MAX_SOURCE_LENGTH} characters")

        params = {
            "tenant_id": tenant_id,
            "user_key": bytes(user_key),
            "status": status,
            "source": source,
        }
        with self._session_factory() as session:
            with session.begin():
                changed = session.execute(
                    text(
                        """
                        INSERT INTO leo_ads.consent_record
                            (tenant_id, user_key_hash, status, source, changed_at)
                        VALUES (:tenant_id, :user_key, :status, :source, now())
                        ON CONFLICT (tenant_id, user_key_hash) DO UPDATE
                           SET status = EXCLUDED.status,
                               source = EXCLUDED.source,
                               changed_at = now()
                         WHERE leo_ads.consent_record.status IS DISTINCT FROM EXCLUDED.status
                        RETURNING 1
                        """
                    ),
                    params,
                ).first()
                if changed is None:
                    return False
                session.execute(
                    text(
                        """
                        INSERT INTO leo_ads.consent_history
                            (tenant_id, user_key_hash, status, source, changed_at)
                        VALUES (:tenant_id, :user_key, :status, :source, now())
                        """
                    ),
                    params,
                )
                return True

    def delete_for_key(self, tenant_id: int, user_key: bytes) -> dict[str, int]:
        """Remove a visitor's consent record and history (erasure). Returns row counts."""
        params = {"tenant_id": tenant_id, "user_key": bytes(user_key)}
        with self._session_factory() as session:
            with session.begin():
                history = session.execute(
                    text(
                        "DELETE FROM leo_ads.consent_history "
                        "WHERE tenant_id = :tenant_id AND user_key_hash = :user_key"
                    ),
                    params,
                ).rowcount
                record = session.execute(
                    text(
                        "DELETE FROM leo_ads.consent_record "
                        "WHERE tenant_id = :tenant_id AND user_key_hash = :user_key"
                    ),
                    params,
                ).rowcount
        return {"consent_record": record, "consent_history": history}
