from datetime import date

import numpy as np
import pytest

from ai_agents_runners.clv.finance import derive_outputs
from ai_agents_runners.clv.model import (
    calibration,
    normalised_gini,
    promotion_gate,
    top_decile_capture,
    train_model,
)
from ai_agents_runners.clv.service import training_cutoffs
from ai_agents_runners.clv.settings import (
    DEFAULT_TIER_BANDS,
    TRANSACTION_FEATURE_KEYS,
    ClvSettings,
    TierBand,
)
from ai_agents_runners.clv.tiers import (
    assign_tier,
    percentile_from_quantiles,
    percentile_ranks,
    population_quantiles,
)

BANDS = DEFAULT_TIER_BANDS  # champion 95, high_value 80, growth_potential 50, standard 0


def test_percentile_ranks_are_population_relative_and_share_ties():
    assert percentile_ranks([10.0, 30.0, 20.0, 40.0, 50.0]) == [0.0, 50.0, 25.0, 75.0, 100.0]
    assert percentile_ranks([5.0, 5.0, 1.0]) == [75.0, 75.0, 0.0]
    assert percentile_ranks([7.0]) == [100.0]
    assert percentile_ranks([]) == []


def test_frozen_quantiles_place_a_rescored_value_on_the_population():
    quantiles = population_quantiles([float(value) for value in range(101)])
    assert len(quantiles) == 101
    assert percentile_from_quantiles(-5.0, quantiles) == 0.0
    assert percentile_from_quantiles(500.0, quantiles) == 100.0
    assert percentile_from_quantiles(90.5, quantiles) == pytest.approx(90.5)


@pytest.mark.parametrize(
    "percentile, previous, expected",
    [
        (96.0, None, "champion"),  # no history: raw band
        (96.0, "high_value", "high_value"),  # crossed 95 by < 2 points: stays
        (97.0, "high_value", "champion"),  # crossed by 2 points: promoted
        (94.0, "champion", "champion"),  # dipped < 2 below 95: stays
        (92.9, "champion", "high_value"),  # dipped > 2 below: demoted one tier
        (40.0, "champion", "standard"),  # large drop skips tiers
        (83.0, "standard", "high_value"),  # large rise skips tiers
        (81.0, "standard", "growth_potential"),  # high_value needs 82
    ],
)
def test_hysteresis_moves_tiers_only_past_the_margin(percentile, previous, expected):
    assert assign_tier(percentile, previous, BANDS, 2.0) == expected


def test_settings_require_a_floor_band_and_sort_bands():
    settings = ClvSettings.from_configuration(
        {
            "tier_bands": [
                {"tier": "low", "min_percentile": 0},
                {"tier": "top", "min_percentile": 90},
            ],
            "gross_margin_rate": 0.4,
        },
        reporting_currency="vnd",
    )
    assert settings.tier_bands == (TierBand("top", 90.0), TierBand("low", 0.0))
    assert settings.reporting_currency == "VND"
    assert settings.gross_margin_rate == 0.4
    assert settings.feature_keys == TRANSACTION_FEATURE_KEYS
    with pytest.raises(ValueError, match="min_percentile 0"):
        ClvSettings.from_configuration({"tier_bands": [{"tier": "top", "min_percentile": 50}]})


def test_finance_derives_contribution_clv_from_revenue():
    settings = ClvSettings(
        gross_margin_rate=0.5,
        annual_retention_rate=0.75,
        annual_cost_per_customer=100.0,
        discount_rate=0.10,
        forecast_horizon_years=3,
        acquisition_cost=500.0,
    )
    outputs = derive_outputs(2_200.0, settings)
    contribution = 2_200.0 * 0.5 - 100.0
    assert outputs.expected_annual_contribution_profit == pytest.approx(contribution)
    assert outputs.predicted_clv_12m == pytest.approx(contribution / 1.1)
    three_year = sum(contribution * 0.75 ** (t - 1) / 1.1**t for t in (1, 2, 3))
    assert outputs.predicted_clv_36m == pytest.approx(three_year)
    assert outputs.predicted_clv_horizon_after_cac == pytest.approx(three_year - 500.0)


