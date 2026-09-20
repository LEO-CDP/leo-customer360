"""Zalo ZNS campaign planning: the AI SELECTS one approved template from a CLOSED
list and fills its typed params (ZNS content is fixed by the approved template)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ai_providers.base import AIProviderError, parse_json_object
from campaign_planner import base
from campaign_planner.base import (
    CAMPAIGN_ZNS_INSTRUCTIONS,
    BaseBrief,
    BaseGeneratedPlan,
)


@dataclass
class ZnsCampaignPlanBrief(BaseBrief):
    pass


@dataclass
class GeneratedZnsCampaignPlan(BaseGeneratedPlan):
    template_id: str = ""
    template_data: dict[str, Any] = field(default_factory=dict)


def _build_prompt(brief: ZnsCampaignPlanBrief, candidate_templates: list[dict[str, Any]]) -> str:
    lines = [base._instructions(CAMPAIGN_ZNS_INSTRUCTIONS), *base._brief_lines(brief)]
    lines.append(f"Candidate approved ZNS templates (choose one template_id, fill its params): {candidate_templates}")
    return "\n".join(lines)


def _required_params(template: dict[str, Any]) -> list[str]:
    """Required param names for a candidate template (accepts a list of names or
    of {name/key: ...} objects)."""
    names = []
    for p in template.get("params") or template.get("required_params") or []:
        names.append(p if isinstance(p, str) else str(p.get("name") or p.get("key") or ""))
    return [n for n in names if n]


def generate_zalo_campaign_plan(
    brief: ZnsCampaignPlanBrief, candidate_templates: list[dict[str, Any]]
) -> GeneratedZnsCampaignPlan:
    """AI selects one APPROVED ZNS template from a closed list and fills its typed
    params. Raises AIProviderError on provider failure, an unparsable response, an
    off-list template, or a missing required param."""
    provider = base._resolve_provider(brief.model, brief.extra_config)
    raw_text = provider.complete(_build_prompt(brief, candidate_templates))
    parsed = parse_json_object(raw_text)

    try:
        template_id = str(parsed["template_id"])
        template_data = dict(parsed["template_data"])
        plan = GeneratedZnsCampaignPlan(
            name=str(parsed["name"]),
            objective=str(parsed["objective"]),
            strategy_summary=str(parsed["strategy_summary"]),
            template_id=template_id,
            template_data=template_data,
            action_plan=[str(item) for item in parsed.get("action_plan", [])],
            start_date=date.fromisoformat(str(parsed["start_date"])),
            end_date=date.fromisoformat(str(parsed["end_date"])),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise AIProviderError(f"AI provider response missing/invalid required field: {exc}") from exc

    # Guardrails: the template must be on the candidate list, and every required
    # param must be filled -- an unbound param would fail the ZNS send.
    candidates = {str(t.get("template_id")): t for t in candidate_templates}
    template = candidates.get(template_id)
    if template is None:
        raise AIProviderError(f"AI selected template_id '{template_id}' not in the candidate list")
    missing = [p for p in _required_params(template) if not str(template_data.get(p, "")).strip()]
    if missing:
        raise AIProviderError(f"AI plan missing required ZNS params: {missing}")
    return plan
