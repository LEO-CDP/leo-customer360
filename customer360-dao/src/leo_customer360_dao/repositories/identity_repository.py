
"""Customer Identity Resolution (CIR) repository: master profiles, raw profiles,
profile links, domain profiles, and persona-related entities.

Encapsulates core identity resolution data access and query operations:
- Master profile queries (list, count, get)
- Raw profile staging operations
- Profile linking and merge history
- Domain profile and persona queries

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py). Row-Level Security is enforced at the DB layer
through tenant_id filtering.
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.crud import identity as identity_crud
from leo_customer360_dao.models.identity import (
    CdpMasterProfile,
    CdpRawProfileStage,
    CdpProfileLink,
    CdpDomainProfile,
    CdpCustomerPersona,
)
from leo_customer360_dao.schemas.identity import MasterProfileListResponse


_RAW_PROFILE_COLUMNS = (
    "raw_profile_id",
    "tenant_id",
    "data_source_id",
    "data_source_analytics",
    "domain",
    "source_system",
    "channel",
    "external_customer_id",
    "full_name",
    "first_name",
    "last_name",
    "email",
    "phone_number",
    "national_id",
    "date_of_birth",
    "address_line1",
    "address_line2",
    "city",
    "state_province",
    "postal_code",
    "country",
    "company_name",
    "device_id",
    "advertising_id",
    "platform",
    "app_version",
    "push_token",
    "cookie_id",
    "ga_client_id",
    "session_id",
    "media_source",
    "campaign",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "event_name",
    "event_time",
    "event_payload",
)

_RAW_PROFILE_UPDATE_COLUMNS = tuple(
    column for column in _RAW_PROFILE_COLUMNS if column not in {"raw_profile_id", "tenant_id"}
    ) + ("status_code", "processed_at")


class IdentityRepository:
    """Identity resolution repository: master profiles, raw profiles, linking."""

    def __init__(self, session: Session):
        self.session = session
        self._master_crud = CRUDBase(CdpMasterProfile)
        self._raw_crud = CRUDBase(CdpRawProfileStage)
        self._link_crud = CRUDBase(CdpProfileLink)
        self._domain_crud = CRUDBase(CdpDomainProfile)
        self._persona_crud = CRUDBase(CdpCustomerPersona)

    def list_master_profiles_page(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        data_source_id: Optional[uuid.UUID] = None,
        domain: Optional[str] = None,
        lifecycle_stage: Optional[str] = None,
        domain_attribute_key: Optional[str] = None,
        domain_attribute_value: Optional[str] = None,
        membership_tier: Optional[str] = None,
        clv_segment: Optional[str] = None,
        churn_risk_tier: Optional[str] = None,
        linked_raw_profile_count_min: Optional[int] = None,
        q: Optional[str] = None,
        days: int = 90,
        page: int = 1,
        page_size: int = 20,
    ) -> MasterProfileListResponse:
        """Paginated list of master profiles with advanced filtering."""
        return identity_crud.list_master_profiles_page(
            self.session,
            tenant_id=tenant_id,
            data_source_id=data_source_id,
            domain=domain,
            lifecycle_stage=lifecycle_stage,
            domain_attribute_key=domain_attribute_key,
            domain_attribute_value=domain_attribute_value,
            membership_tier=membership_tier,
            clv_segment=clv_segment,
            churn_risk_tier=churn_risk_tier,
            linked_raw_profile_count_min=linked_raw_profile_count_min,
            q=q,
            days=days,
            page=page,
            page_size=page_size,
        )

    def count_master_profiles(
        self, tenant_id: Optional[uuid.UUID] = None, domain: Optional[str] = None
    ) -> int:
        """Count master profiles matching filters."""
        return self._master_crud.count(self.session, tenant_id=tenant_id, domain=domain)

    def get_master_profile(self, master_profile_id: uuid.UUID) -> Optional[CdpMasterProfile]:
        """Get master profile by ID."""
        return self._master_crud.get(self.session, master_profile_id)

    def count_raw_profiles(
        self, tenant_id: Optional[uuid.UUID] = None, days: int = 90
    ) -> int:
        """Count raw profiles created within the last N days."""
        from leo_customer360_dao.repositories.reporting_respository import ReportingRepository

        return ReportingRepository(self.session).count_raw_profiles(tenant_id=tenant_id, days=days)

    def get_raw_profile(self, raw_profile_id: uuid.UUID) -> Optional[CdpRawProfileStage]:
        """Get raw profile by ID."""
        return self._raw_crud.get(self.session, raw_profile_id)

    def upsert_raw_profile(self, raw_profile: dict[str, Any]) -> CdpRawProfileStage:
        """Persist one tenant-scoped raw profile observation idempotently.

        The caller owns extraction and deterministic ID generation. This method
        owns the database shape, tenant guard, and replay-safe upsert behavior.
        """
        tenant_id = self._required_uuid(raw_profile.get("tenant_id"), "tenant_id")
        session_tenant_id = self.session.info.get("tenant_id")
        if not session_tenant_id:
            raise ValueError("Tenant context is required for raw profile access")
        if tenant_id != self._required_uuid(session_tenant_id, "session tenant_id"):
            raise ValueError("Raw profile tenant does not match the session tenant")

        values = {
            column: raw_profile.get(column)
            for column in _RAW_PROFILE_COLUMNS
        }
        values["raw_profile_id"] = self._required_uuid(
            raw_profile.get("raw_profile_id"), "raw_profile_id"
        )
        values["tenant_id"] = tenant_id
        values["date_of_birth"] = self._as_date(values.get("date_of_birth"))
        values["event_time"] = self._as_utc_datetime(values.get("event_time"))

        statement = pg_insert(CdpRawProfileStage).values(**values)
        update_values = {
            column: getattr(statement.excluded, column)
            for column in _RAW_PROFILE_UPDATE_COLUMNS
        }
        statement = (
            statement.on_conflict_do_update(
                index_elements=[CdpRawProfileStage.raw_profile_id],
                set_=update_values,
            )
            .returning(CdpRawProfileStage)
        )
        return self.session.execute(statement).scalar_one()

    @staticmethod
    def _required_uuid(value: Any, field_name: str) -> uuid.UUID:
        if value is None:
            raise ValueError(f"{field_name} is required")
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{field_name} must be a UUID") from exc

    @staticmethod
    def _as_utc_datetime(value: Any) -> Optional[datetime]:
        if value is None or isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) else None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("event_time must be a valid timestamp") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        raise ValueError("event_time must be a datetime or ISO timestamp")

    @staticmethod
    def _as_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return date.fromisoformat(value.strip())
            except ValueError as exc:
                raise ValueError("date_of_birth must be a valid ISO date") from exc
        raise ValueError("date_of_birth must be a date or ISO date")

    def list_profile_links(
        self, master_profile_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> list[CdpProfileLink]:
        """List profile links for a master profile."""
        return identity_crud.list_profile_links(
            self.session, master_profile_id, skip=skip, limit=limit
        )

    def get_domain_profile(self, domain_profile_id: uuid.UUID) -> Optional[CdpDomainProfile]:
        """Get domain profile by ID."""
        return self._domain_crud.get(self.session, domain_profile_id)

    def get_customer_persona(self, persona_id: uuid.UUID) -> Optional[CdpCustomerPersona]:
        """Get customer persona by ID."""
        return self._persona_crud.get(self.session, persona_id)