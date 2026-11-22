"""Tenant-scoped PostgreSQL access for CLV snapshots, registry and projection.

Every method opens its own transaction and sets ``app.tenant_id`` so forced
row-level security applies. Snapshot writes and the master-profile projection
share one transaction (single writer, no drift).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
from typing import Any

from psycopg2.extras import RealDictCursor, execute_values

from ..runner import DB_SCHEMA, _connect
from .settings import AGENT_CODE, ClvSettings

S = DB_SCHEMA

# Features use transactions strictly before the end of as_of_date; labels use
# the following 365 days. Rows outside the reporting currency (NULL and UNK
# count as reporting currency) are excluded and measured instead.
FEATURES_SQL = f"""
    WITH profiles AS (
        SELECT mp.master_profile_id, mp.created_at
        FROM {S}.cdp_master_profiles AS mp
        WHERE mp.tenant_id = %(tenant_id)s
          AND mp.domain = %(domain)s
          AND mp.status_code = 1
          AND (%(profile_ids)s::uuid[] IS NULL
               OR mp.master_profile_id = ANY(%(profile_ids)s::uuid[]))
    ),
    tx AS (
        SELECT
            t.master_profile_id,
            t.transaction_time,
            CASE
                WHEN lower(COALESCE(t.transaction_type, '')) = ANY(%(refund_types)s::text[])
                    THEN -abs(t.amount)
                ELSE t.amount
            END AS amount,
            COALESCE(NULLIF(upper(btrim(t.currency)), ''), 'UNK')
                IN (%(currency)s, 'UNK') AS in_currency,
            t.transaction_time >= %(as_of_ts)s - INTERVAL '365 days' AS in_year
        FROM {S}.crm_transactions AS t
        JOIN profiles AS p ON p.master_profile_id = t.master_profile_id
        WHERE t.tenant_id = %(tenant_id)s
          AND t.amount IS NOT NULL
          AND t.transaction_time < %(as_of_ts)s
          AND COALESCE(lower(t.transaction_status), '') <> ALL(%(exclude_status)s::text[])
    )
    SELECT
        p.master_profile_id::text AS master_profile_id,
        COUNT(tx.amount) FILTER (WHERE tx.in_currency AND tx.in_year)
            AS transaction_count_365d,
        COALESCE(SUM(tx.amount) FILTER (WHERE tx.in_currency AND tx.in_year), 0)
            AS transaction_amount_sum_365d,
        AVG(tx.amount) FILTER (WHERE tx.in_currency AND tx.in_year)
            AS transaction_avg_amount_365d,
        COUNT(DISTINCT tx.transaction_time::date) FILTER (WHERE tx.in_currency AND tx.in_year)
            AS transaction_active_days_365d,
        EXTRACT(DAY FROM (
            %(as_of_ts)s - MAX(tx.transaction_time) FILTER (WHERE tx.in_currency)
        ))::integer AS transaction_recency_days,
        COUNT(tx.amount) FILTER (
            WHERE tx.in_currency AND tx.transaction_time >= %(as_of_ts)s - INTERVAL '30 days'
        ) AS transactions_30d,
        COUNT(tx.amount) FILTER (
            WHERE tx.in_currency AND tx.transaction_time >= %(as_of_ts)s - INTERVAL '90 days'
        ) AS transactions_90d,
        -- A 365-day window can touch 13 calendar months; the column caps at 12.
        LEAST(12, COUNT(DISTINCT date_trunc('month', tx.transaction_time))
            FILTER (WHERE tx.in_currency AND tx.in_year)) AS active_months_12m,
        MIN(tx.transaction_time) FILTER (WHERE tx.in_currency) AS first_transaction_time,
        COALESCE(SUM(abs(tx.amount)) FILTER (WHERE NOT tx.in_currency AND tx.in_year), 0)
            AS excluded_amount_365d,
        COALESCE(SUM(abs(tx.amount)) FILTER (WHERE tx.in_year), 0) AS total_amount_365d
    FROM profiles AS p
    LEFT JOIN tx ON tx.master_profile_id = p.master_profile_id
    GROUP BY p.master_profile_id
    HAVING MAX(p.created_at) < %(as_of_ts)s OR COUNT(tx.amount) > 0
    ORDER BY p.master_profile_id
