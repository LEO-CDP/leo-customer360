"""Segment-rule request/response. Not on `BasePlanRequest`: that carries a campaign brief."""

from typing import Any, Optional

from pydantic import BaseModel, Field

from segment_planner import SegmentRulesBrief


class SegmentRulesRequest(BaseModel):
    # Capped here too, so the agent is safe to call directly.
    description: str = Field(..., min_length=1, max_length=2000)
    # Supplied by the caller; the agent has no DB access.
    attributes: list[dict[str, Any]] = Field(default_factory=list)
    domain: Optional[str] = None
    model: Optional[str] = None
    extra_config: Optional[dict[str, Any]] = None
    current_rules: Optional[dict[str, Any]] = None
    # Multi-turn state from the browser (untrusted).
    so_far: Optional[str] = Field(None, max_length=2000)
    last_question: Optional[str] = Field(None, max_length=2000)

    def to_brief(self) -> SegmentRulesBrief:
        return SegmentRulesBrief(
            description=self.description,
            attributes=self.attributes,
            domain=self.domain,
            model=self.model,
            extra_config=self.extra_config,
            current_rules=self.current_rules,
            so_far=self.so_far,
            last_question=self.last_question,
        )


class SegmentRulesResponse(BaseModel):
    segment_tag: str
    segment_name: str
    json_rules: dict[str, Any]
    explanation: str = ""
    outcome: str = "rules"  # rules | ask | not_possible
    so_far: Optional[str] = None
