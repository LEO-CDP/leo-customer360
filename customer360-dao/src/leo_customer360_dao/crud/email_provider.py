"""CRUD for the per-tenant email activation connector.

customer360-api owns the write side of ``crm_connector_config``; the
email_engine (a separate deployable) reads the active row from the DB at send
time -- source of truth, read once per run, no cache.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from leo_customer360_dao.models.crm import ConnectorConfig
from leo_customer360_dao.schemas.crm import EmailProviderConfigUpsert


def get_active_config(db: Session, tenant_id: uuid.UUID) -> ConnectorConfig | None:
    """The tenant's active email connector row (at most one), or None."""
    return db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.tenant_id == tenant_id,
            ConnectorConfig.connector_type == "EMAIL",
            ConnectorConfig.status == "ACTIVE",
            ConnectorConfig.is_active.is_(True),
        )
        .order_by(ConnectorConfig.is_default.desc(), ConnectorConfig.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def upsert_config(db: Session, tenant_id: uuid.UUID, payload: EmailProviderConfigUpsert) -> ConnectorConfig:
    """Create or update the (tenant, name) config row. Activating a row first
    deactivates any other active row for the tenant, so the one-active-per-tenant
    email connector invariant holds."""
    existing = db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.tenant_id == tenant_id,
            ConnectorConfig.connector_type == "EMAIL",
            ConnectorConfig.name == payload.name,
        )
    ).scalar_one_or_none()

    # Merge first so omitted fields retain the existing row's state. In
    # particular, a partial update must not reactivate a disabled connector.
    payload_values = payload.model_dump(exclude_unset=True)
    desired_is_active = payload_values.get(
        "is_active", existing.is_active if existing is not None else True
    )

    if desired_is_active:
        # Deactivate any other currently-active config for this tenant.
        db.execute(
            update(ConnectorConfig)
            .where(
                ConnectorConfig.tenant_id == tenant_id,
                ConnectorConfig.connector_type == "EMAIL",
                ConnectorConfig.is_active.is_(True),
                ConnectorConfig.name != payload.name,
            )
            .values(is_active=False, is_default=False, status="INACTIVE")
        )

    # Merge: only supplied fields are written, so a partial PUT never resets the
    # rest to defaults (new rows still get DB defaults for anything omitted).
    config = dict(existing.config or {}) if existing is not None else {}
    credentials = dict(existing.credentials or {}) if existing is not None else {}
    config_fields = (
        "smtp_host", "smtp_port", "smtp_username", "smtp_use_tls", "from_address", "from_name"
    )
    for field in config_fields:
        if field in payload_values:
            config[field] = payload_values.pop(field)

    # smtp_password is write-only: an empty value never overwrites the stored one.
    if payload_values.get("smtp_password"):
        credentials["password"] = payload_values["smtp_password"]
    payload_values.pop("smtp_password", None)

    values = {
        "name": payload_values.pop("name", existing.name if existing is not None else payload.name),
        "provider": payload_values.pop(
            "provider", existing.provider if existing is not None else "mock"
        ).upper(),
        "connector_type": "EMAIL",
        "direction": "OUTBOUND",
        "config": config,
        "credentials": credentials,
        "is_active": payload_values.pop(
            "is_active", existing.is_active if existing is not None else True
        ),
    }
    values["is_default"] = values["is_active"]
    values["status"] = "ACTIVE" if values["is_active"] else "INACTIVE"
    values.update(payload_values)

    if existing is None:
        row = ConnectorConfig(tenant_id=tenant_id, **values)
        db.add(row)
    else:
        for key, val in values.items():
            setattr(existing, key, val)
        row = existing

    db.commit()
    db.refresh(row)
    return row
