"""Content/email campaign planning: the AI proposes a plan and picks content
items from a CLOSED candidate list. (The email channel is the outbound template
the campaign draft reuses.)"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ai_providers.base import AIProviderError, parse_json_object
from campaign_planner import base
from campaign_planner.base import (
    CAMPAIGN_PLAN_INSTRUCTIONS,
    BaseBrief,
    BaseGeneratedPlan,
)


@dataclass
class CampaignPlanBrief(BaseBrief):
    pass


@dataclass
class GeneratedCampaignPlan(BaseGeneratedPlan):
    content_item_ids: list[str] = field(default_factory=list)


def _build_prompt(brief: CampaignPlanBrief, candidate_content_items: list[dict[str, Any]]) -> str:
    lines = [base._instructions(CAMPAIGN_PLAN_INSTRUCTIONS), *base._brief_lines(brief)]
    lines.append(f"Candidate content items (choose only from these, by content_item_id): {candidate_content_items}")
    return "\n".join(lines)


def generate_campaign_plan(
    brief: CampaignPlanBrief, candidate_content_items: list[dict[str, Any]]
) -> GeneratedCampaignPlan:
    """Raises AIProviderError on provider failure or an unparsable/incomplete
    response -- caller must persist nothing on that path."""
    provider = base._resolve_provider(brief.model, brief.extra_config)
    raw_text = provider.complete(_build_prompt(brief, candidate_content_items))
    parsed = parse_json_object(raw_text)

    try:
        return GeneratedCampaignPlan(
            name=str(parsed["name"]),
            objective=str(parsed["objective"]),
            strategy_summary=str(parsed["strategy_summary"]),
            action_plan=[str(item) for item in parsed.get("action_plan", [])],
            start_date=date.fromisoformat(str(parsed["start_date"])),
            end_date=date.fromisoformat(str(parsed["end_date"])),
            content_item_ids=[str(item) for item in parsed.get("content_item_ids", [])],
        )
    except (KeyError, ValueError) as exc:
        raise AIProviderError(f"AI provider response missing/invalid required field: {exc}") from exc
