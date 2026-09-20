"""HTTP client to the customer360-agent service (AI campaign planning).

The provider abstraction + prompt logic moved out of this API into the
standalone customer360-agent service. This module POSTs briefs to it and maps
the JSON back into the same dataclasses campaign_draft_repository already
consumes, raising AIProviderError on any provider/HTTP/parse failure so the
repository's error handling is unchanged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from leo_customer360_dao.config import settings

_REQUEST_TIMEOUT_SECONDS = 60


class AIProviderError(RuntimeError):
    """Raised on any agent-service failure (provider error, HTTP/timeout, or an
    unparsable response) -- same type the repository already catches."""


# --- Brief / result shapes matching the customer360-agent request contract
#     (segment + objective + optional model/extra_config override). --------------
@dataclass
class CampaignPlanBrief:
    segment_context: dict[str, Any]
    objective: str
    budget_time_constraints: Optional[str] = None
    model: Optional[str] = None
    extra_config: Optional[dict[str, Any]] = None


@dataclass
class GeneratedCampaignPlan:
    name: str
    objective: str
    strategy_summary: str
    action_plan: list[str] = field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    content_item_ids: list[str] = field(default_factory=list)


@dataclass
class ZnsCampaignPlanBrief:
    segment_context: dict[str, Any]
    objective: str
    budget_time_constraints: Optional[str] = None
    model: Optional[str] = None
    extra_config: Optional[dict[str, Any]] = None


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


def _parse_date(value: Any) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _post(path: str, payload: dict) -> dict:
    url = settings.agent_service_url.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if settings.agent_api_token:
        headers["Authorization"] = f"Bearer {settings.agent_api_token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The agent maps provider failures to 502 {"detail": "..."}; surface that.
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
        except Exception:
            detail = str(exc)
        raise AIProviderError(f"AI agent error: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        detail = getattr(exc, "reason", exc)
        raise AIProviderError(f"AI agent request failed: {detail}") from exc


def generate_campaign_plan(
    brief: CampaignPlanBrief, candidate_content_items: list[dict[str, Any]]
) -> GeneratedCampaignPlan:
    data = _post(
        "/plan/email",
        {
            "segment_context": brief.segment_context,
            "objective": brief.objective,
            "budget_time_constraints": brief.budget_time_constraints,
            "model": brief.model,
            "extra_config": brief.extra_config,
            "candidate_content_items": candidate_content_items,
        },
    )
    return GeneratedCampaignPlan(
        name=data["name"],
        objective=data["objective"],
        strategy_summary=data["strategy_summary"],
        action_plan=list(data.get("action_plan", [])),
        start_date=_parse_date(data.get("start_date")),
        end_date=_parse_date(data.get("end_date")),
        content_item_ids=list(data.get("content_item_ids", [])),
    )


def generate_zalo_campaign_plan(
    brief: ZnsCampaignPlanBrief, candidate_templates: list[dict[str, Any]]
) -> GeneratedZnsCampaignPlan:
    data = _post(
        "/plan/zalo",
        {
            "segment_context": brief.segment_context,
            "objective": brief.objective,
            "budget_time_constraints": brief.budget_time_constraints,
            "model": brief.model,
            "extra_config": brief.extra_config,
            "candidate_templates": candidate_templates,
        },
    )
    return GeneratedZnsCampaignPlan(
        name=data["name"],
        objective=data["objective"],
        strategy_summary=data["strategy_summary"],
        template_id=data["template_id"],
        template_data=dict(data.get("template_data", {})),
        action_plan=list(data.get("action_plan", [])),
        start_date=_parse_date(data.get("start_date")),
        end_date=_parse_date(data.get("end_date")),
    )
