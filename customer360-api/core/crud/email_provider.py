"""CRUD for the per-tenant email dispatch config.

customer360-api owns the write side of ``crm_email_provider_config``; the
email_engine (a separate deployable) reads the active row from the DB at send
time -- source of truth, read once per run, no cache.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.models.crm import EmailProviderConfig
from core.schemas.crm import EmailProviderConfigUpsert


def get_active_config(db: Session, tenant_id: uuid.UUID) -> EmailProviderConfig | None:
    """The tenant's active provider config row (at most one), or None."""
    return db.execute(
        select(EmailProviderConfig).where(
            EmailProviderConfig.tenant_id == tenant_id,
            EmailProviderConfig.is_active.is_(True),
        )
    ).scalar_one_or_none()


def upsert_config(db: Session, tenant_id: uuid.UUID, payload: EmailProviderConfigUpsert) -> EmailProviderConfig:
    """Create or update the (tenant, name) config row. Activating a row first
    deactivates any other active row for the tenant, so the one-active-per-tenant
    unique index (uq_crm_email_provider_config_active) holds."""
    existing = db.execute(
        select(EmailProviderConfig).where(
            EmailProviderConfig.tenant_id == tenant_id,
            EmailProviderConfig.name == payload.name,
        )
    ).scalar_one_or_none()

    if payload.is_active:
        # Deactivate any other currently-active config for this tenant.
        db.execute(
            update(EmailProviderConfig)
            .where(
                EmailProviderConfig.tenant_id == tenant_id,
                EmailProviderConfig.is_active.is_(True),
                EmailProviderConfig.name != payload.name,
            )
            .values(is_active=False)
        )

    # Merge: only supplied fields are written, so a partial PUT never resets the
    # rest to defaults (new rows still get DB defaults for anything omitted).
    values = payload.model_dump(exclude_unset=True)
    # smtp_password is write-only: an empty value never overwrites the stored one.
    if not values.get("smtp_password") and existing is not None:
        values.pop("smtp_password", None)

    if existing is None:
        row = EmailProviderConfig(tenant_id=tenant_id, **values)
        db.add(row)
    else:
        for key, val in values.items():
            setattr(existing, key, val)
        row = existing

    db.commit()
    db.refresh(row)
    return row
