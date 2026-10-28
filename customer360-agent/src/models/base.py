"""Shared request/response models for the agent HTTP API. Channel modules
(email, zalo) add their specifics; `_brief_fields()` is the shared request->brief
mapper."""

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


class BasePlanRequest(BaseModel):
    """Shared brief: segment + objective + per-request model/extra override."""

    segment_context: dict[str, Any]
    objective: str
    budget_time_constraints: Optional[str] = None
    model: Optional[str] = None
    extra_config: Optional[dict[str, Any]] = None

    def _brief_fields(self) -> dict[str, Any]:
        """The shared fields every planner brief takes (subclasses map these into
        their channel's brief via to_brief())."""
        return {
            "segment_context": self.segment_context,
            "objective": self.objective,
            "budget_time_constraints": self.budget_time_constraints,
            "model": self.model,
            "extra_config": self.extra_config,
        }


class BasePlanResponse(BaseModel):
    """Shared generated-plan fields; channel modules add their specifics."""

    name: str
    objective: str
    strategy_summary: str
    action_plan: list[str] = Field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
