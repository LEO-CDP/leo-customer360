BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_agent_workflow
        WHERE agent_code = 'identity_graph_intelligence'
    ) THEN
        RAISE EXCEPTION
            'Remove identity_graph_intelligence workflow steps before applying migration 013';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_profile_attributes
        WHERE agent_code = 'identity_graph_intelligence'
    ) THEN
        RAISE EXCEPTION
            'Reassign identity_graph_intelligence profile attributes before applying migration 013';
    END IF;
END;
$$;

DELETE FROM customer360.cdp_ai_agents
WHERE agent_code = 'identity_graph_intelligence';

ALTER TABLE customer360.cdp_ai_agents
    DROP CONSTRAINT IF EXISTS cdp_ai_agents_model_type_check;
ALTER TABLE customer360.cdp_ai_agents
    DROP CONSTRAINT IF EXISTS chk_cdp_ai_agents_model_type;
ALTER TABLE customer360.cdp_ai_agents
    ADD CONSTRAINT chk_cdp_ai_agents_model_type
    CHECK (model_type IN (
        'classification',
        'regression',
        'clustering',
        'ranking_recommendation',
        'forecasting',
        'anomaly_detection',
        'uplift_modeling',
        'semantic_embedding',
        'optimization',
        'rules_engine',
        'generative_llm'
    ));

COMMIT;
