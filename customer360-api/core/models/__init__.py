"""Import every ORM model so Base.metadata is fully populated (needed for
relationship/FK resolution and any metadata-driven tooling)."""

from core.models.base import Base
from core.models.content import CdpContentItem
from core.models.crm import (
    Account,
    Campaign,
    CampaignMember,
    Contact,
    Industry,
    Lead,
    LeadSource,
    Opportunity,
)
from core.models.graph import GraphEdge
from core.models.identity import (
    CdpCustomerPersona,
    CdpDomainProfile,
    CdpIdentityIndex,
    CdpIdResolutionStatus,
    CdpMasterProfile,
    CdpPersonaFeature,
    CdpPersonaHistory,
    CdpPersonaScoreDetail,
    CdpProfileAttribute,
    CdpProfileLink,
    CdpProfileMergeHistory,
    CdpRawProfileStage,
    CdpScoringModel,
)
from core.models.relations import CdpRelation, CustomerContact, RelationType, Transaction
from core.models.segmentation import CdpSegment
from core.models.system import SysDataSource, SysDomain, SysTenantDomain, sys_tenant_table, sys_user_table

__all__ = [
    "Base",
    "Account",
    "Campaign",
    "CampaignMember",
    "CdpContentItem",
    "Contact",
    "Industry",
    "Lead",
    "LeadSource",
    "Opportunity",
    "GraphEdge",
    "CdpCustomerPersona",
    "CdpDomainProfile",
    "CdpIdentityIndex",
    "CdpIdResolutionStatus",
    "CdpMasterProfile",
    "CdpPersonaFeature",
    "CdpPersonaHistory",
    "CdpPersonaScoreDetail",
    "CdpProfileAttribute",
    "CdpProfileLink",
    "CdpProfileMergeHistory",
    "CdpRawProfileStage",
    "CdpRelation",
    "CdpScoringModel",
    "CdpSegment",
    "CustomerContact",
    "Transaction",
    "RelationType",
    "SysDomain",
    "SysDataSource",
    "SysTenantDomain",
    "sys_tenant_table",
    "sys_user_table",
]
