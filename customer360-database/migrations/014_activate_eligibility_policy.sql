BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM customer360.cdp_ai_agents
        WHERE agent_code = 'eligibility_policy'
          AND model_type = 'rules_engine'
    ) THEN
        RAISE EXCEPTION
            'Seed eligibility_policy before applying migration 014';
    END IF;
END;
$$;

UPDATE customer360.cdp_ai_agents
SET status = 'ACTIVE',
    updated_at = now()
WHERE agent_code = 'eligibility_policy'
  AND model_type = 'rules_engine'
  AND status = 'INACTIVE';

COMMIT;
