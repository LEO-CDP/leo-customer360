from pathlib import Path


DATABASE_ROOT = Path(__file__).resolve().parents[1]


def test_journey_reporting_is_in_canonical_schema_and_upgrade_migration():
    schema = (DATABASE_ROOT / "database-schema.sql").read_text(encoding="utf-8")
    migration = (
        DATABASE_ROOT / "migrations" / "018_journey_report.sql"
    ).read_text(encoding="utf-8")

    for sql in (schema, migration):
        assert "CREATE TABLE IF NOT EXISTS customer360.cdp_journey_metric (" in sql
        assert "CREATE TABLE IF NOT EXISTS customer360.cdp_journey_metric_daily (" in sql
        assert "CREATE OR REPLACE FUNCTION customer360.refresh_journey_metrics_pg(" in sql
        assert "CREATE OR REPLACE VIEW customer360.vw_journey_stage_report" in sql
        assert "dim_type IN ('all', 'channel', 'campaign', 'segment', 'agent')" in sql
        assert "business_model TEXT NOT NULL DEFAULT 'B2C'" in sql
        assert "applicable_business_models TEXT[] NOT NULL" in sql
        assert "ALTER TABLE customer360.cdp_journey_metric\n" not in sql
        assert "ALTER TABLE customer360.cdp_journey_metric_daily\n    ADD COLUMN" not in sql
        assert "CREATE TABLE IF NOT EXISTS customer360.cdp_journey_metric_domain" in sql
        assert "UNIQUE NULLS NOT DISTINCT (metric_code, domain_code)" in sql
        assert "domain_code VARCHAR(50)" in sql
        assert "FROM customer360.crm_campaign_agent" in sql
        assert "WHERE tenant_id = p_tenant AND is_active" in sql
        assert "app.tenant_id must match p_tenant" in sql
        assert "SELECT COALESCE(business_model, 'B2C')" in sql
        assert "cur.domain_code" in sql
        assert "domain_code IS NULL" in sql
        assert "currency TEXT NOT NULL DEFAULT ''" in sql
        assert "GROUP BY g.journey_stage, g.currency, a.agent_code" in sql
        assert "AND prev.currency = cur.currency" in sql
        assert "'campaign_spend', 'campaign_conversions', 'campaign_revenue'" in sql
        assert "WITH current_orders AS (" in sql
        assert "md.domain_code = g.domain_code" in sql
        assert "JOIN customer360.sys_domain d ON d.domain_code = p.domain AND d.is_active" in sql

        delete_start = sql.index("DELETE FROM customer360.cdp_journey_metric_daily")
        delete_end = sql.index("FOREACH w IN ARRAY", delete_start)
        delete_clause = sql[delete_start:delete_end]
        assert "domain_code IS NOT NULL" in delete_clause
        assert "AND domain_code IS NULL" not in delete_clause

    assert "'cdp_journey_metric_daily'" in schema
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "Apply migration 017_agent_campagn_journey.sql first" in migration
