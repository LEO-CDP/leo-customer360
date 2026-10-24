"""
Personalisation settings repository.

Responsible for leo_ads.personalization_setting.

Persistence only: which row wins for a request (placement, then site, then
tenant) is decided by personalization/settings.py. The data plane only reads
settings; the admin API in customer360-api writes them. ``upsert`` exists here
for seeding tooling and tests, and enforces the same rules as the admin API.

Multi-tenancy:
    leo_ads has no row-level security. Every method takes a required
    ``tenant_id`` and filters by it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from model.personalization import PersonalizationSetting

SCOPE_TYPES = ("tenant", "site", "placement")
CONSENT_MODES = ("opt_in", "opt_out")


class SettingValidationError(ValueError):
    """A settings change breaks one of the rules (mapped to HTTP 422 by the admin API)."""


class SettingRepository:
    """Read and upsert personalisation settings."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_for_tenant(self, tenant_id: int) -> list[dict[str, Any]]:
        """All settings rows for one tenant, in a stable order."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(PersonalizationSetting)
                    .where(PersonalizationSetting.tenant_id == tenant_id)
                    .order_by(
                        PersonalizationSetting.scope_type,
                        PersonalizationSetting.scope_ref,
                    )
                )
                .scalars()
                .all()
            )
            return [self._to_dict(row) for row in rows]

    def get(
        self, tenant_id: int, scope_type: str, scope_ref: str = ""
    ) -> Optional[dict[str, Any]]:
        with self._session_factory() as session:
            row = session.execute(
                select(PersonalizationSetting).where(
                    PersonalizationSetting.tenant_id == tenant_id,
                    PersonalizationSetting.scope_type == scope_type,
                    PersonalizationSetting.scope_ref == scope_ref,
                )
            ).scalar_one_or_none()
            return None if row is None else self._to_dict(row)

    def _has_tenant_row(self, tenant_id: int) -> bool:
        with self._session_factory() as session:
            return (
                session.execute(
                    select(PersonalizationSetting.setting_id).where(
                        PersonalizationSetting.tenant_id == tenant_id,
                        PersonalizationSetting.scope_type == "tenant",
                        PersonalizationSetting.scope_ref == "",
                    )
                ).first()
                is not None
            )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(
        self,
        tenant_id: int,
        *,
        scope_type: str,
        scope_ref: str = "",
        enabled: Optional[bool] = None,
        holdout_pct: Optional[float] = None,
        consent_mode: Optional[str] = None,
        new_measurement_period: bool = False,
        updated_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create or update one scope's row; only the fields provided change.

        Rules (also enforced by database constraints): hold-out between 0 and
        100; consent mode and a new measurement period only on the tenant scope;
        a tenant row must exist before a site or placement row.
        """
        if scope_type not in SCOPE_TYPES:
            raise SettingValidationError(f"scope_type must be one of {SCOPE_TYPES}")
        if scope_type == "tenant" and scope_ref != "":
            raise SettingValidationError("the tenant scope has an empty scope_ref")
        if scope_type != "tenant" and not scope_ref:
            raise SettingValidationError(f"the {scope_type} scope needs a scope_ref")
        if holdout_pct is not None and not (0 <= float(holdout_pct) <= 100):
            raise SettingValidationError("holdout_pct must be between 0 and 100")
        if consent_mode is not None:
            if consent_mode not in CONSENT_MODES:
                raise SettingValidationError(f"consent_mode must be one of {CONSENT_MODES}")
            if scope_type != "tenant":
                raise SettingValidationError("consent_mode can only be set on the tenant scope")
        if new_measurement_period and scope_type != "tenant":
            raise SettingValidationError(
                "a new measurement period can only be started on the tenant scope"
            )

        # Checked in its own read-only session, before any write transaction
        # starts, so a rule violation never rolls anything back.
        if scope_type != "tenant" and not self._has_tenant_row(tenant_id):
            raise SettingValidationError(
                "create the tenant-scope settings before a site or placement"
            )

        with self._session_factory() as session:
            with session.begin():
                row = session.execute(
                    select(PersonalizationSetting)
                    .where(
                        PersonalizationSetting.tenant_id == tenant_id,
                        PersonalizationSetting.scope_type == scope_type,
                        PersonalizationSetting.scope_ref == scope_ref,
                    )
                    .with_for_update()
                ).scalar_one_or_none()

                if row is None:
                    row = PersonalizationSetting(
                        tenant_id=tenant_id,
                        scope_type=scope_type,
                        scope_ref=scope_ref,
                        enabled=False,
                        holdout_pct=Decimal("10.00"),
                        holdout_salt="default",
                        consent_mode="opt_in",
                    )
                    session.add(row)

                if enabled is not None:
                    row.enabled = enabled
                if holdout_pct is not None:
                    row.holdout_pct = Decimal(str(holdout_pct))
                if consent_mode is not None:
                    row.consent_mode = consent_mode
                if new_measurement_period:
                    row.holdout_salt = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                if updated_by is not None:
                    row.updated_by = updated_by
                row.updated_at = func.now()

                session.flush()
                session.refresh(row)
                return self._to_dict(row)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dict(row: PersonalizationSetting) -> dict[str, Any]:
        """JSON-safe dict (this is what the settings cache stores)."""
        return {
            "setting_id": row.setting_id,
            "tenant_id": row.tenant_id,
            "scope_type": row.scope_type,
            "scope_ref": row.scope_ref,
            "enabled": bool(row.enabled),
            "holdout_pct": float(row.holdout_pct),
            "holdout_salt": row.holdout_salt,
            "consent_mode": row.consent_mode,
            "updated_by": row.updated_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
