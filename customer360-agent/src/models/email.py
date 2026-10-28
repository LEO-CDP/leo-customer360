"""Email/content campaign request + response models."""

from typing import Any

from pydantic import Field

from campaign_planner import CampaignPlanBrief
from models.base import BasePlanRequest, BasePlanResponse


class EmailCampaignPlanRequest(BasePlanRequest):
    candidate_content_items: list[dict[str, Any]] = Field(default_factory=list)

    def to_brief(self) -> CampaignPlanBrief:
        return CampaignPlanBrief(**self._brief_fields())


class EmailCampaignPlanResponse(BasePlanResponse):
    content_item_ids: list[str] = Field(default_factory=list)
