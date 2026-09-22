"""Shared campaign-planning primitives: prompt-store keys, provider resolution,
and the brief/plan base dataclasses. Per-channel modules (email, zalo) build on
these. Channel modules call `base._resolve_provider` / `base._instructions` so a
test can patch them in one place."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ai_providers.provider import LLMProvider, build_state
from prompts import get_store

# Prompt-store keys; the bodies live in the DB (seeded by customer360-database).
CAMPAIGN_PLAN_INSTRUCTIONS = "campaign.plan.instructions"
CAMPAIGN_ZNS_INSTRUCTIONS = "campaign.zns.instructions"


@dataclass
class BaseBrief:
    """Shared marketer brief: segment + objective + per-request model/extra override."""

    segment_context: dict[str, Any]
    objective: str
    budget_time_constraints: Optional[str] = None
    model: Optional[str] = None
    extra_config: Optional[dict[str, Any]] = None


@dataclass
class BaseGeneratedPlan:
    """Shared generated-plan fields; channel modules add their specifics."""

    name: str
    objective: str
    strategy_summary: str
    action_plan: list[str] = field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


def _instructions(key: str) -> str:
    """The stored instruction body for `key` (from the prompt store / DB)."""
    return get_store().get(key).render()


def _resolve_provider(
    model: Optional[str] = None,
    extra_config: Optional[dict[str, Any]] = None,
) -> LLMProvider:
    return LLMProvider(build_state(model, extra_config))


def _brief_lines(brief: BaseBrief) -> list[str]:
    """The segment/objective/constraints lines shared by every channel prompt."""
    lines = [f"Segment: {brief.segment_context}", f"Objective: {brief.objective}"]
    if brief.budget_time_constraints:
        lines.append(f"Budget/time constraints: {brief.budget_time_constraints}")
    return lines
