"""API-layer facade for identity/profile and profile-event operations."""

from typing import Any

from sqlalchemy import select

from leo_customer360_dao.crud import identity as identity_crud
from leo_customer360_dao.crud import profile360 as profile360_crud
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.models.identity import (
    CdpCustomerPersona, CdpDomainProfile, CdpIdResolutionStatus, CdpMasterProfile,
    CdpPersonaHistory, CdpProfileLink, CdpProfileMergeHistory, CdpRawProfileStage,
)
from leo_customer360_dao.models.system import SysDomain


class IdentityRepository:
    """Centralize identity CRUD, profile views, and JSONB attribute updates."""

    def __init__(
        self,
        session,
        link_crud=None,
        master_crud=None,
        raw_crud=None,
        merge_history_crud=None,
        identity_module=None,
        profile360_module=None,
    ):
        """Create identity CRUD facades, preserving injectable test seams."""
        self.session = session
        self.identity_crud = identity_module or identity_crud
        self.profile360_crud = profile360_module or profile360_crud
        self.master_crud = master_crud or CRUDBase(CdpMasterProfile)
        self.raw_crud = raw_crud or CRUDBase(CdpRawProfileStage)
        self.link_crud = link_crud or CRUDBase(CdpProfileLink)
        self.merge_history_crud = merge_history_crud or CRUDBase(CdpProfileMergeHistory)

    def list_master_profiles(self, **filters):
        """Return the paginated master-profile query result."""
        return self.identity_crud.list_master_profiles_page(self.session, **filters)

    def count_master_profiles(self, **filters):
        """Count tenant-scoped master profiles."""
        return self.master_crud.count(self.session, **filters)

    def get_master_profile(self, master_profile_id):
        """Load one master profile by identifier."""
        return self.master_crud.get(self.session, master_profile_id)

    def list_master_profile_links(self, master_profile_id, limit):
        """Load recent raw-profile links for a master profile."""
        statement = (
            select(CdpProfileLink)
            .where(CdpProfileLink.master_profile_id == master_profile_id)
            .order_by(CdpProfileLink.created_at.desc())
            .limit(limit)
        )
        return self.session.execute(statement).scalars().all()

    def get_domain_profiles(self, master_profile_id):
        """Load domain profiles and resolve their human-readable domain codes."""
        profiles = self.session.execute(
            select(CdpDomainProfile).where(CdpDomainProfile.master_profile_id == master_profile_id)
        ).scalars().all()
        ids = {profile.domain_id for profile in profiles}
        codes = dict(self.session.execute(select(SysDomain.domain_id, SysDomain.domain_code).where(SysDomain.domain_id.in_(ids))).all()) if ids else {}
        for profile in profiles:
            profile.domain_code = codes.get(profile.domain_id)
        return profiles

    def upsert_domain_attribute(self, master, domain, attribute_key, attribute_value):
        """Merge one attribute into a domain profile and persist it."""
        domain_id = self.session.execute(select(SysDomain.domain_id).where(SysDomain.domain_code == domain)).scalar_one_or_none()
        if domain_id is None:
            return None
        profile = self.session.execute(select(CdpDomainProfile).where(CdpDomainProfile.master_profile_id == master.master_profile_id, CdpDomainProfile.domain_id == domain_id)).scalar_one_or_none()
        if profile is None:
            profile = CdpDomainProfile(tenant_id=master.tenant_id, master_profile_id=master.master_profile_id, domain_id=domain_id, domain_attributes={attribute_key: attribute_value})
            self.session.add(profile)
        else:
            attributes = dict(profile.domain_attributes or {})
            attributes[attribute_key] = attribute_value
            profile.domain_attributes = attributes
        self.session.commit()
        self.session.refresh(profile)
        return profile

    def get_linked_raw_profile(self, master_profile_id, raw_profile_id, tenant_id):
        """Load a linked raw profile with tenant-scoped joins."""
        statement = select(CdpProfileLink, CdpRawProfileStage).join(CdpRawProfileStage, CdpRawProfileStage.raw_profile_id == CdpProfileLink.raw_profile_id).where(CdpProfileLink.master_profile_id == master_profile_id, CdpProfileLink.raw_profile_id == raw_profile_id, CdpProfileLink.tenant_id == tenant_id, CdpRawProfileStage.tenant_id == tenant_id).limit(1)
        return self.session.execute(statement).first()

    def get_persona(self, profile):
        """Load the current persona for a master profile."""
        return self.session.get(CdpCustomerPersona, profile.current_persona_id)

    def get_persona_history(self, master_profile_id, limit):
        """Return recent persona history for a master profile."""
        statement = select(CdpPersonaHistory).join(CdpCustomerPersona, CdpPersonaHistory.persona_id == CdpCustomerPersona.persona_id).where(CdpCustomerPersona.master_profile_id == master_profile_id).order_by(CdpPersonaHistory.changed_at.desc()).limit(limit)
        return self.session.execute(statement).scalars().all()

    def get_engagement_summary(self, master_profile_id, days):
        """Return the profile360 engagement summary."""
        return self.profile360_crud.get_engagement_summary(self.session, master_profile_id, days=days)

    def get_channel_activity(self, master_profile_id, days):
        """Return the profile360 channel activity summary."""
        return self.profile360_crud.get_channel_activity(self.session, master_profile_id, days=days)

    def get_top_interests(self, master_profile_id, limit):
        """Return the profile360 top interests."""
        return self.profile360_crud.get_top_interests(self.session, master_profile_id, limit=limit)

    def get_timeline(self, master_profile_id, **filters):
        """Return the unified profile activity timeline."""
        return self.profile360_crud.get_timeline(self.session, master_profile_id, **filters)

    def create_master_profile(self, values: dict[str, Any]):
        """Create a master profile through the configured CRUD facade."""
        return self.master_crud.create(self.session, values)

    def update_master_profile(self, profile, values: dict[str, Any]):
        """Update a master profile through the configured CRUD facade."""
        return self.master_crud.update(self.session, profile, values)

    def delete_master_profile(self, profile) -> None:
        """Delete a master profile through the configured CRUD facade."""
        self.master_crud.delete(self.session, profile)

    def list_raw_profiles(self, **filters):
        """List raw profiles through the configured CRUD facade."""
        skip = filters.pop("skip", 0)
        limit = filters.pop("limit", 100)
        return self.raw_crud.list(self.session, skip=skip, limit=limit, **filters)

    def count_raw_profiles(self, **filters):
        """Count raw profiles through the configured CRUD facade."""
        return self.raw_crud.count(self.session, **filters)

    def get_raw_profile(self, raw_profile_id):
        """Load one raw profile by identifier."""
        return self.raw_crud.get(self.session, raw_profile_id)

    def create_raw_profile(self, values: dict[str, Any]):
        """Create a raw profile through the configured CRUD facade."""
        return self.raw_crud.create(self.session, values)

    def update_raw_profile(self, profile, values: dict[str, Any]):
        """Update a raw profile through the configured CRUD facade."""
        return self.raw_crud.update(self.session, profile, values)

    def delete_raw_profile(self, profile) -> None:
        """Delete a raw profile through the configured CRUD facade."""
        self.raw_crud.delete(self.session, profile)

    def list_profile_links(self, **filters):
        """List profile links through the configured CRUD facade."""
        skip = filters.pop("skip", 0)
        limit = filters.pop("limit", 100)
        return self.link_crud.list(self.session, skip=skip, limit=limit, **filters)

    def get_profile_link(self, link_id):
        """Load one profile link by identifier."""
        return self.link_crud.get(self.session, link_id)

    def create_profile_link(self, values: dict[str, Any]):
        """Create a profile link through the configured CRUD facade."""
        return self.link_crud.create(self.session, values)

    def delete_profile_link(self, profile) -> None:
        """Delete a profile link through the configured CRUD facade."""
        self.link_crud.delete(self.session, profile)

    def list_profile_merge_history(self, **filters):
        """List profile merge history through the configured CRUD facade."""
        skip = filters.pop("skip", 0)
        limit = filters.pop("limit", 100)
        return self.merge_history_crud.list(self.session, skip=skip, limit=limit, **filters)

    def get_profile_merge_history(self, merge_id):
        """Load one profile merge history row by identifier."""
        return self.merge_history_crud.get(self.session, merge_id)

    def create_profile_merge_history(self, values: dict[str, Any]):
        """Create a profile merge history row."""
        return self.merge_history_crud.create(self.session, values)

    def get_resolution_status(self):
        """Return the current identity-resolution throttle status."""
        return self.session.get(CdpIdResolutionStatus, True)