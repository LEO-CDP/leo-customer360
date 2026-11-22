"""LightGBM CLV revenue model, holdout metrics and the promotion gate."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .settings import ClvSettings
from .tiers import assign_tier, percentile_ranks, tier_change_rate

DEFAULT_LIGHTGBM_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "n_estimators": 300,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.9,
    "random_state": 42,
    "verbose": -1,
}
TOP_DRIVERS = 5


def _matrix(rows: Sequence[Mapping[str, Any]], feature_keys: Sequence[str]) -> np.ndarray:
    return np.array(
        [
            [np.nan if row.get(key) is None else float(row[key]) for key in feature_keys]
            for row in rows
        ],
        dtype=float,
    )


@dataclass
class ClvModel:
    """A trained revenue model; predictions are in reporting currency, >= 0."""

    booster: Any
    feature_keys: tuple[str, ...]
    # expm1() of a log-scale prediction estimates the median, not the mean, so
    # predicted revenue is scaled to match actual revenue on the training rows.
    # A ratio of sums stays stable on zero-inflated revenue, unlike smearing.
    calibration_scale: float = 1.0

    def predict(self, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        if not rows:
            return np.array([], dtype=float)
        raw = self.booster.predict(_matrix(rows, self.feature_keys))
        return np.clip(np.expm1(raw), 0.0, None) * self.calibration_scale

    def drivers(self, rows: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
        """Top SHAP contributions (log-revenue scale) per row, largest first."""
        if not rows:
            return []
        contributions = self.booster.predict(
            _matrix(rows, self.feature_keys), pred_contrib=True
        )
        explanations = []
        for row_contributions in contributions:
            pairs = sorted(
                zip(self.feature_keys, row_contributions[:-1]),
                key=lambda pair: abs(pair[1]),
                reverse=True,
            )[:TOP_DRIVERS]
            explanations.append(
                [
                    {"feature": key, "contribution": round(float(value), 6)}
                    for key, value in pairs
                ]
            )
        return explanations

    def to_bytes(self) -> bytes:
        return self.booster.model_to_string().encode("utf-8")

    @classmethod
    def from_bytes(
        cls, payload: bytes, feature_keys: Sequence[str], calibration_scale: float = 1.0
    ) -> ClvModel:
        import lightgbm

        booster = lightgbm.Booster(model_str=payload.decode("utf-8"))
        return cls(
            booster=booster,
            feature_keys=tuple(feature_keys),
            calibration_scale=calibration_scale,
        )


def train_model(
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[float],
    feature_keys: Sequence[str],
    params: Mapping[str, Any] | None = None,
) -> ClvModel:
    import lightgbm

    if len(rows) != len(labels):
        raise ValueError("rows and labels must have the same length")
    if not rows:
        raise ValueError("cannot train a CLV model without rows")
    target = np.log1p(np.clip(np.asarray(labels, dtype=float), 0.0, None))
    estimator = lightgbm.LGBMRegressor(**{**DEFAULT_LIGHTGBM_PARAMS, **dict(params or {})})
    matrix = _matrix(rows, feature_keys)
    estimator.fit(matrix, target)
    fitted = np.clip(np.expm1(estimator.booster_.predict(matrix)), 0.0, None).sum()
    actual = np.clip(np.asarray(labels, dtype=float), 0.0, None).sum()
    scale = float(actual / fitted) if fitted > 0 and actual > 0 else 1.0
    return ClvModel(
        booster=estimator.booster_,
        feature_keys=tuple(feature_keys),
        calibration_scale=scale,
    )


def normalised_gini(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Gini of the ranking by prediction, divided by the Gini of a perfect ranking."""

    def gini(values: np.ndarray, scores: np.ndarray) -> float:
        order = np.lexsort((np.arange(len(scores)), -scores))
        ordered = values[order]
        total = ordered.sum()
        if total <= 0:
            return 0.0
        cumulative = np.cumsum(ordered) / total
        return float(cumulative.sum() / len(ordered) - (len(ordered) + 1) / (2 * len(ordered)))

    actual_array = np.asarray(actual, dtype=float)
    perfect = gini(actual_array, actual_array)
    if perfect == 0:
        return 0.0
    return gini(actual_array, np.asarray(predicted, dtype=float)) / perfect


