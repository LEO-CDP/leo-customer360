"""CRUD for the per-tenant email dispatch config.

customer360-api owns the write side of ``crm_email_provider_config``; the
email_engine (a separate deployable) reads it at send time. Both cache the
resolved config in Redis under the SAME key (``email_provider_config:{tenant}``),
so every write here DELETES that key -- the next send then re-reads the DB and
re-populates the cache. For that invalidation to reach the sender, this API's
Redis (``core.config.settings.redis_*``) and the email_engine's Redis
(``REDIS_*`` env) must be the same instance.
"""

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.cache import get_redis_client
from core.models.crm import EmailProviderConfig
from core.schemas.crm import EmailProviderConfigUpsert

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "email_provider_config:"


def cache_key(tenant_id: str) -> str:
    return f"{CACHE_KEY_PREFIX}{tenant_id}"


def invalidate_config_cache(tenant_id: str) -> None:
    """Delete the tenant's cached config so the next send re-reads the DB.
    Fail-open: a Redis problem never blocks a config write."""
    client = get_redis_client()
    if client is None:
        return
    try:
        client.delete(cache_key(str(tenant_id)))
    except Exception:  # noqa: BLE001 - cache invalidation is best-effort.
        logger.warning("Failed to invalidate email provider-config cache for tenant %s", tenant_id, exc_info=True)


def get_active_config(db: Session, tenant_id: uuid.UUID) -> EmailProviderConfig | None:
    """The tenant's active provider config row (at most one), or None."""
    return db.execute(
        select(EmailProviderConfig).where(
            EmailProviderConfig.tenant_id == tenant_id,
            EmailProviderConfig.is_active.is_(True),
        )
    ).scalar_one_or_none()


def upsert_config(db: Session, tenant_id: uuid.UUID, payload: EmailProviderConfigUpsert) -> EmailProviderConfig:
    """Create or update the (tenant, name) config row. When the row is active,
    any other active row for the tenant is deactivated first so the
    one-active-per-tenant unique index (uq_crm_email_provider_config_active)
    holds. Invalidates the Redis cache after commit."""
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

    values = payload.model_dump(exclude_unset=False)
    # smtp_password is write-only: only overwrite the stored secret when a new
    # non-empty value was supplied (so a plain toggle/edit never wipes it).
    if not payload.smtp_password and existing is not None:
        values.pop("smtp_password", None)
    values["metadata_"] = values.pop("metadata_", None)

    if existing is None:
        row = EmailProviderConfig(tenant_id=tenant_id, **values)
        db.add(row)
    else:
        for key, val in values.items():
            setattr(existing, key, val)
        row = existing

    db.commit()
    db.refresh(row)
    invalidate_config_cache(str(tenant_id))
    return row
