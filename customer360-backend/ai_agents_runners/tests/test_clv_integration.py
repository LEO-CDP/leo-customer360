"""End-to-end CLV lifecycle against a real Customer 360 PostgreSQL schema.

Skipped unless CLV_INTEGRATION_DSN points at a database built from
customer360-database/*.sql and a NON-superuser role (so forced RLS applies)
with SELECT/INSERT/UPDATE/DELETE on customer360 tables and EXECUTE on its
functions. The test seeds its own tenants and synthetic transactions.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

import numpy as np
import pytest

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

from ai_agents_runners.clv.artifacts import LocalArtifactStore  # noqa: E402
from ai_agents_runners.clv.repository import ClvRepository  # noqa: E402
from ai_agents_runners.clv.service import ClvService, _month_end  # noqa: E402

DSN = os.environ.get("CLV_INTEGRATION_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="CLV_INTEGRATION_DSN is not set")

PROFILES = 700
DOMAIN = "retail"
HYPERPARAMETERS = {
    "gross_margin_rate": 0.4,
    "annual_retention_rate": 0.7,
    "min_population": 500,
    "lightgbm_params": {"n_estimators": 120},
    # The synthetic holdout has ~70 rows per decile, too few for the default
    # +/-15% per-decile calibration check; the lifecycle test relaxes it and
    # asserts below that the default would have rejected this model.
    "calibration_tolerance": 1.0,
}
DEFAULT_CALIBRATION_TOLERANCE = 0.15


def _connect():
    return psycopg2.connect(DSN)


def _query(tenant_id, sql, params=None):
    connection = _connect()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        connection.close()


def _seed_tenant(tenant_id: str, profiles: int, seed: int) -> list[str]:
    rng = np.random.default_rng(seed)
    today = date.today()
    profile_ids = [str(uuid.uuid4()) for _ in range(profiles)]
    transactions = []
    for index, profile_id in enumerate(profile_ids):
        start = today - timedelta(days=int(rng.integers(200, 1_400)))
        end = min(today, start + timedelta(days=int(rng.exponential(700))))
        monthly_spend = float(rng.gamma(1.5, 400_000))
        day = start
        while day < end:
            transactions.append(
                (
                    tenant_id,
                    profile_id,
                    "refund" if rng.random() < 0.03 else "purchase",
                    "cancelled" if rng.random() < 0.05 else "completed",
                    round(monthly_spend * float(rng.uniform(0.6, 1.4)), 2),
                    "USD" if index % 50 == 0 and rng.random() < 0.3 else "VND",
                    datetime.combine(day, datetime.min.time()) + timedelta(hours=10),
                )
            )
            day += timedelta(days=int(rng.integers(20, 45)))

    connection = _connect()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO customer360.sys_tenant
                    (tenant_id, tenant_code, tenant_name, company_name, business_type,
                     reporting_currency)
                VALUES (%s, %s, 'CLV IT', 'CLV IT Co', 'retail', 'VND')
                """,
                (tenant_id, f"clv-it-{tenant_id[:8]}"),
            )
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO customer360.cdp_master_profiles
                    (tenant_id, master_profile_id, domain, created_at)
                VALUES %s
                """,
                [(tenant_id, profile_id, DOMAIN, datetime(2021, 1, 1)) for profile_id in profile_ids],
            )
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO customer360.crm_transactions
                    (tenant_id, master_profile_id, transaction_type, transaction_status,
                     amount, currency, transaction_time)
                VALUES %s
                """,
                transactions,
                page_size=2_000,
            )
    finally:
        connection.close()
    return profile_ids