"""

LABELS_SQL = f"""
    SELECT
        t.master_profile_id::text AS master_profile_id,
        GREATEST(
            COALESCE(SUM(
                CASE
                    WHEN lower(COALESCE(t.transaction_type, '')) = ANY(%(refund_types)s::text[])
                        THEN -abs(t.amount)
                    ELSE t.amount
                END
            ), 0),
            0
        ) AS label
    FROM {S}.crm_transactions AS t
    WHERE t.tenant_id = %(tenant_id)s
      AND t.master_profile_id = ANY(%(profile_ids)s::uuid[])
      AND t.amount IS NOT NULL
      AND t.transaction_time >= %(as_of_ts)s
      AND t.transaction_time < %(as_of_ts)s + INTERVAL '365 days'
      AND COALESCE(lower(t.transaction_status), '') <> ALL(%(exclude_status)s::text[])
      AND COALESCE(NULLIF(upper(btrim(t.currency)), ''), 'UNK') IN (%(currency)s, 'UNK')
    GROUP BY t.master_profile_id
"""

FIRST_TRANSACTION_SQL = f"""
    SELECT MIN(t.transaction_time) AS first_transaction_time
    FROM {S}.crm_transactions AS t
    JOIN {S}.cdp_master_profiles AS mp
      ON mp.tenant_id = t.tenant_id AND mp.master_profile_id = t.master_profile_id
    WHERE t.tenant_id = %(tenant_id)s
      AND mp.domain = %(domain)s
      AND COALESCE(NULLIF(upper(btrim(t.currency)), ''), 'UNK') IN (%(currency)s, 'UNK')
"""

VALUATION_COLUMNS = (
    "tenant_id",
    "master_profile_id",
    "domain_code",
    "as_of_date",
    "currency",
    "model_name",
    "model_version",
    "feature_version",
    "model_version_id",
    "days_since_last_transaction",
    "customer_tenure_days",
    "active_months_12m",
    "transactions_30d",
    "transactions_90d",
    "transactions_12m",
    "historical_revenue_12m",
    "average_transaction_value_12m",
    "gross_margin_rate",
    "discount_rate",
    "forecast_horizon_years",
    "expected_annual_revenue",
    "expected_annual_contribution_profit",
    "model_assumptions",
    "feature_snapshot",
    "predicted_clv_12m",
    "predicted_clv_36m",
    "predicted_clv_horizon_before_cac",
    "predicted_clv_horizon_after_cac",
    "predicted_revenue_horizon",
    "predicted_contribution_profit_horizon",
    "data_quality_score",
    "clv_segment",
    "explanation",
    "calculation_status",
    "last_error",
    "calculated_at",
)
JSON_COLUMNS = frozenset({"model_assumptions", "feature_snapshot", "explanation"})
_KEY_COLUMNS = frozenset(
    {"tenant_id", "master_profile_id", "domain_code", "as_of_date", "model_name", "model_version"}
)
UPSERT_VALUATION_SQL = f"""
    INSERT INTO {S}.cdp_customer_valuation ({", ".join(VALUATION_COLUMNS)})
    VALUES %s
    ON CONFLICT ON CONSTRAINT uq_cdp_customer_valuation_snapshot DO UPDATE SET
        {", ".join(f"{column} = EXCLUDED.{column}" for column in VALUATION_COLUMNS if column not in _KEY_COLUMNS)},
        updated_at = now()
    RETURNING valuation_id, master_profile_id
