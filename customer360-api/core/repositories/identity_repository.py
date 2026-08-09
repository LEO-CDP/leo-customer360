
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
from typing import Optional

from sqlalchemy.orm import Session

from core.crud.base import CRUDBase
from core.crud import identity as identity_crud
from core.models.identity import (
    CdpMasterProfile,
    CdpRawProfileStage,
    CdpProfileLink,
    CdpDomainProfile,
    CdpCustomerPersona,
)
from core.schemas.identity import MasterProfileListResponse


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
        return identity_crud.count_raw_profiles(self.session, tenant_id, days=days)

    def get_raw_profile(self, raw_profile_id: uuid.UUID) -> Optional[CdpRawProfileStage]:
        """Get raw profile by ID."""
        return self._raw_crud.get(self.session, raw_profile_id)

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