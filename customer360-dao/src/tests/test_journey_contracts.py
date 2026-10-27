"""Contract and repository tests for the eight-stage customer journey feature."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from leo_customer360_dao.repositories.reporting_respository import ReportingRepository
from leo_customer360_dao.schemas.crm import CampaignCreate, CampaignUpdate, EditCampaignDraftRequest
from leo_customer360_dao.schemas.journey import (
    JOURNEY_STAGES,
    JourneyReportResponse,
    infer_journey_stage_from_objective,
)
from leo_customer360_dao.schemas.system import AiAgentCreate, AiAgentUpdate


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.statements = []

    def execute(self, stmt):
        self.statements.append(stmt)
        return self._responses.pop(0)


def test_campaign_and_agent_accept_targeted_journey_stage():
    campaign = CampaignCreate(
        tenant_id=uuid4(),
        name="Retention win-back",
        journey_stage="retention",
    )
    campaign_update = CampaignUpdate(journey_stage="conversion")
    draft_edit = EditCampaignDraftRequest(journey_stage="engagement")
    agent_create = AiAgentCreate(
        agent_code="retention_optimizer",
        display_name="Retention Optimizer Agent",
        model_type="optimization",
        journey_stage="retention",
    )
    agent_update = AiAgentUpdate(journey_stage="churned")

    assert campaign.journey_stage == "retention"
    assert campaign_update.journey_stage == "conversion"
    assert draft_edit.journey_stage == "engagement"
    assert agent_create.journey_stage == "retention"
    assert agent_update.journey_stage == "churned"


def test_campaign_and_agent_reject_unknown_journey_stage():
    with pytest.raises(ValidationError):
        CampaignCreate(
            tenant_id=uuid4(),
            name="Invalid journey",
            journey_stage="consideration",
        )
    with pytest.raises(ValidationError):
        AiAgentUpdate(journey_stage="consideration")


def test_infer_journey_stage_from_objective():
    assert infer_journey_stage_from_objective("Awareness") == "awareness"
    assert infer_journey_stage_from_objective("Leads") == "acquisition"
    assert infer_journey_stage_from_objective("App Install") == "activation"
    assert infer_journey_stage_from_objective("Visit Store") == "engagement"
    assert infer_journey_stage_from_objective("Conversions") == "conversion"
    assert infer_journey_stage_from_objective("Win-back lapsed customers") == "churned"
    assert infer_journey_stage_from_objective(None) is None


def test_journey_report_contract_requires_all_eight_stages():
    report = JourneyReportResponse(
        report_date=None,
        window_days=30,
        stages=[
            {
                "stage": stage,
                "label": stage.title(),
                "metrics": [],
                "campaigns": [],
                "agents": [],
            }
            for stage in JOURNEY_STAGES
        ],
    )

    assert len(report.stages) == 8
    assert [stage.stage for stage in report.stages] == list(JOURNEY_STAGES)


def test_reporting_repository_journey_report_aggregates_metrics_campaigns_and_agents():
    tenant_id = uuid4()
    campaign_id = uuid4()
    legacy_campaign_id = uuid4()
    snapshot_date = date(2026, 10, 26)

    metric_row = SimpleNamespace(
        journey_stage="retention",
        metric_code="retention_rate",
        label="Retention rate",
        unit="percent",
        is_primary=True,
        higher_is_better=True,
        display_order=10,
        dim_type="all",
        dim_value="",
        value=Decimal("68.4000"),
        previous_value=Decimal("65.0000"),
        change_=Decimal("3.40"),
        change_kind="points",
        currency="",
        domain_code=None,
    )
    catalog_rows = [
        ("visitors", "awareness", "Visitors", "count", True, True, 10),
        ("retention_rate", "retention", "Retention rate", "percent", True, True, 10),
    ]
    campaign_rows = [
        (
            campaign_id,
            "Win-back",
            "Failed",
            "Approved",
            "email",
            "email",
            "Retention",
            "retention",
        ),
        (
            legacy_campaign_id,
            "Lead nurture",
            "Running",
            "Approved",
            "email",
            "email",
            "Leads",
            None,
        ),
    ]
    campaign_agent_rows = [
        (
            campaign_id,
            "next_best_action",
            "optimize",
            "approve",
            "Re-engagement Agent",
            "optimization",
            "ACTIVE",
        ),
        (
            campaign_id,
            "clv_prediction",
            "decide",
            "suggest",
            "CLV Agent",
            "regression",
            "ACTIVE",
        ),
    ]
    agent_rows = [
        (
            "clv_prediction",
            "Customer Lifetime Value Agent",
            "regression",
            "ACTIVE",
            "0 2 * * *",
            "retention",
        ),
        (
            "pricing_optimization",
            "Pricing Optimization Agent",
            "optimization",
            "INACTIVE",
            "0 3 * * *",
            "conversion",
        ),
    ]

    session = _FakeSession(
        [
            _FakeResult(scalar=snapshot_date),
            _FakeResult(rows=[metric_row]),
            _FakeResult(rows=catalog_rows),
            _FakeResult(rows=campaign_rows),
            _FakeResult(rows=campaign_agent_rows),
            _FakeResult(rows=agent_rows),
        ]
    )

    report = ReportingRepository(session).get_journey_report(
        tenant_id=tenant_id,
        days=90,
        window_days=30,
    )

    assert report.report_date == "2026-10-26"
    assert report.window_days == 30
    assert [s.stage for s in report.stages] == list(JOURNEY_STAGES)

    by_stage = {s.stage: s for s in report.stages}
    # Awareness has no snapshot row, but still gets its catalog primary KPI metadata
    assert by_stage["awareness"].primary_metric is not None
    assert by_stage["awareness"].primary_metric.label == "Visitors"
    assert by_stage["awareness"].primary_metric.value is None

    # Acquisition receives the legacy campaign whose stage was inferred from objective='Leads'
    assert len(by_stage["acquisition"].campaigns) == 1
    assert by_stage["acquisition"].campaigns[0].name == "Lead nurture"

    # Conversion includes the stage-targeted inactive agent so UI can render "(inactive)"
    assert len(by_stage["conversion"].agents) == 1
    assert by_stage["conversion"].agents[0].status == "INACTIVE"

    # Churned has default primary KPI (Churn rate, higher_is_better=False)
    assert by_stage["churned"].primary_metric is not None
    assert by_stage["churned"].primary_metric.label == "Churn rate"
    assert by_stage["churned"].primary_metric.higher_is_better is False

    # Retention includes snapshot metric, campaign, and assigned campaign agents with roles
    retention = by_stage["retention"]
    assert retention.primary_metric is not None
    assert retention.primary_metric.value == pytest.approx(68.4)
    assert retention.primary_metric.change == pytest.approx(3.4)
    assert len(retention.campaigns) == 1
    assert retention.campaigns[0].name == "Win-back"
    assert [(a.agent_code, a.role) for a in retention.campaigns[0].agents] == [
        ("next_best_action", "optimize"),
        ("clv_prediction", "decide"),
    ]
    assert [a.agent_code for a in retention.agents] == ["clv_prediction"]

