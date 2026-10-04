\set ON_ERROR_STOP on
-- Run only on a disposable database after schema, AI seed and core seed.
CREATE FUNCTION pg_temp.assert_true(actual BOOLEAN, message TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF actual IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'Assertion failed: %', message;
    END IF;
END;
$$;

SELECT pg_temp.assert_true(
    (SELECT count(*) = 14 AND bool_and(status = 'INACTIVE')
     FROM customer360.cdp_ai_agents),
    'Fresh bootstrap has 12 core templates and two inactive service prompt agents');
SELECT pg_temp.assert_true(
    NOT EXISTS (
        SELECT 1 FROM customer360.cdp_profile_attributes attribute
        LEFT JOIN customer360.cdp_ai_agents agent ON agent.agent_code = attribute.agent_code
        WHERE attribute.agent_code IS NOT NULL AND agent.agent_code IS NULL
    ),
    'Core attribute ownership foreign keys resolve');
SELECT pg_temp.assert_true(
    (SELECT count(*) = 3 FROM customer360.cdp_ai_agents
     WHERE prompt_key IN ('campaign.plan.instructions', 'campaign.zns.instructions',
                          'segment.nl_to_rules.instructions')),
    'All agent-service prompt keys are present');
SELECT pg_temp.assert_true(
    (SELECT bool_and(
        jsonb_array_length(prompt_versions) = 1
        AND prompt_versions -> 0 ->> 'body' = system_instructions
        AND (prompt_versions -> 0 ->> 'version')::integer = instruction_version
        AND prompt_versions -> 0 -> 'required_vars' = to_jsonb(required_variables)
    ) FROM customer360.cdp_ai_agents WHERE prompt_key IS NOT NULL),
    'Published prompt state and initial history agree');

CREATE TEMP TABLE saved_agents AS TABLE customer360.cdp_ai_agents;
CREATE TEMP TABLE saved_features AS TABLE customer360.cdp_ai_feature_catalog;

UPDATE customer360.cdp_ai_agents
SET status = 'ACTIVE', model_name = 'approved-deployment',
    hyperparameters = '{"approved": true}', instruction_version = 2,
    system_instructions = 'Reviewed production instructions',
    prompt_versions = prompt_versions || jsonb_build_array(jsonb_build_object(
        'version', 2, 'body', 'Reviewed production instructions',
        'required_vars', required_variables, 'created_at', now(),
        'created_by', 'operator', 'note', 'Reviewed deployment'
    ))
WHERE agent_code = 'lead_scoring';
UPDATE customer360.cdp_ai_feature_catalog
SET description = 'Operator-reviewed feature definition'
WHERE feature_key = 'profile_engagement_score';
CREATE TEMP TABLE deployed_agents AS TABLE customer360.cdp_ai_agents;
CREATE TEMP TABLE deployed_features AS TABLE customer360.cdp_ai_feature_catalog;

\ir ../init-cdp-ai-agents.sql

SELECT pg_temp.assert_true(
    NOT EXISTS (
        SELECT 1 FROM customer360.cdp_ai_agents actual
        FULL JOIN deployed_agents expected USING (agent_code)
        WHERE to_jsonb(actual) IS DISTINCT FROM to_jsonb(expected)
    ), 'Rerunning bootstrap preserves every deployed agent field and prompt history');
SELECT pg_temp.assert_true(
    NOT EXISTS (
        SELECT 1 FROM customer360.cdp_ai_feature_catalog actual
        FULL JOIN deployed_features expected USING (feature_key)
        WHERE to_jsonb(actual) IS DISTINCT FROM to_jsonb(expected)
    ), 'Rerunning bootstrap preserves feature definitions and timestamps');

BEGIN;
CREATE TEMP TABLE profile_fixture (LIKE customer360.cdp_master_profiles INCLUDING DEFAULTS);
INSERT INTO profile_fixture (
    tenant_id, master_profile_id, external_ids, communication_preferences
) VALUES (
    '11111111-1111-1111-1111-111111111111',
    'aaaaaaaa-0000-0000-0000-000000000001',
    '{"crm": "123", "web": "456"}',
    '{"email_opt_in": false, "sms_opt_in": "true", "push_opt_in": true}'
);
CREATE TEMP TABLE event_fixture (
    tenant_id UUID, master_profile_id UUID, event_time TIMESTAMPTZ,
    event_name TEXT, channel TEXT, session_id TEXT, campaign_id UUID,
    utm_campaign TEXT, payload JSONB
);
INSERT INTO event_fixture (tenant_id, master_profile_id, event_time, event_name)
VALUES
    ('11111111-1111-1111-1111-111111111111',
     'aaaaaaaa-0000-0000-0000-000000000001', '2026-01-30Z', 'kyc-completed'),
    ('11111111-1111-1111-1111-111111111111',
     'aaaaaaaa-0000-0000-0000-000000000001', '2026-02-02Z', 'kyc-completed'),
    ('bbbbbbbb-0000-0000-0000-000000000001',
     'aaaaaaaa-0000-0000-0000-000000000001', '2026-01-30Z', 'purchase');
CREATE TEMP TABLE transaction_fixture (LIKE customer360.crm_transactions);
CREATE TEMP TABLE contact_fixture (LIKE customer360.crm_customer_contacts);
CREATE TEMP TABLE candidate_fixture (LIKE customer360.cdp_content_items);
ALTER TABLE candidate_fixture ADD COLUMN action_id TEXT,
    ADD COLUMN channel TEXT, ADD COLUMN propensity_score NUMERIC,
    ADD COLUMN expected_value NUMERIC, ADD COLUMN frequency_cap_remaining INTEGER;
CREATE TEMP TABLE edge_fixture (
    tenant_id UUID, master_profile_id UUID, other_master_profile_id UUID,
    edge_type TEXT, match_score NUMERIC
);
CREATE TEMP TABLE series_fixture (tenant_id UUID, day DATE);
CREATE TEMP TABLE segment_fixture (
    tenant_id UUID, master_profile_id UUID, computed_at TIMESTAMPTZ
);
CREATE TEMP TABLE context_fixture (
    treatment_flag BOOLEAN, treatment_at TIMESTAMPTZ, variant_id TEXT,
    suppression_active BOOLEAN, suppression_channel TEXT,
    suppression_expires_at TIMESTAMPTZ, business_rule_context JSONB,
    target_segment JSONB, objective TEXT, budget NUMERIC,
    time_constraints JSONB, candidate_content_item_ids JSONB,
    customer_insights JSONB, recommended_actions JSONB
);

INSERT INTO customer360.sys_tenant (
    tenant_id, tenant_code, tenant_name, company_name, business_type
) VALUES (
    'bbbbbbbb-0000-0000-0000-000000000001',
    'seed_contract_other', 'Other', 'Other', 'test'
);
INSERT INTO customer360.crm_campaign (tenant_id, name, status, start_date, end_date)
VALUES
    ('11111111-1111-1111-1111-111111111111',
     'Seed contract campaign', 'Running', '2026-01-29', '2026-02-01'),
    ('bbbbbbbb-0000-0000-0000-000000000001',
     'Other tenant campaign', 'Running', '2026-01-01', NULL);
INSERT INTO series_fixture (tenant_id, day)
VALUES
    ('11111111-1111-1111-1111-111111111111', '2026-01-28'),
    ('11111111-1111-1111-1111-111111111111', '2026-01-30'),
    ('11111111-1111-1111-1111-111111111111', '2026-02-03');

-- Execute each trusted seed fragment against its documented source aliases.
DO $$
DECLARE
    feature RECORD;
    source_sql TEXT;
    result JSONB;
BEGIN
    FOR feature IN
        SELECT feature_key, source_kind, sql_expression
        FROM customer360.cdp_ai_feature_catalog
        ORDER BY feature_key
    LOOP
        source_sql := CASE feature.source_kind
            WHEN 'profile' THEN 'FROM profile_fixture mp'
            WHEN 'event_log' THEN
                'FROM event_fixture events CROSS JOIN profile_fixture mp
                 WHERE events.tenant_id = mp.tenant_id
                 AND events.master_profile_id = mp.master_profile_id
                 GROUP BY mp.tenant_id, mp.master_profile_id'
            WHEN 'transaction' THEN 'FROM transaction_fixture tx'
            WHEN 'contact' THEN 'FROM contact_fixture contacts'
            WHEN 'candidate' THEN 'FROM candidate_fixture candidates'
            WHEN 'graph' THEN 'FROM edge_fixture edges'
            WHEN 'runtime' THEN 'FROM context_fixture context'
            WHEN 'aggregate' THEN
                'FROM series_fixture series CROSS JOIN transaction_fixture tx
                 CROSS JOIN event_fixture events CROSS JOIN segment_fixture sm
                 GROUP BY series.tenant_id, series.day'
        END;
        BEGIN
            EXECUTE 'SELECT to_jsonb('
                || replace(replace(feature.sql_expression, ':as_of', '$1'), ':channel', '$2')
                || ') ' || source_sql
            INTO result USING '2026-02-01Z'::timestamptz, 'EMAIL';
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'Feature % failed: %', feature.feature_key, SQLERRM;
        END;
        IF feature.feature_key = 'profile_external_id_count' THEN
            PERFORM pg_temp.assert_true(result = '2'::jsonb, 'JSONB identity count');
        ELSIF feature.feature_key IN ('profile_email_opt_in', 'profile_sms_opt_in') THEN
            PERFORM pg_temp.assert_true(result = 'false'::jsonb, 'Consent fails closed');
        ELSIF feature.feature_key = 'profile_push_opt_in' THEN
            PERFORM pg_temp.assert_true(result = 'true'::jsonb, 'Literal true grants consent');
        ELSIF feature.feature_key = 'event_count_7d' THEN
            PERFORM pg_temp.assert_true(result = '1'::jsonb, 'Future and other-tenant events excluded');
        END IF;
    END LOOP;
END;
$$;

DO $$
DECLARE
    expression TEXT;
    result JSONB;
BEGIN
    SELECT sql_expression INTO STRICT expression
    FROM customer360.cdp_ai_feature_catalog
    WHERE feature_key = 'campaign_active_flag_daily';
    EXECUTE 'SELECT jsonb_agg(flag ORDER BY day) FROM (
        SELECT series.day, (' || expression || ') AS flag FROM series_fixture series
    ) flags' INTO result;
    PERFORM pg_temp.assert_true(
        result = '[false, true, false]'::jsonb,
        'Campaign flag respects tenant isolation and the entire running interval'
    );