def test_finance_without_margin_reports_revenue_only():
    outputs = derive_outputs(-3.0, ClvSettings())
    assert outputs.expected_annual_revenue == 0.0
    assert outputs.predicted_clv_12m is None
    assert outputs.predicted_clv_36m is None


def test_ranking_metrics():
    actual = [0.0, 0.0, 10.0, 0.0, 90.0]
    assert normalised_gini(actual, actual) == pytest.approx(1.0)
    assert normalised_gini(actual, [5, 4, 3, 2, 1]) < 0
    assert top_decile_capture(actual, [0, 0, 1, 0, 9]) == pytest.approx(0.9)


def test_calibration_bounds_near_zero_deciles():
    actual = [0.0] * 90 + [100.0] * 10
    predicted = [1.0] * 90 + [91.0] * 10
    result = calibration(actual, predicted)
    assert result["overall_error"] == pytest.approx(0.0)
    assert all(np.isfinite(error) for error in result["decile_errors"])
    assert calibration([0.0, 0.0], [1.0, 2.0]) == {"overall_error": None, "decile_errors": []}


def _synthetic(rows=3_000, seed=7):
    rng = np.random.default_rng(seed)
    spend = rng.gamma(1.2, 400.0, rows) * (rng.random(rows) < 0.7)
    count = np.where(spend > 0, rng.integers(1, 12, rows), 0)
    features = [
        {
            "transaction_amount_sum_365d": float(amount),
            "transaction_count_365d": int(orders),
            "transaction_avg_amount_365d": float(amount / orders) if orders else None,
            "transaction_active_days_365d": int(orders),
            "transaction_recency_days": int(rng.integers(1, 365)) if orders else None,
        }
        for amount, orders in zip(spend, count)
    ]
    labels = list(np.clip(spend * rng.normal(1.05, 0.15, rows), 0.0, None))
    return features, labels


def test_model_trains_on_log_target_and_is_calibrated_in_currency():
    features, labels = _synthetic()
    model = train_model(
        features[:2_000], labels[:2_000], TRANSACTION_FEATURE_KEYS, {"n_estimators": 80}
    )
    predictions = model.predict(features[2_000:])
    assert (predictions >= 0).all()
    assert model.calibration_scale > 1.0
    assert calibration(labels[2_000:], predictions)["overall_error"] < 0.15
    drivers = model.drivers(features[2_000:2_001])[0]
    assert drivers and set(drivers[0]) == {"feature", "contribution"}

    restored = type(model).from_bytes(
        model.to_bytes(), model.feature_keys, model.calibration_scale
    )
    assert np.allclose(restored.predict(features[2_000:2_010]), predictions[:10])


def test_promotion_gate_routes_first_champion_and_unstable_models_to_a_human():
    actual = [float(value) for value in range(1, 201)]
    good = [value * 1.02 for value in actual]
    flat = [100.0] * 200
    settings = ClvSettings(calibration_tolerance=0.15, max_tier_change_rate=0.10)

    first = promotion_gate(
        actual=actual, challenger=good, baseline=flat, champion=None, settings=settings
    )
    assert first.decision == "approval" and first.checks["first_champion"]

    worse = promotion_gate(
        actual=actual, challenger=flat, baseline=good, champion=None, settings=settings
    )
    assert worse.decision == "reject" and not worse.checks["beats_baseline"]

    unstable = promotion_gate(
        actual=actual, challenger=good, baseline=flat, champion=list(reversed(good)),
        settings=settings,
    )
    assert unstable.checks["beats_champion"]
    assert not unstable.checks["stable"]
    assert unstable.decision == "approval"

    noisier_champion = [
        value * 0.98 if index % 50 else 1.0 for index, value in enumerate(good)
    ]
    routine = promotion_gate(
        actual=actual, challenger=good, baseline=flat, champion=noisier_champion,
        settings=settings,
    )
    assert routine.decision == "promote", routine.checks


def test_training_cutoffs_leave_a_full_label_year():
    cutoffs = training_cutoffs(date(2023, 1, 15), date(2026, 11, 19))
    assert cutoffs[0] == date(2024, 1, 31)
    assert cutoffs[-1] == date(2025, 10, 31)
    assert len(cutoffs) == 22
