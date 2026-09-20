"""Client library for the customer360-agent service.

Consumers (e.g. customer360-api) install this package and call the agent over
HTTP via `leo_customer360_agent.client` — the agent owns its own client contract,
the same way customer360-dao owns the shared data models. This package does NOT
contain the agent server (that runs from customer360-agent/src, unpackaged)."""

from leo_customer360_agent.client import (
    AIProviderError,
    CampaignPlanBrief,
    GeneratedCampaignPlan,
    GeneratedZnsCampaignPlan,
    ZnsCampaignPlanBrief,
    generate_campaign_plan,
    generate_zalo_campaign_plan,
)

__all__ = [
    "AIProviderError",
    "CampaignPlanBrief",
    "GeneratedCampaignPlan",
    "GeneratedZnsCampaignPlan",
    "ZnsCampaignPlanBrief",
    "generate_campaign_plan",
    "generate_zalo_campaign_plan",
]