@pytest.fixture(scope="module")
def tenant(tmp_path_factory):
    connection = _connect()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE customer360.cdp_ai_agents
                SET status = 'ACTIVE', hyperparameters = hyperparameters || %s::jsonb
                WHERE agent_code = 'clv_prediction'
                """,
                (psycopg2.extras.Json(HYPERPARAMETERS),),
            )
    finally:
        connection.close()
    tenant_id = str(uuid.uuid4())
    other_tenant_id = str(uuid.uuid4())
    profile_ids = _seed_tenant(tenant_id, PROFILES, seed=11)
    _seed_tenant(other_tenant_id, 20, seed=12)
    artifact_dir = tmp_path_factory.mktemp("clv")
    service = ClvService(
        repository=ClvRepository(_connect),
        artifact_store_factory=lambda: LocalArtifactStore(artifact_dir),
    )
    return {
        "tenant_id": tenant_id,
        "other_tenant_id": other_tenant_id,
        "profile_ids": profile_ids,
        "service": service,
        "artifact_dir": artifact_dir,
    }


def test_clv_lifecycle_train_gate_approve_score_rescore(tenant):
    service, tenant_id = tenant["service"], tenant["tenant_id"]

    training = service.train(tenant_id, DOMAIN)
    assert training["decision"] == "approval", training
    assert training["status"] == "PENDING_APPROVAL"
    version = service.repository.get_model_version(tenant_id, training["model_version_id"])
    checks = version["gate_result"]["checks"]
    assert checks["beats_baseline"], checks
    assert checks["challenger"]["normalised_gini"] > checks["baseline"]["normalised_gini"]
    assert max(checks["challenger"]["calibration"]["decile_errors"]) > DEFAULT_CALIBRATION_TOLERANCE
    assert version["artifact_uri"].startswith("file://")
    assert f"tenant_id={tenant_id}" in unquote(version["artifact_uri"])

    approved = service.approve(tenant_id, training["model_version_id"], "analyst@example.com")
    assert approved["status"] == "CHAMPION"
    assert approved["scoring"]["tiered"] is True

    rows = _query(
        tenant_id,
        """
        SELECT clv_segment, count(*),
               count(*) FILTER (WHERE predictive_clv IS NULL OR clv_percentile IS NULL),
               count(*) FILTER (WHERE current_valuation_id IS NULL)
        FROM customer360.cdp_master_profiles
        WHERE tenant_id = %s
        GROUP BY clv_segment
        """,
        (tenant_id,),
    )
    tiers = {row[0]: row[1] for row in rows}
    assert set(tiers) == {"champion", "high_value", "growth_potential", "standard"}
    assert sum(tiers.values()) == PROFILES
    assert 0.03 <= tiers["champion"] / PROFILES <= 0.07
    assert all(row[2] == 0 and row[3] == 0 for row in rows)

    valuation = _query(
        tenant_id,
        """
        SELECT count(*), count(DISTINCT model_version_id),
               bool_and(calculation_status = 'COMPUTED'),
               bool_and(currency = 'VND'),
               max((feature_snapshot ->> 'excluded_currency_share')::numeric),
               bool_and(predicted_clv_12m IS NOT NULL AND predicted_clv_36m IS NOT NULL),
               bool_and(jsonb_array_length(explanation -> 'drivers') > 0)
        FROM customer360.cdp_customer_valuation
        WHERE tenant_id = %s
        """,
        (tenant_id,),
    )[0]
    assert valuation[0] == PROFILES
    assert valuation[1] == 1
    assert valuation[2] and valuation[3] and valuation[5] and valuation[6]
    assert valuation[4] > 0  # USD rows were excluded and measured

    profile_id = tenant["profile_ids"][0]
    missing_id = str(uuid.uuid4())
    rescored = service.score_profiles(tenant_id, [profile_id, missing_id])
    assert rescored[profile_id].calculation_status == "COMPUTED"
    assert rescored[profile_id].clv_percentile is not None
    assert rescored[missing_id].calculation_status == "PROFILE_NOT_FOUND"
    rescored_again = service.score_profiles(tenant_id, [profile_id])
    assert rescored_again[profile_id].valuation_id == rescored[profile_id].valuation_id

    assert not [s for s in service.stale_projections() if s["tenant_id"] == tenant_id]
    later = datetime.now(timezone.utc) + timedelta(days=4)
    assert [s for s in service.stale_projections(later) if s["tenant_id"] == tenant_id]


def test_rls_keeps_tenants_apart(tenant):
    count_sql = "SELECT count(*) FROM customer360.cdp_customer_valuation"
    assert _query(tenant["other_tenant_id"], count_sql)[0][0] == 0
    assert _query("", count_sql)[0][0] == 0
    assert _query(tenant["tenant_id"], count_sql)[0][0] > 0


def test_retention_exports_then_deletes_expired_daily_rows(tenant):
    service, tenant_id = tenant["service"], tenant["tenant_id"]
    old_day = date.today() - timedelta(days=60)
    if old_day == _month_end(old_day):
        old_day -= timedelta(days=1)  # keep it a daily (non-month-end) row
    service.score_population(tenant_id, DOMAIN, as_of=old_day)

    result = service.apply_retention(tenant_id)

    assert result == {"exported": PROFILES, "deleted": PROFILES}
    remaining = _query(
        tenant_id,
        "SELECT count(*) FROM customer360.cdp_customer_valuation WHERE as_of_date = %s",
        (old_day,),
    )[0][0]
    assert remaining == 0
    exports = list(tenant["artifact_dir"].rglob("*.parquet"))
    assert exports and all(f"tenant_id={tenant_id}" in str(path) for path in exports)