def top_decile_capture(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Share of total actual revenue held by the top 10% of predictions."""
    actual_array = np.asarray(actual, dtype=float)
    total = actual_array.sum()
    if total <= 0 or len(actual_array) == 0:
        return 0.0
    count = max(1, math.ceil(len(actual_array) * 0.1))
    top = np.argsort(-np.asarray(predicted, dtype=float), kind="stable")[:count]
    return float(actual_array[top].sum() / total)


def calibration(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, Any]:
    """Overall and per-decile errors between predicted and actual revenue.

    A decile's error is measured against at least an average decile's revenue,
    so near-zero deciles (non-buyers) don't produce unbounded relative errors.
    Errors are None when there is no actual revenue to compare against.
    """
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    total_actual = float(actual_array.sum())
    if total_actual <= 0:
        return {"overall_error": None, "decile_errors": []}

    order = np.argsort(predicted_array, kind="stable")
    chunks = [chunk for chunk in np.array_split(order, 10) if len(chunk)]
    floor = total_actual / len(chunks)
    deciles = [
        abs(float(predicted_array[chunk].sum()) - float(actual_array[chunk].sum()))
        / max(float(actual_array[chunk].sum()), floor)
        for chunk in chunks
    ]
    return {
        "overall_error": abs(float(predicted_array.sum()) - total_actual) / total_actual,
        "decile_errors": deciles,
    }


def evaluate(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, Any]:
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    return {
        "rows": int(len(actual_array)),
        "normalised_gini": normalised_gini(actual_array, predicted_array),
        "top_decile_capture": top_decile_capture(actual_array, predicted_array),
        "mae": float(np.mean(np.abs(actual_array - predicted_array))) if len(actual_array) else 0.0,
        "calibration": calibration(actual_array, predicted_array),
    }


def tiers_for(predictions: Sequence[float], settings: ClvSettings) -> list[str]:
    return [
        assign_tier(percentile, None, settings.tier_bands, 0.0)
        for percentile in percentile_ranks(list(predictions))
    ]


@dataclass
class GateResult:
    passed: bool
    decision: str
    checks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "decision": self.decision, "checks": self.checks}


def promotion_gate(
    *,
    actual: Sequence[float],
    challenger: Sequence[float],
    baseline: Sequence[float],
    champion: Sequence[float] | None,
    settings: ClvSettings,
) -> GateResult:
    """Apply the Q8 gate on the time holdout.

    decision is "promote" (all checks pass), "approval" (first ML champion, or
    only the stability check failed) or "reject".
    """
    challenger_metrics = evaluate(actual, challenger)
    baseline_metrics = evaluate(actual, baseline)
    tolerance = settings.calibration_tolerance

    def within(error: float | None) -> bool:
        return error is None or error <= tolerance

    checks: dict[str, Any] = {
        "challenger": challenger_metrics,
        "baseline": baseline_metrics,
        # Ranking drives tiers, so the baseline must be beaten on ranking; money
        # accuracy is judged separately by the calibration check.
        "beats_baseline": (
            challenger_metrics["normalised_gini"] > baseline_metrics["normalised_gini"]
            and challenger_metrics["top_decile_capture"] >= baseline_metrics["top_decile_capture"]
        ),
        "calibrated": within(challenger_metrics["calibration"]["overall_error"])
        and all(within(error) for error in challenger_metrics["calibration"]["decile_errors"]),
    }
    if champion is None:
        checks["beats_champion"] = True
        checks["stable"] = True
        checks["first_champion"] = True
    else:
        champion_metrics = evaluate(actual, champion)
        checks["champion"] = champion_metrics
        checks["beats_champion"] = (
            challenger_metrics["normalised_gini"] > champion_metrics["normalised_gini"]
            and challenger_metrics["top_decile_capture"] >= champion_metrics["top_decile_capture"]
        )
        change = tier_change_rate(tiers_for(champion, settings), tiers_for(challenger, settings))
        checks["tier_change_rate"] = change
        checks["stable"] = change <= settings.max_tier_change_rate
        checks["first_champion"] = False

    quality = checks["beats_baseline"] and checks["calibrated"] and checks["beats_champion"]
    if not quality:
        return GateResult(False, "reject", checks)
    if checks["first_champion"] or not checks["stable"]:
        return GateResult(False, "approval", checks)
    return GateResult(True, "promote", checks)
