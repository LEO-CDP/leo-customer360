"""Shared customer journey stages and reporting response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JourneyStageValue = Literal[
    "awareness",
    "acquisition",
    "activation",
    "engagement",
    "conversion",
    "retention",
    "loyalty",
    "churned",
]

CampaignAgentRoleValue = Literal["plan", "decide", "optimize"]

JOURNEY_STAGES: tuple[str, ...] = (
    "awareness",
    "acquisition",
    "activation",
    "engagement",
    "conversion",
    "retention",
    "loyalty",
    "churned",
)

JOURNEY_STAGE_LABELS: dict[str, str] = {
    "awareness": "Awareness",
    "acquisition": "Acquisition",
    "activation": "Activation",
    "engagement": "Engagement",
    "conversion": "Conversion",
    "retention": "Retention",
    "loyalty": "Loyalty",
    "churned": "Churned",
}

OBJECTIVE_TO_JOURNEY_STAGE: dict[str, JourneyStageValue] = {
    "awareness": "awareness",
    "brand awareness": "awareness",
    "leads": "acquisition",
    "lead generation": "acquisition",
    "acquisition": "acquisition",
    "app install": "activation",
    "activation": "activation",
    "onboarding": "activation",
    "visit store": "engagement",
    "engagement": "engagement",
    "conversions": "conversion",
    "conversion": "conversion",
    "sales": "conversion",
    "retention": "retention",
    "loyalty": "loyalty",
    "referral": "loyalty",
    "win-back": "churned",
    "winback": "churned",
    "win back": "churned",
    "re-engagement": "churned",
    "reengagement": "churned",
    "churn": "churned",
    "churned": "churned",
}

DEFAULT_STAGE_PRIMARY_METRICS: dict[str, tuple[str, str, str, bool]] = {
    "awareness": ("visitors", "Visitors", "count", True),
    "acquisition": ("new_leads", "New leads", "count", True),
    "activation": ("activated_users", "Activated users", "count", True),
    "engagement": ("active_customers", "Active customers", "count", True),
    "conversion": ("orders", "Orders", "count", True),
    "retention": ("retention_rate", "Retention rate", "percent", True),
    "loyalty": ("repeat_purchase_rate", "Repeat purchase", "percent", True),
    "churned": ("churn_rate", "Churn rate", "percent", False),
}


def infer_journey_stage_from_objective(objective: str | None) -> JourneyStageValue | None:
    """Infer a default 8-stage journey value from a campaign objective string."""
    if not objective:
        return None
    normalized = " ".join(str(objective).strip().lower().split())
    if normalized in OBJECTIVE_TO_JOURNEY_STAGE:
        return OBJECTIVE_TO_JOURNEY_STAGE[normalized]
    for key, stage in OBJECTIVE_TO_JOURNEY_STAGE.items():
        if key in normalized:
            return stage
    return None


class JourneyMetricReport(BaseModel):
    """One current journey KPI and its comparison with the previous window."""

    model_config = ConfigDict(from_attributes=True)

    metric_code: str
    label: str
    unit: str
    is_primary: bool
    higher_is_better: bool
    value: float | None = None
    previous_value: float | None = None
    change: float | None = None
    change_kind: str = "percent"
    dim_type: str = "all"
    dim_value: str = ""
    currency: str = ""


class JourneyCampaignAgentSummary(BaseModel):
    """AI agent assigned to a campaign with its role (plan, decide, optimize)."""

    agent_code: str
    display_name: str
    role: CampaignAgentRoleValue = "decide"
    autonomy: str = "approve"
    model_type: str | None = None
    status: str | None = None


class JourneyCampaignSummary(BaseModel):
    """Campaign surfaced on a journey-stage card."""

    campaign_id: str
    name: str
    status: str | None = None
    approval_status: str | None = None
    workflow_approval_status: str | None = None
    channel: str | None = None
    platform: str | None = None
    objective: str | None = None
    agents: list[JourneyCampaignAgentSummary] = Field(default_factory=list)


class JourneyAgentSummary(BaseModel):
    """Always-on AI agent surfaced on a journey-stage card."""

    agent_code: str
    display_name: str
    model_type: str
    status: str
    schedule_definition: str | None = None
    journey_stage: JourneyStageValue | None = None


class JourneyStageReport(BaseModel):
    """All report data needed to render one of the eight journey stages."""

    stage: JourneyStageValue
    label: str
    primary_metric: JourneyMetricReport | None = None
    metrics: list[JourneyMetricReport] = Field(default_factory=list)
    campaigns: list[JourneyCampaignSummary] = Field(default_factory=list)
    agents: list[JourneyAgentSummary] = Field(default_factory=list)


class JourneyReportResponse(BaseModel):
    """Eight-stage customer journey report for the requested tenant/window."""

    report_date: str | None = None
    window_days: int
    stages: list[JourneyStageReport]

