-- Run only against a disposable database initialized with the schema and seeds.
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

SELECT set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
SELECT pg_temp.assert_true(
    NOT EXISTS (
        SELECT 1
        FROM customer360.sys_domain d
        LEFT JOIN customer360.cdp_segments s
          ON s.tenant_id = '11111111-1111-1111-1111-111111111111'::uuid
         AND s.segment_tag = 'domain_' || d.domain_code
         AND s.domain = d.domain_code
        WHERE s.segment_id IS NULL
    ),
    'every catalog domain has a default-tenant segment'
);
SELECT pg_temp.assert_true(
    (SELECT array_agg(segment_tag ORDER BY segment_name, segment_id)
     FROM (
         SELECT segment_tag, segment_name, segment_id
         FROM customer360.cdp_segments
         WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
           AND is_active AND status_code = 1
         ORDER BY CASE WHEN segment_tag IN ('churn_risk', 'champions') THEN 0 ELSE 1 END,
                  segment_name, segment_id
         LIMIT 2
     ) selected) = ARRAY['churn_risk', 'champions'],
    'demo experiment retains its original segment selection'
);

UPDATE customer360.cdp_segments
SET segment_name = 'Operator-customized Education'
WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
  AND segment_tag = 'domain_education';

INSERT INTO customer360.sys_domain (domain_code, domain_name, is_active)
VALUES ('test_new_domain', 'Test Domain', FALSE),
       ('drop', 'Keyword Domain', TRUE);
INSERT INTO customer360.sys_tenant
    (tenant_id, tenant_code, tenant_name, company_name, business_type)
VALUES ('22222222-2222-2222-2222-222222222222',
        'domain_segment_test', 'Other tenant', 'Other', 'test');

\ir ../init-core-database.sql

SELECT pg_temp.assert_true(
    (SELECT segment_name = 'Operator-customized Education'
     FROM customer360.cdp_segments
     WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
       AND segment_tag = 'domain_education'),
    'reseed preserves an operator-customized segment'
);
SELECT pg_temp.assert_true(
    EXISTS (
        SELECT 1 FROM customer360.cdp_segments
        WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
          AND segment_tag = 'domain_test_new_domain'
          AND domain = 'test_new_domain'
    ),
    'reseed adds a segment for a newly registered domain'
);

INSERT INTO customer360.cdp_master_profiles (master_profile_id, tenant_id, domain, status_code)
VALUES
    ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111', 'education', 1),
    ('44444444-4444-4444-4444-444444444444', '11111111-1111-1111-1111-111111111111', 'banking', 1),
    ('55555555-5555-5555-5555-555555555555', '11111111-1111-1111-1111-111111111111', 'education', 0),
    ('77777777-7777-7777-7777-777777777777', '11111111-1111-1111-1111-111111111111', 'drop', 1);

SELECT set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222', true);
INSERT INTO customer360.cdp_master_profiles (master_profile_id, tenant_id, domain, status_code)
VALUES ('66666666-6666-6666-6666-666666666666',
        '22222222-2222-2222-2222-222222222222', 'education', 1);
SELECT pg_temp.assert_true(
    NOT EXISTS (
        SELECT 1 FROM customer360.cdp_segments
        WHERE tenant_id = '22222222-2222-2222-2222-222222222222'
    ),
    'domain seed does not create segments for another tenant'
);
SELECT set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);

DO $$
DECLARE
    segment RECORD;
    matches INTEGER;
BEGIN
    FOR segment IN
        SELECT domain, segment_tag, json_rules, sql_rules, final_generated_sql
        FROM customer360.cdp_segments
        WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
          AND segment_tag IN ('domain_education', 'domain_banking', 'domain_drop')
    LOOP
        IF segment.json_rules #>> '{rules,0,value}' <> segment.domain
           OR segment.final_generated_sql NOT LIKE '%11111111-1111-1111-1111-111111111111%' THEN
            RAISE EXCEPTION 'Invalid rule for segment %', segment.segment_tag;
        END IF;
        EXECUTE format(
            'SELECT count(*) FROM customer360.cdp_master_profiles WHERE tenant_id = $1 AND status_code = 1 AND (%s)',
            segment.sql_rules
        )
        INTO matches USING '11111111-1111-1111-1111-111111111111'::uuid;
        IF matches <> 1 THEN
            RAISE EXCEPTION 'Segment % matched % profiles instead of 1', segment.segment_tag, matches;
        END IF;
    END LOOP;
END $$;

ROLLBACK;
