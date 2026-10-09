"""Single-campaign report math and the enriched campaign table query."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from leo_customer360_dao.repositories.campaign_repository import CampaignRepository, metric_totals
from leo_customer360_dao.schemas.crm import CampaignFilterParams

TENANT_ID = uuid.uuid4()
CAMPAIGN_ID = uuid.uuid4()


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalar(self):
        return self._scalar


class _RecordingSession:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append(statement)
        if params is not None:  # set_config(app.tenant_id)
            return _Result()
        return self._results.pop(0)


def test_zero_conversions_reports_no_cpa_and_warns():
    totals = metric_totals(Decimal("150.00"), 1000, 40, 0, Decimal("0"))

    assert totals["cpa"] is None
    assert totals["zero_conversion_warning"] is True
    assert totals["ctr_percentage"] == Decimal("4.00")
    assert totals["roas"] == Decimal("0.00")


def test_rates_are_derived_from_totals():
    totals = metric_totals(Decimal("200"), 2000, 100, 4, Decimal("1000"))

    assert totals["cpa"] == Decimal("50.00")
    assert totals["cvr_percentage"] == Decimal("4.00")
    assert totals["roas"] == Decimal("5.00")
    assert totals["zero_conversion_warning"] is False


def test_no_spend_reports_no_roas():
    assert metric_totals(0, 0, 0, 0, 0)["roas"] is None


def test_campaign_performance_filters_dates_and_tenant_and_sums_period():
    daily_rows = [
        SimpleNamespace(report_date=date(2026, 1, 1), spend=Decimal("10"), impressions=100, clicks=5, conversions=0, revenue=Decimal("0")),
        SimpleNamespace(report_date=date(2026, 1, 2), spend=Decimal("20"), impressions=300, clicks=10, conversions=0, revenue=Decimal("0")),
    ]
    lifetime = SimpleNamespace(total_spend=Decimal("90"), total_impressions=900, total_clicks=30, total_conversions=3, total_revenue=Decimal("270"))
    session = _RecordingSession([_Result(rows=daily_rows), _Result(scalar=lifetime)])

    report = CampaignRepository(session, TENANT_ID).get_campaign_performance(
        CAMPAIGN_ID, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )

    daily_sql = _sql(session.statements[1])
    assert f"tenant_id = '{TENANT_ID}'" in daily_sql
    assert f"campaign_id = '{CAMPAIGN_ID}'" in daily_sql
    assert "report_date >= '2026-01-01'" in daily_sql and "report_date <= '2026-01-31'" in daily_sql
    assert f"tenant_id = '{TENANT_ID}'" in _sql(session.statements[2])

    assert [day["report_date"] for day in report["daily"]] == [date(2026, 1, 1), date(2026, 1, 2)]
    assert report["period_totals"]["spend"] == Decimal("30")
    assert report["period_totals"]["zero_conversion_warning"] is True
    assert report["lifetime"]["cpa"] == Decimal("30.00")


def test_campaign_performance_without_any_rows_is_all_zero():
    session = _RecordingSession([_Result(rows=[]), _Result(scalar=None)])

    report = CampaignRepository(session, TENANT_ID).get_campaign_performance(CAMPAIGN_ID)

    assert report["daily"] == []
    assert report["lifetime"]["spend"] == Decimal("0")
    assert report["lifetime"]["zero_conversion_warning"] is True


def test_campaign_table_joins_management_columns_within_the_tenant():
    session = _RecordingSession([_Result(scalar=0), _Result(rows=[])])

    items, total = CampaignRepository(session, TENANT_ID).get_filtered_campaigns(CampaignFilterParams())

    sql = _sql(session.statements[2])
    assert "JOIN customer360.crm_campaign" in sql
    assert f"crm_campaign.tenant_id = '{TENANT_ID}'" in sql
    assert "crm_campaign.approval_status" in sql
    assert "agent_provenance" in sql and "agent_display_name" in sql
    assert (items, total) == ([], 0)
