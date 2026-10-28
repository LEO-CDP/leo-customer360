\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.assert_true(actual BOOLEAN, message TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF actual IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'Assertion failed: %', message;
    END IF;
END;
$$;

CREATE FUNCTION pg_temp.expect_error(statement TEXT, expected_state TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    actual_state TEXT;
BEGIN
    BEGIN
        EXECUTE statement;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS actual_state = RETURNED_SQLSTATE;
        IF actual_state <> expected_state THEN
            RAISE EXCEPTION 'Expected SQLSTATE %, got % for %', expected_state, actual_state, statement;
        END IF;
        RETURN;
    END;
    RAISE EXCEPTION 'Expected SQLSTATE %, statement succeeded: %', expected_state, statement;
END;
$$;

INSERT INTO customer360.sys_tenant (tenant_id, tenant_code, tenant_name, company_name, business_type)
VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', 'workflow_test_a', 'A', 'A', 'test'),
    ('bbbbbbbb-0000-0000-0000-000000000001', 'workflow_test_b', 'B', 'B', 'test');

INSERT INTO customer360.cdp_ai_agents (agent_code, display_name, model_type)
VALUES ('workflow_test_churn', 'Churn', 'rules_engine'),
       ('workflow_test_offer', 'Offer', 'rules_engine'),
       ('workflow_test_email', 'Email', 'generative_llm');

INSERT INTO customer360.cdp_segments (segment_id, tenant_id, segment_tag, segment_name)
VALUES
    ('aaaaaaaa-0000-0000-0000-000000000011', 'aaaaaaaa-0000-0000-0000-000000000001', 'workflow_test_one', 'One'),
    ('aaaaaaaa-0000-0000-0000-000000000012', 'aaaaaaaa-0000-0000-0000-000000000001', 'workflow_test_two', 'Two'),
    ('bbbbbbbb-0000-0000-0000-000000000011', 'bbbbbbbb-0000-0000-0000-000000000001', 'workflow_test_other', 'Other');

INSERT INTO customer360.cdp_content_items (content_item_id, tenant_id, item_type, title)
VALUES
    ('aaaaaaaa-0000-0000-0000-000000000021', 'aaaaaaaa-0000-0000-0000-000000000001', 'product', 'Product'),
    ('aaaaaaaa-0000-0000-0000-000000000022', 'aaaaaaaa-0000-0000-0000-000000000001', 'article', 'Article'),
    ('bbbbbbbb-0000-0000-0000-000000000021', 'bbbbbbbb-0000-0000-0000-000000000001', 'product', 'Other product');

INSERT INTO customer360.cdp_agent_workflow
    (tenant_id, segment_id, agent_code, execution_order, candidate_content_item_ids, configuration)
VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000011',
     'workflow_test_churn', 1, ARRAY[]::UUID[], '{}'),
    ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000011',
     'workflow_test_offer', 2, ARRAY['aaaaaaaa-0000-0000-0000-000000000021']::UUID[], '{"top_k":5}'),
    ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000011',
     'workflow_test_email', 3, ARRAY[]::UUID[], '{}'),
    ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000012',
     'workflow_test_offer', 1, ARRAY['aaaaaaaa-0000-0000-0000-000000000022']::UUID[], '{"top_k":2}'),
    ('bbbbbbbb-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-000000000011',
     'workflow_test_offer', 1, ARRAY['bbbbbbbb-0000-0000-0000-000000000021']::UUID[], '{}');

