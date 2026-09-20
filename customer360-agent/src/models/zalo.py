"""Zalo ZNS campaign request + response models."""

from typing import Any

from pydantic import Field

from campaign_planner import ZnsCampaignPlanBrief
from models.base import BasePlanRequest, BasePlanResponse


class ZnsCampaignPlanRequest(BasePlanRequest):
    candidate_templates: list[dict[str, Any]] = Field(default_factory=list)

    def to_brief(self) -> ZnsCampaignPlanBrief:
        return ZnsCampaignPlanBrief(**self._brief_fields())


class ZnsCampaignPlanResponse(BasePlanResponse):
    template_id: str
    template_data: dict[str, Any]
