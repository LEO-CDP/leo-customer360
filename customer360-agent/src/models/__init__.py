"""HTTP request/response models. Shared bits in `base`; one module per channel
(`email`, `zalo`). Each request maps to its planner brief via `to_brief()`."""

from models.base import BasePlanRequest, BasePlanResponse
from models.email import EmailCampaignPlanRequest, EmailCampaignPlanResponse
from models.zalo import ZnsCampaignPlanRequest, ZnsCampaignPlanResponse

__all__ = [
    "BasePlanRequest",
    "BasePlanResponse",
    "EmailCampaignPlanRequest",
    "EmailCampaignPlanResponse",
    "ZnsCampaignPlanRequest",
    "ZnsCampaignPlanResponse",
]