END;
$$;
ROLLBACK;

UPDATE customer360.cdp_ai_agents actual
SET status = original.status, model_name = original.model_name,
    hyperparameters = original.hyperparameters,
    instruction_version = original.instruction_version,
    system_instructions = original.system_instructions,
    prompt_versions = original.prompt_versions
FROM saved_agents original
WHERE actual.agent_code = original.agent_code;
UPDATE customer360.cdp_ai_feature_catalog actual
SET description = original.description
FROM saved_features original
WHERE actual.feature_key = original.feature_key;

-- A validation error must roll back all new seed rows, not partially seed them.
DELETE FROM customer360.cdp_ai_agents WHERE agent_code = 'clv_prediction';
INSERT INTO customer360.cdp_ai_agents (
    agent_code, display_name, model_type, status, input_features
) VALUES (
    'seed_contract_invalid', 'Expected validation failure',
    'rules_engine', 'INACTIVE', ARRAY['seed_contract_missing_feature']
);
\set ON_ERROR_STOP off
\ir ../init-cdp-ai-agents.sql
\set ON_ERROR_STOP on
SELECT pg_temp.assert_true(
    NOT EXISTS (SELECT 1 FROM customer360.cdp_ai_agents WHERE agent_code = 'clv_prediction'),
    'Expected missing-feature error rolls back insertion of the deleted core template');
DELETE FROM customer360.cdp_ai_agents WHERE agent_code = 'seed_contract_invalid';
\ir ../init-cdp-ai-agents.sql
