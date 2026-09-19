"""AI campaign-planning provider layer for 002-ai-campaign-draft-creation.

Reuses the existing GeminiProvider/OpenAIProvider classes and
CRM_EMAIL_AI_PROVIDER default-provider setting from
001-ai-email-template-authoring (research.md §4), via each provider's
generic complete(prompt) -> str method, with a campaign-planning
prompt/response shape instead of email subject/html_body/text_body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from core.ai_providers.base import AIProvider, AIProviderError, parse_json_object
from core.ai_providers.gemini_provider import GeminiProvider
from core.ai_providers.openai_provider import OpenAIProvider
from leo_customer360_dao.config import settings

_PROVIDERS: dict[str, type[AIProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


@dataclass
class CampaignPlanBrief:
    segment_context: dict[str, Any]
    objective: str
    budget_time_constraints: Optional[str] = None
    ai_provider: Optional[str] = None


@dataclass
class GeneratedCampaignPlan:
    name: str
    objective: str
    strategy_summary: str
    action_plan: list[str] = field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    content_item_ids: list[str] = field(default_factory=list)


_PROMPT_INSTRUCTIONS = (
    "You are a marketing campaign strategist. Given a target segment, a "
    "marketer's objective, optional budget/time constraints, and a CLOSED "
    "list of candidate content items, propose a campaign plan. You MUST "
    "select recommended content only from the supplied candidate list -- "
    "you MUST NOT invent new content_item_id values or reference any item "
    "not in that list. If no candidate items are suitable, return an empty "
    "content_item_ids array rather than fabricating one. Respond with ONLY "
    "a JSON object with exactly these keys: \"name\" (string), \"objective\" "
    "(string), \"strategy_summary\" (string), \"action_plan\" (array of "
    "short strings), \"start_date\" (string, YYYY-MM-DD), \"end_date\" "
    "(string, YYYY-MM-DD), \"content_item_ids\" (array of strings, each "
    "exactly one of the candidate content_item_id values, ordered by "
    "recommended priority)."
)


def _resolve_provider(ai_provider: Optional[str]) -> AIProvider:
    provider_name = (ai_provider or settings.crm_email_ai_provider or "gemini").lower()
    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise AIProviderError(f"Unsupported AI provider '{provider_name}'")
    return provider_cls()


def _build_prompt(brief: CampaignPlanBrief, candidate_content_items: list[dict[str, Any]]) -> str:
    lines = [
        _PROMPT_INSTRUCTIONS,
        f"Segment: {brief.segment_context}",
        f"Objective: {brief.objective}",
    ]
    if brief.budget_time_constraints:
        lines.append(f"Budget/time constraints: {brief.budget_time_constraints}")
    lines.append(f"Candidate content items (choose only from these, by content_item_id): {candidate_content_items}")
    return "\n".join(lines)


def generate_campaign_plan(
    brief: CampaignPlanBrief, candidate_content_items: list[dict[str, Any]]
) -> GeneratedCampaignPlan:
    """Raises AIProviderError on provider failure or an unparsable/incomplete
    response -- caller must persist nothing on that path (mirrors
    001-ai-email-template-authoring's create_draft contract)."""
    provider = _resolve_provider(brief.ai_provider)
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


# --- Zalo ZNS param-fill (channel='zalo_zns') -----------------------------
# Same provider abstraction; the AI SELECTS one approved template + fills its
# typed params instead of authoring free content (ZNS content is fixed by the
# approved template, see PLAN-ZALO-ZNS-OPTIMIZED.md §3.2/§3.5).
@dataclass
class ZnsCampaignPlanBrief:
    segment_context: dict[str, Any]
    objective: str
    budget_time_constraints: Optional[str] = None
    ai_provider: Optional[str] = None


@dataclass
class GeneratedZnsCampaignPlan:
    name: str
    objective: str
    strategy_summary: str
    template_id: str
    template_data: dict[str, Any]
    action_plan: list[str] = field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


_ZNS_PROMPT_INSTRUCTIONS = (
    "You are a Zalo ZNS campaign strategist. Given a target segment, a "
    "marketer's objective, and a CLOSED list of APPROVED ZNS templates (each "
    "with a template_id and its required parameter names), choose exactly ONE "
    "template and fill EVERY one of its required parameters with concrete values "
    "suitable for the segment. You MUST pick a template_id from the candidate "
    "list -- never invent one -- and you MUST NOT author free message text (ZNS "
    "content is fixed by the approved template). Respond with ONLY a JSON object "
    "with exactly these keys: \"template_id\" (string, one of the candidates), "
    "\"template_data\" (object mapping every required param name to a string "
    "value), \"name\" (string), \"objective\" (string), \"strategy_summary\" "
    "(string), \"action_plan\" (array of short strings), \"start_date\" "
    "(YYYY-MM-DD), \"end_date\" (YYYY-MM-DD)."
)


def _build_zns_prompt(brief: ZnsCampaignPlanBrief, candidate_templates: list[dict[str, Any]]) -> str:
    lines = [
        _ZNS_PROMPT_INSTRUCTIONS,
        f"Segment: {brief.segment_context}",
        f"Objective: {brief.objective}",
    ]
    if brief.budget_time_constraints:
        lines.append(f"Budget/time constraints: {brief.budget_time_constraints}")
    lines.append(
        f"Candidate approved ZNS templates (choose one template_id, fill its params): {candidate_templates}"
    )
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
    off-list template, or a missing required param -- caller persists nothing on
    that path (the campaign draft stays unwritten)."""
    provider = _resolve_provider(brief.ai_provider)
    raw_text = provider.complete(_build_zns_prompt(brief, candidate_templates))
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
