"""AI campaign-planning package. Shared bits in `base`; one module per channel
(`email` = content campaigns, `zalo` = ZNS). Public API is re-exported here so
callers keep `from campaign_planner import ...`.
"""

from ai_providers.base import AIProviderError
from campaign_planner import base  # noqa: F401 -- exposes cp.base (patch point)
from campaign_planner.base import (
    CAMPAIGN_PLAN_INSTRUCTIONS,
    CAMPAIGN_ZNS_INSTRUCTIONS,
    BaseBrief,
    BaseGeneratedPlan,
    _instructions,
    _resolve_provider,
)
from campaign_planner.email import (
    CampaignPlanBrief,
    GeneratedCampaignPlan,
    generate_campaign_plan,
)
from campaign_planner.zalo import (
    GeneratedZnsCampaignPlan,
    ZnsCampaignPlanBrief,
    generate_zalo_campaign_plan,
)

__all__ = [
    "AIProviderError",
    "BaseBrief",
    "BaseGeneratedPlan",
    "CAMPAIGN_PLAN_INSTRUCTIONS",
    "CAMPAIGN_ZNS_INSTRUCTIONS",
    "CampaignPlanBrief",
    "GeneratedCampaignPlan",
    "GeneratedZnsCampaignPlan",
    "ZnsCampaignPlanBrief",
    "base",
    "generate_campaign_plan",
    "generate_zalo_campaign_plan",
]
