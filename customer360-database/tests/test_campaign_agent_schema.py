from pathlib import Path


DATABASE_ROOT = Path(__file__).resolve().parents[1]


def test_campaign_to_ai_agents_contract_is_in_canonical_schema_and_migration():
    schema = (DATABASE_ROOT / "database-schema.sql").read_text(encoding="utf-8")
    migration = (
        DATABASE_ROOT / "migrations" / "017_agent_campagn_journey.sql"
    ).read_text(encoding="utf-8")

    for sql in (schema, migration):
        assert "chk_cdp_ai_agents_journey_stage" in sql
        assert "chk_crm_campaign_journey_stage" in sql
        for stage in (
            "awareness",
            "acquisition",
            "activation",
            "engagement",
            "conversion",
            "retention",
            "loyalty",
            "churned",
        ):
            assert f"'{stage}'" in sql
        assert "'ai_decide', 'ai_generate', 'ai_recommend'" in sql
        assert "GENERATED ALWAYS AS (config ->> 'agent_code') STORED" in sql
        assert "crm_flow_node_agent_fk" in sql
        assert "CREATE TABLE IF NOT EXISTS customer360.crm_campaign_agent" in sql
        assert "fk_crm_campaign_agent_tenant_campaign" in sql
        assert "UNIQUE (campaign_id, agent_code, role)" in sql
        assert "guardrails JSONB NOT NULL DEFAULT '{}'::jsonb" in sql
        assert "idx_crm_campaign_agent_agent" in sql

    assert "'crm_campaign_agent'" in schema
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration


def test_legacy_and_invalid_profile_lifecycle_values_are_normalized_before_constraint():
    migration = (
        DATABASE_ROOT / "migrations" / "017_agent_campagn_journey.sql"
    ).read_text(encoding="utf-8")
    loop_start = migration.index("FOR current_tenant_id IN")
    constraint_start = migration.index(
        "ADD CONSTRAINT cdp_master_profiles_lifecycle_stage_check"
    )
    normalization = migration[loop_start:constraint_start]

    assert "set_config('app.tenant_id', current_tenant_id::text, true)" in normalization
    assert "WHERE tenant_id = current_tenant_id" in normalization
    assert "set_config('app.tenant_id', '', true)" in normalization
    for legacy_stage in ("prospect", "lead", "customer", "vip", "dormant", "churn_risk"):
        assert f"WHEN '{legacy_stage}' THEN" in normalization
    assert "ELSE NULL" in normalization
    assert "AND lifecycle_stage IS NOT NULL" in normalization
