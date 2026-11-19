"""Typed CLV settings parsed from the clv_prediction agent hyperparameters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

AGENT_CODE = "clv_prediction"
MODEL_NAME = "lightgbm.LGBMRegressor"
FEATURE_VERSION = "2.0.0"
FEATURE_SNAPSHOT_MODEL_VERSION = f"features-{FEATURE_VERSION}"

# Point-in-time transaction features, keyed by cdp_ai_feature_catalog entry.
TRANSACTION_FEATURE_KEYS = (
    "transaction_count_365d",
    "transaction_amount_sum_365d",
    "transaction_avg_amount_365d",
    "transaction_active_days_365d",
    "transaction_recency_days",
)
EVENT_FEATURE_KEYS = (
    "event_purchase_count_365d",
    "event_campaign_touch_count_365d",
)
FEATURE_KEYS = TRANSACTION_FEATURE_KEYS + EVENT_FEATURE_KEYS


@dataclass(frozen=True)
class TierBand:
    """A percentile tier: profiles at or above min_percentile belong to it."""

    tier: str
    min_percentile: float


DEFAULT_TIER_BANDS = (
    TierBand("champion", 95.0),
    TierBand("high_value", 80.0),
    TierBand("growth_potential", 50.0),
    TierBand("standard", 0.0),
)


@dataclass(frozen=True)
class ClvSettings:
    """Effective CLV configuration for one (tenant, domain)."""

    reporting_currency: str = "VND"
    exclude_status: tuple[str, ...] = ("cancelled", "failed", "refunded")
    refund_types: tuple[str, ...] = ("refund", "return")
    event_features_enabled: bool = False
    min_history_months: int = 24
    tier_bands: tuple[TierBand, ...] = DEFAULT_TIER_BANDS
    min_population: int = 500
    hysteresis_points: float = 2.0
    max_tier_change_rate: float = 0.10
    calibration_tolerance: float = 0.15
    fx_exclusion_threshold: float = 0.02
    discount_rate: float = 0.10
    forecast_horizon_years: int = 5
    gross_margin_rate: float | None = None
    annual_retention_rate: float | None = None
    annual_cost_per_customer: float | None = None
    acquisition_cost: float | None = None
    lightgbm_params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_configuration(
        cls,
        configuration: Mapping[str, Any] | None,
        *,
        reporting_currency: str | None = None,
    ) -> ClvSettings:
        config = dict(configuration or {})
        defaults = cls()
        bands = config.get("tier_bands")
        tier_bands = (
            tuple(
                TierBand(str(band["tier"]), float(band["min_percentile"]))
                for band in bands
            )
            if isinstance(bands, list) and bands
            else defaults.tier_bands
        )
        tier_bands = tuple(sorted(tier_bands, key=lambda band: -band.min_percentile))
        if tier_bands[-1].min_percentile != 0:
            raise ValueError("tier_bands must include a band with min_percentile 0")

        def optional_float(key: str) -> float | None:
            value = config.get(key)
            return None if value is None else float(value)

        return cls(
            reporting_currency=(reporting_currency or defaults.reporting_currency).upper(),
            exclude_status=tuple(
                str(value).lower()
                for value in config.get("exclude_status", defaults.exclude_status)
            ),
            refund_types=tuple(
                str(value).lower()
                for value in config.get("refund_types", defaults.refund_types)
            ),
            event_features_enabled=bool(
                config.get("event_features_enabled", defaults.event_features_enabled)
            ),
            min_history_months=int(
                config.get("min_history_months", defaults.min_history_months)
            ),
            tier_bands=tier_bands,
            min_population=int(config.get("min_population", defaults.min_population)),
            hysteresis_points=float(
                config.get("hysteresis_points", defaults.hysteresis_points)
            ),
            max_tier_change_rate=float(
                config.get("max_tier_change_rate", defaults.max_tier_change_rate)
            ),
            calibration_tolerance=float(
                config.get("calibration_tolerance", defaults.calibration_tolerance)
            ),
            fx_exclusion_threshold=float(
                config.get("fx_exclusion_threshold", defaults.fx_exclusion_threshold)
            ),
            discount_rate=float(config.get("discount_rate", defaults.discount_rate)),
            forecast_horizon_years=int(
                config.get("forecast_horizon_years", defaults.forecast_horizon_years)
            ),
            gross_margin_rate=optional_float("gross_margin_rate"),
            annual_retention_rate=optional_float("annual_retention_rate"),
            annual_cost_per_customer=optional_float("annual_cost_per_customer"),
            acquisition_cost=optional_float("acquisition_cost"),
            lightgbm_params=dict(config.get("lightgbm_params") or {}),
        )

    @property
    def feature_keys(self) -> tuple[str, ...]:
        return FEATURE_KEYS if self.event_features_enabled else TRANSACTION_FEATURE_KEYS