"""
PROJECT_SQL = f"""
    UPDATE {S}.cdp_master_profiles AS mp
    SET predictive_clv = round(v.predictive_clv::numeric, 2),
        clv_segment = v.clv_segment,
        clv_percentile = v.clv_percentile::numeric,
        current_valuation_id = v.valuation_id::uuid,
        model_versions = COALESCE(mp.model_versions, '{{}}'::jsonb)
            || jsonb_build_object('clv_model', v.model_version),
        scores_updated_at = now(),
        updated_at = now()
    FROM (VALUES {{placeholders}}) AS v(
        master_profile_id, valuation_id, predictive_clv, clv_segment,
        clv_percentile, model_version
    )
    WHERE mp.tenant_id = %s
      AND mp.master_profile_id = v.master_profile_id::uuid
"""
DELETE_VALUATIONS_SQL = f"""
    DELETE FROM {S}.cdp_customer_valuation AS v
    USING (VALUES {{placeholders}}) AS k(valuation_id, as_of_date)
    WHERE v.tenant_id = %s
      AND v.valuation_id = k.valuation_id::uuid
      AND v.as_of_date = k.as_of_date::date
"""


def _chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class ClvRepository:
    def __init__(self, connection_factory: Callable[[], Any] = _connect):
        self._connection_factory = connection_factory

    @contextmanager
    def _cursor(self, tenant_id: str | None = None) -> Iterator[Any]:
        with closing(self._connection_factory()) as connection:
            with connection:
                with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    if tenant_id is not None:
                        cursor.execute(
                            "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),)
                        )
                    yield cursor

    # ---- configuration -------------------------------------------------

    def load_agent(self) -> dict[str, Any] | None:
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT status, hyperparameters FROM {S}.cdp_ai_agents WHERE agent_code = %s",
                (AGENT_CODE,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_tenants(self) -> list[dict[str, Any]]:
        with self._cursor() as cursor:
            cursor.execute(
                f"""
                SELECT tenant_id::text AS tenant_id, reporting_currency
                FROM {S}.sys_tenant
                WHERE status = 'ACTIVE'
                ORDER BY tenant_id
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def tenant_currency(self, tenant_id: str) -> str:
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT reporting_currency FROM {S}.sys_tenant WHERE tenant_id = %s",
                (tenant_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Unknown tenant {tenant_id}")
            return row["reporting_currency"]

    def list_domains(self, tenant_id: str) -> list[str]:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT domain
                FROM {S}.cdp_master_profiles
                WHERE tenant_id = %s AND status_code = 1 AND domain IS NOT NULL
                ORDER BY domain
                """,
                (tenant_id,),
            )
            return [row["domain"] for row in cursor.fetchall()]

    def profile_domains(self, tenant_id: str, profile_ids: Sequence[str]) -> dict[str, str]:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                SELECT master_profile_id::text AS master_profile_id, domain
                FROM {S}.cdp_master_profiles
                WHERE tenant_id = %s
                  AND master_profile_id = ANY(%s::uuid[])
                  AND status_code = 1
                  AND domain IS NOT NULL
                """,
                (tenant_id, list(profile_ids)),
            )
            return {row["master_profile_id"]: row["domain"] for row in cursor.fetchall()}

    # ---- features and labels -------------------------------------------

    @staticmethod
    def _params(
        tenant_id: str,
        domain: str,
        as_of: date,
        settings: ClvSettings,
        profile_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "domain": domain,
            "as_of_ts": datetime.combine(as_of + timedelta(days=1), datetime.min.time()),
            "currency": settings.reporting_currency,
            "exclude_status": list(settings.exclude_status),
            "refund_types": list(settings.refund_types),
            "profile_ids": None if profile_ids is None else list(profile_ids),
        }

    def load_features(
        self,
        tenant_id: str,
        domain: str,
        as_of: date,
        settings: ClvSettings,
        profile_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                FEATURES_SQL, self._params(tenant_id, domain, as_of, settings, profile_ids)
            )
            return [dict(row) for row in cursor.fetchall()]

    def load_labels(
        self,
        tenant_id: str,
        domain: str,
        as_of: date,
        settings: ClvSettings,
        profile_ids: Sequence[str],
    ) -> dict[str, float]:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                LABELS_SQL, self._params(tenant_id, domain, as_of, settings, profile_ids)
            )
            return {row["master_profile_id"]: float(row["label"]) for row in cursor.fetchall()}

    def first_transaction_time(
        self, tenant_id: str, domain: str, settings: ClvSettings
    ) -> datetime | None:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                FIRST_TRANSACTION_SQL,
                {"tenant_id": tenant_id, "domain": domain, "currency": settings.reporting_currency},
            )
            row = cursor.fetchone()
            return row["first_transaction_time"] if row else None

    # ---- registry ------------------------------------------------------

    def get_champion(self, tenant_id: str, domain: str) -> dict[str, Any] | None:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                SELECT *, model_version_id::text AS model_version_id
                FROM {S}.cdp_model_versions
                WHERE tenant_id = %s AND domain_code = %s AND agent_code = %s
                  AND status = 'CHAMPION'
                """,
                (tenant_id, domain, AGENT_CODE),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_model_version(self, tenant_id: str, model_version_id: str) -> dict[str, Any] | None:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                SELECT *, model_version_id::text AS model_version_id
                FROM {S}.cdp_model_versions
                WHERE tenant_id = %s AND model_version_id = %s
                """,
                (tenant_id, model_version_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def insert_model_version(self, tenant_id: str, record: Mapping[str, Any]) -> str:
        columns = list(record)
        values = [
            json.dumps(value) if isinstance(value, dict) else value
            for value in record.values()
        ]
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                INSERT INTO {S}.cdp_model_versions (tenant_id, {", ".join(columns)})
                VALUES (%s, {", ".join(["%s"] * len(columns))})
                RETURNING model_version_id::text AS model_version_id
                """,
                [tenant_id, *values],
            )
            return cursor.fetchone()["model_version_id"]

    def update_model_version(
        self, tenant_id: str, model_version_id: str, changes: Mapping[str, Any]
    ) -> None:
        assignments = ", ".join(f"{column} = %s" for column in changes)
        values = [
            json.dumps(value) if isinstance(value, dict) else value
            for value in changes.values()
        ]
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                UPDATE {S}.cdp_model_versions
                SET {assignments}, updated_at = now()
                WHERE tenant_id = %s AND model_version_id = %s
                """,
                [*values, tenant_id, model_version_id],
            )

    def promote(self, tenant_id: str, model_version_id: str, promoted_by: str) -> None:
        """Retire the current champion and promote the given version atomically."""
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                UPDATE {S}.cdp_model_versions AS old
                SET status = 'RETIRED', updated_at = now()
                FROM {S}.cdp_model_versions AS new
                WHERE new.tenant_id = %s AND new.model_version_id = %s
                  AND old.tenant_id = new.tenant_id
                  AND old.domain_code = new.domain_code
                  AND old.agent_code = new.agent_code
                  AND old.status = 'CHAMPION'
                  AND old.model_version_id <> new.model_version_id
                """,
                (tenant_id, model_version_id),
            )
            cursor.execute(
                f"""
                UPDATE {S}.cdp_model_versions
                SET status = 'CHAMPION', promoted_at = now(), promoted_by = %s,
                    updated_at = now()
                WHERE tenant_id = %s AND model_version_id = %s
                  AND status IN ('CHALLENGER', 'PENDING_APPROVAL')
                """,
                (promoted_by, tenant_id, model_version_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Model version {model_version_id} is not awaiting promotion")

    # ---- scoring writes ------------------------------------------------

    def current_tiers(
        self, tenant_id: str, profile_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Current projected tier and percentile per profile (for hysteresis)."""
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                SELECT master_profile_id::text AS master_profile_id,
                       clv_segment, clv_percentile
                FROM {S}.cdp_master_profiles
                WHERE tenant_id = %s AND master_profile_id = ANY(%s::uuid[])
                """,
                (tenant_id, list(profile_ids)),
            )
            return {
                row["master_profile_id"]: {
                    "clv_segment": row["clv_segment"],
                    "clv_percentile": (
                        None if row["clv_percentile"] is None else float(row["clv_percentile"])
                    ),
                }
                for row in cursor.fetchall()
            }

    def ensure_partitions(self, start: date, end: date) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT {S}.ensure_cdp_customer_valuation_partitions(%s, %s)", (start, end)
            )

    def write_valuations(
        self,
        tenant_id: str,
        rows: Sequence[Mapping[str, Any]],
        projections: Mapping[str, Mapping[str, Any]],
        model_version: str,
        page_size: int = 500,
    ) -> dict[str, str]:
        """Upsert snapshots and project them onto master profiles in one transaction.

        ``projections`` maps master_profile_id to predictive_clv, clv_segment
        and clv_percentile; profiles absent from it keep their projection.
        Returns master_profile_id -> valuation_id.
        """
        if not rows:
            return {}
        values = [
            tuple(
                json.dumps(row.get(column) or {}) if column in JSON_COLUMNS else row.get(column)
                for column in VALUATION_COLUMNS
            )
            for row in rows
        ]
        with self._cursor(tenant_id) as cursor:
            returned = execute_values(
                cursor, UPSERT_VALUATION_SQL, values, page_size=page_size, fetch=True
            )
            valuation_ids = {
                str(row["master_profile_id"]): str(row["valuation_id"]) for row in returned
            }
            projection_values = [
                (
                    profile_id,
                    valuation_ids[profile_id],
                    projection.get("predictive_clv"),
                    projection.get("clv_segment"),
                    projection.get("clv_percentile"),
                    model_version,
                )
                for profile_id, projection in projections.items()
                if profile_id in valuation_ids
            ]
            for chunk in _chunks(projection_values, page_size):
                placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(chunk))
                cursor.execute(
                    PROJECT_SQL.replace("{placeholders}", placeholders),
                    [item for row in chunk for item in row] + [tenant_id],
                )
            return valuation_ids

    # ---- monitoring and retention --------------------------------------

    def latest_projection(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                SELECT domain, MAX(scores_updated_at) AS last_scored_at,
                       COUNT(*) AS projected_profiles
                FROM {S}.cdp_master_profiles
                WHERE tenant_id = %s AND current_valuation_id IS NOT NULL
                GROUP BY domain
                """,
                (tenant_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def expired_valuations(
        self, tenant_id: str, daily_before: date, monthly_before: date, limit: int
    ) -> list[dict[str, Any]]:
        """Rows past retention: daily (non-month-end) rows before ``daily_before``
        and any row before ``monthly_before``."""
        with self._cursor(tenant_id) as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM {S}.cdp_customer_valuation
                WHERE tenant_id = %s
                  AND (
                      as_of_date < %s
                      OR (
                          as_of_date < %s
                          AND as_of_date <> (date_trunc('month', as_of_date)
                                             + INTERVAL '1 month - 1 day')::date
                      )
                  )
                ORDER BY as_of_date, valuation_id
                LIMIT %s
                """,
                (tenant_id, monthly_before, daily_before, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_valuations(
        self, tenant_id: str, keys: Sequence[tuple[str, date]], page_size: int = 500
    ) -> int:
        deleted = 0
        with self._cursor(tenant_id) as cursor:
            for chunk in _chunks(list(keys), page_size):
                placeholders = ", ".join(["(%s, %s)"] * len(chunk))
                cursor.execute(
                    DELETE_VALUATIONS_SQL.replace("{placeholders}", placeholders),
                    [item for key in chunk for item in (str(key[0]), key[1])] + [tenant_id],
                )
                deleted += cursor.rowcount
        return deleted
