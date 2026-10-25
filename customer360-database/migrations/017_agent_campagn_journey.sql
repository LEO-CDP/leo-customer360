-- ============================================================================
-- Upgrade: connect Campaigns to AI Agents (smallest change set)
-- Target : customer360 schema, PostgreSQL 16+ (tested on 16, uses no 17/18-only syntax)
-- Safe to re-run (idempotent). Append to database-schema.sql or run as a migration.
--
-- What it adds
--   1. journey_stage on cdp_ai_agents and crm_campaign (7 stages shared by both)
--   2. AI node types on crm_flow_node + a generated agent_code column with an FK
--   3. crm_campaign_agent: which agents work on a campaign, in which role,
--      at which autonomy level, pinned to which prompt revision
--
-- What it deliberately does NOT add (already covered by your schema)
--   - workflow versions / pinning / review  -> crm_flow, crm_flow_version, crm_campaign.workflow_*
--   - agent proposals                       -> crm_flow_version (created_by = 'agent', InReview)
--   - prompt versions                       -> cdp_ai_agents.prompt_versions / instruction_version
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Journey stage on agents and campaigns
-- ----------------------------------------------------------------------------
ALTER TABLE customer360.cdp_ai_agents
    ADD COLUMN IF NOT EXISTS journey_stage VARCHAR(30);

ALTER TABLE customer360.crm_campaign
    ADD COLUMN IF NOT EXISTS journey_stage VARCHAR(30);

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT * FROM (VALUES
            ('cdp_ai_agents', 'chk_cdp_ai_agents_journey_stage'),
            ('crm_campaign',  'chk_crm_campaign_journey_stage')
        ) AS t(table_name, constraint_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = c.constraint_name
              AND conrelid = format('customer360.%I', c.table_name)::regclass
        ) THEN
            EXECUTE format(
                'ALTER TABLE customer360.%I ADD CONSTRAINT %I CHECK (journey_stage IS NULL OR journey_stage IN (
                    ''awareness'', ''acquisition'', ''activation'', ''engagement'',
                    ''conversion'', ''retention'', ''loyalty''))',
                c.table_name, c.constraint_name);
        END IF;
    END LOOP;
END $$;

COMMENT ON COLUMN customer360.cdp_ai_agents.journey_stage IS
    'Customer journey stage this agent serves (awareness .. loyalty). NULL = cross-stage. Drives the AI Agents journey map.';
COMMENT ON COLUMN customer360.crm_campaign.journey_stage IS
    'Customer journey stage this campaign targets; derived from objective in the app. Lets the journey map show live campaigns per stage.';

CREATE INDEX IF NOT EXISTS idx_cdp_ai_agents_journey_stage
    ON customer360.cdp_ai_agents (journey_stage) WHERE journey_stage IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_campaign_journey_stage
    ON customer360.crm_campaign (tenant_id, journey_stage) WHERE journey_stage IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 2. AI nodes in the workflow canvas
--    ai_decide    : score/classify a profile (e.g. churn risk), result feeds a later condition node
--    ai_generate  : write subject/body/copy for a following send node
--    ai_recommend : pick product/content/next best action
--    Branching stays with the existing 'condition' node (yes/no), so crm_flow_edge,
--    crm_save_flow and crm_load_flow need no change.
-- ----------------------------------------------------------------------------
ALTER TABLE customer360.crm_flow_node
    DROP CONSTRAINT IF EXISTS crm_flow_node_node_type_check;
ALTER TABLE customer360.crm_flow_node
    ADD CONSTRAINT crm_flow_node_node_type_check CHECK (node_type IN (
        'trigger', 'condition', 'wait', 'wait_event', 'exit',
        'email', 'notification', 'push', 'update_profile', 'run_data_synch_job',
        'ai_decide', 'ai_generate', 'ai_recommend'
    )) NOT VALID;

-- agent_code is read from config, so crm_save_flow keeps shredding JSON unchanged.
ALTER TABLE customer360.crm_flow_node
    ADD COLUMN IF NOT EXISTS agent_code VARCHAR(100)
    GENERATED ALWAYS AS (config ->> 'agent_code') STORED;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_flow_node_ai_agent_check'
          AND conrelid = 'customer360.crm_flow_node'::regclass
    ) THEN
        ALTER TABLE customer360.crm_flow_node
            ADD CONSTRAINT crm_flow_node_ai_agent_check
            CHECK (node_type NOT IN ('ai_decide', 'ai_generate', 'ai_recommend')
                   OR (config ->> 'agent_code' IS NOT NULL
                       AND btrim(config ->> 'agent_code') <> ''));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_flow_node_agent_fk'
          AND conrelid = 'customer360.crm_flow_node'::regclass
    ) THEN
        ALTER TABLE customer360.crm_flow_node
            ADD CONSTRAINT crm_flow_node_agent_fk
            FOREIGN KEY (agent_code)
            REFERENCES customer360.cdp_ai_agents (agent_code) ON DELETE RESTRICT;
    END IF;
END $$;

COMMENT ON COLUMN customer360.crm_flow_node.agent_code IS
    'Generated from config->>agent_code. Set only for AI nodes; FK keeps every AI node pointing at a registered agent.';

CREATE INDEX IF NOT EXISTS crm_flow_node_agent_idx
    ON customer360.crm_flow_node (agent_code) WHERE agent_code IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 3. Campaign <-> agent assignment
--    autonomy: 'suggest' (advice only) or 'approve' (agent drafts, human reviews).
--    'auto' is intentionally absent: crm_review_flow_version / crm_publish_flow
--    still require a human reviewer, so allowing it here would promise too much.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_agent (
    campaign_agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL,
    agent_code VARCHAR(100) NOT NULL
        REFERENCES customer360.cdp_ai_agents(agent_code) ON DELETE RESTRICT,
    role VARCHAR(20) NOT NULL CHECK (role IN ('plan', 'decide', 'optimize')),
    autonomy VARCHAR(20) NOT NULL DEFAULT 'approve' CHECK (autonomy IN ('suggest', 'approve')),
    -- Prompt revision pinned at campaign approval; NULL = follow cdp_ai_agents.instruction_version.
    pinned_instruction_version INTEGER CHECK (pinned_instruction_version > 0),
    guardrails JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(guardrails) = 'object'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_crm_campaign_agent_tenant_campaign
        FOREIGN KEY (tenant_id, campaign_id)
        REFERENCES customer360.crm_campaign (tenant_id, campaign_id) ON DELETE CASCADE,
    CONSTRAINT uq_crm_campaign_agent UNIQUE (campaign_id, agent_code, role)
);

COMMENT ON TABLE customer360.crm_campaign_agent IS
    'Agents assigned to a campaign with a role (plan/decide/optimize), autonomy level, guardrails and pinned prompt revision. Answers "campaigns using this agent" and "agents on this campaign".';

CREATE INDEX IF NOT EXISTS idx_crm_campaign_agent_agent
    ON customer360.crm_campaign_agent (agent_code, tenant_id) WHERE is_active;

ALTER TABLE customer360.crm_campaign_agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.crm_campaign_agent FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.crm_campaign_agent;
CREATE POLICY tenant_policy ON customer360.crm_campaign_agent
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMIT;