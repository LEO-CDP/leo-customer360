"""CLV orchestration: training with the promotion gate, scoring and projection.

Entry points used by the Dagster jobs, the segment workflow runner and the
regression pipeline:

* ``train``             - monthly retrain of one (tenant, domain), gated (Q8)
* ``approve``           - human approval of a PENDING_APPROVAL model version
* ``score_population``  - nightly full-population score, rank, tier, project (Q9)
* ``score_profiles``    - event/segment re-score against frozen cut-offs (Q9)
* ``stale_projections`` - 72-hour staleness check (Q15)
* ``apply_retention``   - export then delete rows past retention (Q14)
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .artifacts import ArtifactStore, default_artifact_store, model_key, valuation_export_key
from .finance import derive_outputs, model_assumptions
from .model import ClvModel, promotion_gate, train_model
from .repository import ClvRepository
from .settings import AGENT_CODE, FEATURE_KEYS, FEATURE_VERSION, MODEL_NAME, ClvSettings
from .tiers import (
    assign_tier,
    percentile_from_quantiles,
    percentile_ranks,
    population_quantiles,
)

logger = logging.getLogger(__name__)

DAILY_RETENTION_DAYS = 35
MONTHLY_RETENTION_MONTHS = 36
STALE_AFTER = timedelta(hours=72)
LABEL_DAYS = 365


def _normalise(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: float(value) if isinstance(value, Decimal) else value
        for key, value in row.items()
    }


def _month_end(day: date) -> date:
    next_month = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
    return next_month - timedelta(days=1)


def _add_months(day: date, months: int) -> date:
    """Shift by whole months, clamping the day to the target month's length."""
    month_index = day.month - 1 + months
    target = date(day.year + month_index // 12, month_index % 12 + 1, 1)
    return target.replace(day=min(day.day, _month_end(target).day))


def training_cutoffs(first_transaction: date, today: date) -> list[date]:
    """Month-end cut-offs with a feature year behind and a full label year ahead."""
    cutoff = _month_end(_add_months(first_transaction, 12))
    last = today - timedelta(days=LABEL_DAYS + 1)
    cutoffs = []
    while cutoff <= last:
        cutoffs.append(cutoff)
        cutoff = _month_end(_add_months(cutoff.replace(day=1), 1))
    return cutoffs


@dataclass
class ScoredProfile:
    master_profile_id: str
    calculation_status: str
    value: float | None = None
    valuation_id: str | None = None
    clv_segment: str | None = None
    clv_percentile: float | None = None

    def as_result(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "valuation_id": self.valuation_id,
            "clv_segment": self.clv_segment,
            "clv_percentile": self.clv_percentile,
            "calculation_status": self.calculation_status,
        }


@dataclass
class ScoringSummary:
    tenant_id: str
    domain: str
    status: str
    profiles_scored: int = 0
    tiered: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class ClvService:
    def __init__(
        self,
        repository: ClvRepository | None = None,
        artifact_store_factory: Callable[[], ArtifactStore] = default_artifact_store,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.repository = repository or ClvRepository()
        self._artifact_store_factory = artifact_store_factory
        self._artifact_store: ArtifactStore | None = None
        self._clock = clock

    # ---- configuration -------------------------------------------------

    @property
    def artifacts(self) -> ArtifactStore:
        if self._artifact_store is None:
            self._artifact_store = self._artifact_store_factory()
        return self._artifact_store

    def today(self) -> date:
        return self._clock().date()

    def agent_active(self) -> bool:
        agent = self.repository.load_agent()
        return bool(agent) and agent.get("status") == "ACTIVE"

    def settings_for(
        self, tenant_id: str, configuration: Mapping[str, Any] | None = None
    ) -> ClvSettings:
        agent = self.repository.load_agent() or {}
        merged = dict(agent.get("hyperparameters") or {})
        merged.update(configuration or {})
        return ClvSettings.from_configuration(
            merged, reporting_currency=self.repository.tenant_currency(tenant_id)
        )

    def _load_model(self, version: Mapping[str, Any]) -> ClvModel:
        metrics = version.get("metrics") or {}
        return ClvModel.from_bytes(
            self.artifacts.get(version["artifact_uri"]),
            version.get("feature_keys") or FEATURE_KEYS,
            calibration_scale=float(metrics.get("calibration_scale", 1.0)),
        )

    # ---- training ------------------------------------------------------

    def _dataset(
        self, tenant_id: str, domain: str, cutoff: date, settings: ClvSettings
    ) -> tuple[list[dict[str, Any]], list[float]]:
        rows = [
            _normalise(row)
            for row in self.repository.load_features(tenant_id, domain, cutoff, settings)
        ]
        labels = self.repository.load_labels(
            tenant_id, domain, cutoff, settings, [row["master_profile_id"] for row in rows]
        )
        return rows, [labels.get(row["master_profile_id"], 0.0) for row in rows]

    def train(
        self,
        tenant_id: str,
        domain: str,
        configuration: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = self.settings_for(tenant_id, configuration)
        today = self.today()
        first = self.repository.first_transaction_time(tenant_id, domain, settings)
        if first is None:
            return {"status": "insufficient_history", "reason": "no transactions"}
        history_months = (today - first.date()).days / 30.4375
        if history_months < settings.min_history_months:
            return {
                "status": "insufficient_history",
                "history_months": round(history_months, 1),
                "required_months": settings.min_history_months,
            }
        cutoffs = training_cutoffs(first.date(), today)
        if len(cutoffs) < 2:
            return {"status": "insufficient_history", "cutoffs": [str(c) for c in cutoffs]}

        feature_keys = settings.feature_keys
        train_rows: list[dict[str, Any]] = []
        train_labels: list[float] = []
        for cutoff in cutoffs[:-1]:
            rows, labels = self._dataset(tenant_id, domain, cutoff, settings)
            train_rows.extend(rows)
            train_labels.extend(labels)
        holdout_rows, holdout_labels = self._dataset(tenant_id, domain, cutoffs[-1], settings)
        if not train_rows or not holdout_rows:
            return {"status": "insufficient_history", "reason": "empty training or holdout set"}

        challenger = train_model(train_rows, train_labels, feature_keys, settings.lightgbm_params)
        champion_version = self.repository.get_champion(tenant_id, domain)
        champion_predictions = None
        if champion_version is not None:
            champion_predictions = list(self._load_model(champion_version).predict(holdout_rows))
        gate = promotion_gate(
            actual=holdout_labels,
            challenger=list(challenger.predict(holdout_rows)),
            baseline=[
                max(0.0, row.get("transaction_amount_sum_365d") or 0.0) for row in holdout_rows
            ],
            champion=champion_predictions,
            settings=settings,
        )

        model_version = f"{today:%Y%m%d}-{uuid.uuid4().hex[:8]}"
        artifact_uri = self.artifacts.put(
            model_key(tenant_id, domain, AGENT_CODE, model_version), challenger.to_bytes()
        )
        status = {"promote": "CHALLENGER", "approval": "PENDING_APPROVAL"}.get(
            gate.decision, "REJECTED"
        )
        model_version_id = self.repository.insert_model_version(
            tenant_id,
            {
                "domain_code": domain,
                "agent_code": AGENT_CODE,
                "model_name": MODEL_NAME,
                "model_version": model_version,
                "artifact_uri": artifact_uri,
                "feature_version": FEATURE_VERSION,
                "feature_keys": list(feature_keys),
                "training_window": {
                    "train_cutoffs": [str(cutoff) for cutoff in cutoffs[:-1]],
                    "holdout_cutoff": str(cutoffs[-1]),
                    "train_rows": len(train_rows),
                    "holdout_rows": len(holdout_rows),
                },
                "label_definition": {
                    "label": "net_realised_revenue_365d",
                    "window_days": LABEL_DAYS,
                    "target_transform": "log1p",
                    "reporting_currency": settings.reporting_currency,
                    "exclude_status": list(settings.exclude_status),
                    "refund_types": list(settings.refund_types),
                },
                "metrics": {"calibration_scale": challenger.calibration_scale, **gate.checks},
                "gate_result": gate.as_dict(),
                "status": status,
            },
        )
        summary: dict[str, Any] = {
            "status": status,
            "decision": gate.decision,
            "model_version": model_version,
            "model_version_id": model_version_id,
        }
        if gate.decision == "promote":
            self.repository.promote(tenant_id, model_version_id, "system")
            summary["status"] = "CHAMPION"
            summary["scoring"] = self.score_population(tenant_id, domain, configuration).details
        return summary

    def approve(self, tenant_id: str, model_version_id: str, approved_by: str) -> dict[str, Any]:
        version = self.repository.get_model_version(tenant_id, model_version_id)
        if version is None:
            raise ValueError(f"Unknown model version {model_version_id}")
        if version["status"] != "PENDING_APPROVAL":
            raise ValueError(
                f"Model version {model_version_id} is {version['status']}, not PENDING_APPROVAL"
            )
        self.repository.promote(tenant_id, model_version_id, approved_by)
        scoring = self.score_population(tenant_id, version["domain_code"])
        return {"status": "CHAMPION", "scoring": scoring.details}

    # ---- scoring -------------------------------------------------------

    def _valuation_row(
        self,
        *,
        tenant_id: str,
        domain: str,
        as_of: date,
        champion: Mapping[str, Any],
        settings: ClvSettings,
        features: Mapping[str, Any],
        prediction: float,
        drivers: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        outputs = derive_outputs(prediction, settings)
        total = features.get("total_amount_365d") or 0.0
        excluded = features.get("excluded_amount_365d") or 0.0
        excluded_share = excluded / total if total > 0 else 0.0
        first = features.get("first_transaction_time")
        as_of_end = datetime.combine(as_of + timedelta(days=1), datetime.min.time())
        average = features.get("transaction_avg_amount_365d")
        return {
            "tenant_id": tenant_id,
            "master_profile_id": features["master_profile_id"],
            "domain_code": domain,
            "as_of_date": as_of,
            "currency": settings.reporting_currency,
            "model_name": MODEL_NAME,
            "model_version": champion["model_version"],
            "feature_version": FEATURE_VERSION,
            "model_version_id": champion["model_version_id"],
            "days_since_last_transaction": features.get("transaction_recency_days"),
            "customer_tenure_days": None if first is None else (as_of_end - first).days,
            "active_months_12m": features.get("active_months_12m"),
            "transactions_30d": features.get("transactions_30d"),
            "transactions_90d": features.get("transactions_90d"),
            "transactions_12m": features.get("transaction_count_365d"),
            "historical_revenue_12m": max(0.0, features.get("transaction_amount_sum_365d") or 0.0),
            "average_transaction_value_12m": None if average is None else max(0.0, average),
            "gross_margin_rate": settings.gross_margin_rate,
            "discount_rate": settings.discount_rate,
            "forecast_horizon_years": settings.forecast_horizon_years,
            "expected_annual_revenue": outputs.expected_annual_revenue,
            "expected_annual_contribution_profit": outputs.expected_annual_contribution_profit,
            "model_assumptions": model_assumptions(settings),
            "feature_snapshot": {
                "features": {
                    key: features.get(key)
                    for key in champion.get("feature_keys") or FEATURE_KEYS
                },
                "event_features_enabled": settings.event_features_enabled,
                "excluded_currency_share": round(excluded_share, 6),
                "excluded_amount_365d": excluded,
                "fx_conversion_recommended": excluded_share > settings.fx_exclusion_threshold,
            },
            "predicted_clv_12m": outputs.predicted_clv_12m,
            "predicted_clv_36m": outputs.predicted_clv_36m,
            "predicted_clv_horizon_before_cac": outputs.predicted_clv_horizon_before_cac,
            "predicted_clv_horizon_after_cac": outputs.predicted_clv_horizon_after_cac,
            "predicted_revenue_horizon": outputs.predicted_revenue_horizon,
            "predicted_contribution_profit_horizon": outputs.predicted_contribution_profit_horizon,
            "data_quality_score": round(100.0 * (1.0 - excluded_share), 2),
            "clv_segment": None,
            "explanation": {"drivers": list(drivers), "scale": "log1p_revenue"},
            "calculation_status": "COMPUTED",
            "last_error": None,
            "calculated_at": self._clock(),
        }

    @staticmethod
    def _rank_value(row: Mapping[str, Any]) -> float:
        """Rank on contribution CLV when a margin is configured, else on revenue."""
        value = row["predicted_clv_12m"]
        return row["expected_annual_revenue"] if value is None else value

    def _scored_rows(
        self,
        tenant_id: str,
        domain: str,
        as_of: date,
        champion: Mapping[str, Any],
        settings: ClvSettings,
        profile_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        features = [
            _normalise(row)
            for row in self.repository.load_features(
                tenant_id, domain, as_of, settings, profile_ids
            )
        ]
        if not features:
            return []
        model = self._load_model(champion)
        return [
            self._valuation_row(
                tenant_id=tenant_id, domain=domain, as_of=as_of, champion=champion,
                settings=settings, features=feature, prediction=float(prediction),
                drivers=driver,
            )
            for feature, prediction, driver in zip(
                features, model.predict(features), model.drivers(features)
            )
        ]

    def score_population(
        self,
        tenant_id: str,
        domain: str,
        configuration: Mapping[str, Any] | None = None,
        as_of: date | None = None,
    ) -> ScoringSummary:
        settings = self.settings_for(tenant_id, configuration)
        champion = self.repository.get_champion(tenant_id, domain)
        if champion is None:
            return ScoringSummary(tenant_id, domain, "no_champion")
        as_of = as_of or self.today() - timedelta(days=1)
        self.repository.ensure_partitions(as_of, as_of)
        rows = self._scored_rows(tenant_id, domain, as_of, champion, settings)
        if not rows:
            return ScoringSummary(tenant_id, domain, "no_profiles")

        rank_values = [self._rank_value(row) for row in rows]
        tiered = len(rows) >= settings.min_population
        percentiles: list[float | None] = [None] * len(rows)
        if tiered:
            percentiles = list(percentile_ranks(rank_values))
            current = self.repository.current_tiers(
                tenant_id, [row["master_profile_id"] for row in rows]
            )
            for row, percentile in zip(rows, percentiles):
                previous = current.get(row["master_profile_id"], {}).get("clv_segment")
                row["clv_segment"] = assign_tier(
                    percentile, previous, settings.tier_bands, settings.hysteresis_points
                )
        projections = {
            row["master_profile_id"]: {
                "predictive_clv": row["predicted_clv_12m"],
                "clv_segment": row["clv_segment"],
                "clv_percentile": percentile,
            }
            for row, percentile in zip(rows, percentiles)
        }
        self.repository.write_valuations(tenant_id, rows, projections, champion["model_version"])
        self.repository.update_model_version(
            tenant_id,
            champion["model_version_id"],
            {
                "tier_cutoffs": {
                    "as_of_date": str(as_of),
                    "population": len(rows),
                    "rank_basis": "predicted_clv_12m"
                    if settings.gross_margin_rate is not None
                    else "expected_annual_revenue",
                    "quantiles": population_quantiles(rank_values) if tiered else [],
                    "tier_bands": [
                        {"tier": band.tier, "min_percentile": band.min_percentile}
                        for band in settings.tier_bands
                    ],
                }
            },
        )
        return ScoringSummary(
            tenant_id, domain, "scored", profiles_scored=len(rows), tiered=tiered,
            details={"profiles_scored": len(rows), "tiered": tiered, "as_of_date": str(as_of)},
        )

    def score_profiles(
        self,
        tenant_id: str,
        profile_ids: Sequence[str],
        configuration: Mapping[str, Any] | None = None,
        as_of: date | None = None,
    ) -> dict[str, ScoredProfile]:
        """Re-score individual profiles, tiering them on the frozen cut-offs."""
        settings = self.settings_for(tenant_id, configuration)
        as_of = as_of or self.today()
        results = {
            str(profile_id): ScoredProfile(str(profile_id), "PROFILE_NOT_FOUND")
            for profile_id in profile_ids
        }
        domains: dict[str, list[str]] = {}
        for profile_id, domain in self.repository.profile_domains(tenant_id, list(results)).items():
            domains.setdefault(domain, []).append(profile_id)

        for domain, domain_profile_ids in domains.items():
            champion = self.repository.get_champion(tenant_id, domain)
            if champion is None:
                for profile_id in domain_profile_ids:
                    results[profile_id] = ScoredProfile(profile_id, "NO_CHAMPION")
                continue
            self.repository.ensure_partitions(as_of, as_of)
            rows = self._scored_rows(
                tenant_id, domain, as_of, champion, settings, domain_profile_ids
            )
            if not rows:
                continue
            quantiles = (champion.get("tier_cutoffs") or {}).get("quantiles") or []
            current = self.repository.current_tiers(tenant_id, domain_profile_ids)
            projections = {}
            for row in rows:
                previous = current.get(row["master_profile_id"], {})
                if quantiles:
                    percentile = percentile_from_quantiles(self._rank_value(row), quantiles)
                    row["clv_segment"] = assign_tier(
                        percentile,
                        previous.get("clv_segment"),
                        settings.tier_bands,
                        settings.hysteresis_points,
                    )
                else:
                    # No tiered population run yet: keep the projected tier.
                    percentile = previous.get("clv_percentile")
                    row["clv_segment"] = previous.get("clv_segment")
                projections[row["master_profile_id"]] = {
                    "predictive_clv": row["predicted_clv_12m"],
                    "clv_segment": row["clv_segment"],
                    "clv_percentile": percentile,
                }
            valuation_ids = self.repository.write_valuations(
                tenant_id, rows, projections, champion["model_version"]
            )
            for row in rows:
                profile_id = row["master_profile_id"]
                results[profile_id] = ScoredProfile(
                    master_profile_id=profile_id,
                    calculation_status="COMPUTED",
                    value=row["predicted_clv_12m"],
                    valuation_id=valuation_ids.get(profile_id),
                    clv_segment=row["clv_segment"],
                    clv_percentile=projections[profile_id]["clv_percentile"],
                )
        return results

    # ---- scheduled jobs ------------------------------------------------

    def _pairs(self, tenant_id: str | None = None) -> list[tuple[str, str]]:
        tenants = (
            [tenant_id]
            if tenant_id
            else [row["tenant_id"] for row in self.repository.list_tenants()]
        )
        return [
            (tenant, domain) for tenant in tenants for domain in self.repository.list_domains(tenant)
        ]

    def run_nightly(self, tenant_id: str | None = None) -> list[ScoringSummary]:
        if not self.agent_active():
            logger.info("clv_prediction agent is not ACTIVE; skipping nightly scoring")
            return []
        summaries = []
        for tenant, domain in self._pairs(tenant_id):
            try:
                summaries.append(self.score_population(tenant, domain))
            except Exception:
                logger.exception("CLV scoring failed (tenant_id=%s domain=%s)", tenant, domain)
                summaries.append(ScoringSummary(tenant, domain, "failed"))
        return summaries

    def run_training(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        if not self.agent_active():
            logger.info("clv_prediction agent is not ACTIVE; skipping training")
            return []
        results = []
        for tenant, domain in self._pairs(tenant_id):
            try:
                result = self.train(tenant, domain)
            except Exception:
                logger.exception("CLV training failed (tenant_id=%s domain=%s)", tenant, domain)
                result = {"status": "failed"}
            results.append({"tenant_id": tenant, "domain": domain, **result})
        return results

    def stale_projections(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """(tenant, domain) pairs whose CLV projection is older than 72 hours."""
        now = now or self._clock()
        stale = []
        for tenant in self.repository.list_tenants():
            for row in self.repository.latest_projection(tenant["tenant_id"]):
                last = row["last_scored_at"]
                if last is None or now - last > STALE_AFTER:
                    stale.append(
                        {
                            "tenant_id": tenant["tenant_id"],
                            "domain": row["domain"],
                            "last_scored_at": None if last is None else last.isoformat(),
                            "projected_profiles": row["projected_profiles"],
                        }
                    )
        return stale

    def apply_retention(self, tenant_id: str, batch_size: int = 5000) -> dict[str, int]:
        """Export rows past retention to Parquet under the tenant prefix, then delete."""
        today = self.today()
        daily_before = today - timedelta(days=DAILY_RETENTION_DAYS)
        monthly_before = _add_months(today.replace(day=1), -MONTHLY_RETENTION_MONTHS)
        exported = deleted = 0
        while True:
            rows = self.repository.expired_valuations(
                tenant_id, daily_before, monthly_before, batch_size
            )
            if not rows:
                break
            by_month: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                by_month.setdefault(f"{row['as_of_date']:%Y-%m}", []).append(row)
            for month, month_rows in by_month.items():
                self.artifacts.put(
                    valuation_export_key(tenant_id, month, uuid.uuid4().hex),
                    _to_parquet(month_rows),
                )
            exported += len(rows)
            batch_deleted = self.repository.delete_valuations(
                tenant_id, [(row["valuation_id"], row["as_of_date"]) for row in rows]
            )
            if batch_deleted == 0:
                raise RuntimeError(
                    f"Exported {len(rows)} expired CLV rows for tenant {tenant_id} "
                    "but deleted none; stopping to avoid re-exporting them"
                )
            deleted += batch_deleted
        return {"exported": exported, "deleted": deleted}


def _to_parquet(rows: Sequence[Mapping[str, Any]]) -> bytes:
    import polars

    def cell(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str)
        if isinstance(value, (uuid.UUID, Decimal)):
            return str(value)
        return value

    frame = polars.DataFrame(
        [{key: cell(value) for key, value in row.items()} for row in rows],
        infer_schema_length=None,
    )
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()
