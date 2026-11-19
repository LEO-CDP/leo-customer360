"""Discounted contribution CLV derived from the predicted 12-month revenue.

CLV_H = sum_{t=1..H} S_t * M_t / (1 + r)^t - CAC
(docs/research-papers/customer-value-measurement.md). The model's 12-month
revenue prediction already reflects the chance the customer stays active, so
year 1 uses S_1 = 1 and later years compound the annual retention rate.
"""

from __future__ import annotations

from dataclasses import dataclass

from .settings import ClvSettings


@dataclass(frozen=True)
class ClvOutputs:
    expected_annual_revenue: float
    expected_annual_contribution_profit: float | None
    predicted_clv_12m: float | None
    predicted_clv_36m: float | None
    predicted_clv_horizon_before_cac: float | None
    predicted_clv_horizon_after_cac: float | None
    predicted_revenue_horizon: float | None
    predicted_contribution_profit_horizon: float | None


def derive_outputs(expected_annual_revenue: float, settings: ClvSettings) -> ClvOutputs:
    revenue = max(0.0, float(expected_annual_revenue))
    margin = settings.gross_margin_rate
    if margin is None:
        # Without a margin there is no contribution value; only revenue is known.
        return ClvOutputs(revenue, None, None, None, None, None, None, None)

    annual_cost = settings.annual_cost_per_customer or 0.0
    contribution = revenue * margin - annual_cost
    rate = settings.discount_rate
    clv_12m = contribution / (1 + rate)

    retention = settings.annual_retention_rate
    if retention is None:
        return ClvOutputs(revenue, contribution, clv_12m, None, None, None, None, None)

    def discounted(years: int) -> float:
        return sum(
            contribution * retention ** (year - 1) / (1 + rate) ** year
            for year in range(1, years + 1)
        )

    horizon = settings.forecast_horizon_years
    before_cac = discounted(horizon)
    after_cac = (
        None if settings.acquisition_cost is None else before_cac - settings.acquisition_cost
    )
    survival = [retention ** (year - 1) for year in range(1, horizon + 1)]
    return ClvOutputs(
        expected_annual_revenue=revenue,
        expected_annual_contribution_profit=contribution,
        predicted_clv_12m=clv_12m,
        predicted_clv_36m=discounted(3),
        predicted_clv_horizon_before_cac=before_cac,
        predicted_clv_horizon_after_cac=after_cac,
        predicted_revenue_horizon=revenue * sum(survival),
        predicted_contribution_profit_horizon=contribution * sum(survival),
    )


def model_assumptions(settings: ClvSettings) -> dict:
    return {
        "label": "net_realised_revenue_365d",
        "reporting_currency": settings.reporting_currency,
        "gross_margin_rate": settings.gross_margin_rate,
        "annual_retention_rate": settings.annual_retention_rate,
        "annual_cost_per_customer": settings.annual_cost_per_customer,
        "cost_source": (
            "configured" if settings.annual_cost_per_customer is not None else "not_provided_zero"
        ),
        "acquisition_cost": settings.acquisition_cost,
        "discount_rate": settings.discount_rate,
        "forecast_horizon_years": settings.forecast_horizon_years,
        "year_one_survival": 1.0,
    }