SELECT pg_temp.assert_true(
    (SELECT array_agg(agent_code ORDER BY execution_order)::TEXT[]
     FROM customer360.cdp_agent_workflow
     WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000011')
    = ARRAY['workflow_test_churn', 'workflow_test_offer', 'workflow_test_email'],
    'Three agents have a deterministic segment queue');
SELECT pg_temp.assert_true(
    (SELECT count(*) = 3 FROM customer360.cdp_agent_workflow WHERE agent_code = 'workflow_test_offer'),
    'One global agent can serve multiple segments and tenants');
SELECT pg_temp.assert_true(
    (SELECT configuration = '{"top_k":2}'::jsonb
        AND candidate_content_item_ids = ARRAY['aaaaaaaa-0000-0000-0000-000000000022']::UUID[]
     FROM customer360.cdp_agent_workflow WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000012'),
    'Candidates and configuration are specific to each segment-agent link');

SELECT pg_temp.expect_error($sql$
    INSERT INTO customer360.cdp_agent_workflow (tenant_id, segment_id, agent_code, execution_order)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000011', 'workflow_test_churn', 4)
$sql$, '23505');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow SET execution_order = 1
    WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000011' AND agent_code = 'workflow_test_offer'
$sql$, '23505');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow SET execution_order = 0
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23514');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow SET configuration = '[]'
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23514');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow SET agent_code = 'workflow_test_missing'
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    INSERT INTO customer360.cdp_agent_workflow (tenant_id, segment_id, agent_code, execution_order)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-000000000011', 'workflow_test_churn', 2)
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow
    SET candidate_content_item_ids = ARRAY['bbbbbbbb-0000-0000-0000-000000000021']::UUID[]
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow SET candidate_content_item_ids = ARRAY[gen_random_uuid()]
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow SET candidate_content_item_ids = ARRAY[NULL]::UUID[]
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23514');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow
    SET candidate_content_item_ids = ARRAY['aaaaaaaa-0000-0000-0000-000000000021', 'aaaaaaaa-0000-0000-0000-000000000021']::UUID[]
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23514');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow
    SET candidate_content_item_ids = ARRAY[['aaaaaaaa-0000-0000-0000-000000000021', 'aaaaaaaa-0000-0000-0000-000000000022']]::UUID[]
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23514');
SELECT pg_temp.expect_error($sql$
    DELETE FROM customer360.cdp_content_items
    WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021'
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_content_items SET tenant_id = 'bbbbbbbb-0000-0000-0000-000000000001'
    WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021'
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_content_items SET content_item_id = gen_random_uuid()
    WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021'
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    DELETE FROM customer360.cdp_ai_agents WHERE agent_code = 'workflow_test_churn'
$sql$, '23503');

UPDATE customer360.cdp_content_items SET title = 'Updated product'
WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021';

SET CONSTRAINTS customer360.uq_cdp_agent_workflow_execution_order DEFERRED;
UPDATE customer360.cdp_agent_workflow SET execution_order = 3 - execution_order
WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000011' AND execution_order IN (1, 2);
SET CONSTRAINTS customer360.uq_cdp_agent_workflow_execution_order IMMEDIATE;
SELECT pg_temp.assert_true(
    (SELECT execution_order = 2 FROM customer360.cdp_agent_workflow WHERE agent_code = 'workflow_test_churn'),
    'Queue positions can be swapped atomically');

CREATE ROLE workflow_test_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA customer360 TO workflow_test_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON customer360.cdp_agent_workflow,
    customer360.cdp_content_items TO workflow_test_runtime;
SET LOCAL ROLE workflow_test_runtime;
SELECT set_config('app.tenant_id', '', true);
SELECT pg_temp.assert_true((SELECT count(*) = 0 FROM customer360.cdp_agent_workflow),
    'Missing tenant context fails closed');
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
SELECT pg_temp.assert_true((SELECT count(*) = 4 FROM customer360.cdp_agent_workflow),
    'Tenant A cannot see tenant B steps');
SELECT pg_temp.expect_error($sql$
    INSERT INTO customer360.cdp_agent_workflow (tenant_id, segment_id, agent_code, execution_order)
    VALUES ('bbbbbbbb-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-000000000011', 'workflow_test_churn', 2)
$sql$, '42501');
SELECT pg_temp.expect_error($sql$
    UPDATE customer360.cdp_agent_workflow
    SET candidate_content_item_ids = ARRAY['bbbbbbbb-0000-0000-0000-000000000021']::UUID[]
    WHERE agent_code = 'workflow_test_churn'
$sql$, '23503');
SELECT pg_temp.expect_error($sql$
    DELETE FROM customer360.cdp_content_items WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021'
$sql$, '23503');
UPDATE customer360.cdp_agent_workflow SET is_active = FALSE WHERE agent_code = 'workflow_test_churn';
SELECT pg_temp.assert_true(
    (SELECT count(*) = 2 FROM customer360.cdp_agent_workflow
     WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000011' AND is_active),
    'Disabled steps are excluded from the active queue');
UPDATE customer360.cdp_agent_workflow
SET candidate_content_item_ids = ARRAY['aaaaaaaa-0000-0000-0000-000000000022']::UUID[]
WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000011' AND agent_code = 'workflow_test_offer';
DELETE FROM customer360.cdp_content_items WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021';
SELECT pg_temp.assert_true(
    (SELECT count(*) = 0 FROM customer360.cdp_content_items
     WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021'),
    'Unlinked candidates can be deleted');

SELECT set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000001', true);
SELECT pg_temp.assert_true((SELECT count(*) = 1 FROM customer360.cdp_agent_workflow),
    'Tenant B sees only its own queue');
RESET ROLE;
DELETE FROM customer360.cdp_segments WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000012';
SELECT pg_temp.assert_true(
    (SELECT count(*) = 0 FROM customer360.cdp_agent_workflow
     WHERE segment_id = 'aaaaaaaa-0000-0000-0000-000000000012'),
    'Segment deletion cascades to its workflow steps');

ROLLBACK;

BEGIN ISOLATION LEVEL REPEATABLE READ;
INSERT INTO customer360.sys_tenant (tenant_id, tenant_code, tenant_name, company_name, business_type)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'workflow_isolation_test', 'A', 'A', 'test');
INSERT INTO customer360.cdp_content_items (content_item_id, tenant_id, item_type, title)
VALUES ('aaaaaaaa-0000-0000-0000-000000000021', 'aaaaaaaa-0000-0000-0000-000000000001', 'product', 'Product');
DO $$
BEGIN
    BEGIN
        INSERT INTO customer360.cdp_agent_workflow (tenant_id, segment_id, agent_code, execution_order)
        VALUES ('aaaaaaaa-0000-0000-0000-000000000001', gen_random_uuid(), 'workflow_test_churn', 1);
        RAISE EXCEPTION 'REPEATABLE READ candidate writes must be rejected';
    EXCEPTION WHEN feature_not_supported THEN
        NULL;
    END;
    BEGIN
        DELETE FROM customer360.cdp_content_items
        WHERE content_item_id = 'aaaaaaaa-0000-0000-0000-000000000021';
        RAISE EXCEPTION 'REPEATABLE READ candidate deletion must be rejected';
    EXCEPTION WHEN feature_not_supported THEN
        NULL;
    END;
END;
$$;
ROLLBACK;
\echo 'cdp_agent_workflow regression checks passed'
