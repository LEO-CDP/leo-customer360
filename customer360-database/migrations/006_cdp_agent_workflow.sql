-- Add ordered segment/agent configuration to existing databases.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_segments_tenant_id
    ON customer360.cdp_segments (tenant_id, segment_id);

CREATE TABLE IF NOT EXISTS customer360.cdp_agent_workflow (
    agent_workflow_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    segment_id UUID NOT NULL,
    agent_code VARCHAR(100) NOT NULL REFERENCES customer360.cdp_ai_agents(agent_code) ON DELETE RESTRICT,
    execution_order INTEGER NOT NULL CHECK (execution_order > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    schedule_definition VARCHAR(100),
    candidate_content_item_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(configuration) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_cdp_agent_workflow_tenant_segment
        FOREIGN KEY (tenant_id, segment_id)
        REFERENCES customer360.cdp_segments (tenant_id, segment_id) ON DELETE CASCADE,
    CONSTRAINT uq_cdp_agent_workflow_segment_agent UNIQUE (tenant_id, segment_id, agent_code),
    CONSTRAINT uq_cdp_agent_workflow_execution_order
        UNIQUE (tenant_id, segment_id, execution_order) DEFERRABLE INITIALLY IMMEDIATE,
    CONSTRAINT chk_cdp_agent_workflow_candidates_dimension
        CHECK (cardinality(candidate_content_item_ids) = 0 OR array_ndims(candidate_content_item_ids) = 1)
);

-- Existing installations may already have the workflow table from the first
-- version of this migration. Add the scheduler column before comments and
-- indexes reference it, without requiring existing workflow rows to be rebuilt.
ALTER TABLE customer360.cdp_agent_workflow
    ADD COLUMN IF NOT EXISTS schedule_definition VARCHAR(100);

COMMENT ON TABLE customer360.cdp_agent_workflow IS
    'One configured AI-agent step per tenant/segment/agent. Defines a many-to-many relationship with the global agent registry; active steps execute sequentially in ascending execution_order. Different segments may execute concurrently.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.execution_order IS
    'Positive queue position, lowest first. The workflow runner must await each step before starting the next; defer the unique constraint within a transaction when reordering steps.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.schedule_definition IS
    'Optional five-field cron override for this segment-agent step. NULL means inherit the selected cdp_ai_agents.schedule_definition.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.candidate_content_item_ids IS
    'Per-step content/product candidates from cdp_content_items. Empty means no explicit candidates; the runner must not implicitly select the entire catalog. Triggers enforce existing same-tenant references and restrict deletion or key changes while referenced.';
COMMENT ON COLUMN customer360.cdp_agent_workflow.configuration IS
    'Per-segment agent input/configuration overrides, such as ranking parameters. This is configuration, not workflow execution state.';

CREATE INDEX IF NOT EXISTS idx_cdp_agent_workflow_agent
    ON customer360.cdp_agent_workflow (agent_code, tenant_id, segment_id);
CREATE INDEX IF NOT EXISTS idx_cdp_agent_workflow_active_queue
    ON customer360.cdp_agent_workflow (tenant_id, segment_id, execution_order)
    WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_cdp_agent_workflow_candidates
    ON customer360.cdp_agent_workflow USING GIN (candidate_content_item_ids);

-- An array keeps candidate lists in the requested single relation table.
-- Lock referenced rows to serialize validation against deletion/key changes.
CREATE OR REPLACE FUNCTION customer360.validate_agent_workflow_candidates()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_id UUID;
BEGIN
    IF current_setting('transaction_isolation') = 'repeatable read' THEN
        RAISE EXCEPTION 'Workflow candidate writes require READ COMMITTED or SERIALIZABLE isolation'
            USING ERRCODE = '0A000';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(NEW.candidate_content_item_ids) AS candidate(item_id)
        GROUP BY item_id
        HAVING item_id IS NULL OR count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Workflow candidates must be distinct, non-null content item IDs'
            USING ERRCODE = '23514';
    END IF;

    FOR candidate_id IN
        SELECT item_id
        FROM unnest(NEW.candidate_content_item_ids) AS candidate(item_id)
        ORDER BY item_id
    LOOP
        PERFORM 1
        FROM customer360.cdp_content_items
        WHERE tenant_id = NEW.tenant_id AND content_item_id = candidate_id
        FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Workflow candidate % does not exist in tenant %', candidate_id, NEW.tenant_id
                USING ERRCODE = '23503';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_agent_workflow_candidates ON customer360.cdp_agent_workflow;
CREATE TRIGGER trg_validate_agent_workflow_candidates
    BEFORE INSERT OR UPDATE OF tenant_id, candidate_content_item_ids
    ON customer360.cdp_agent_workflow
    FOR EACH ROW EXECUTE FUNCTION customer360.validate_agent_workflow_candidates();

CREATE OR REPLACE FUNCTION customer360.restrict_workflow_candidate_changes()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant_id = OLD.tenant_id AND NEW.content_item_id = OLD.content_item_id THEN
            RETURN NEW;
        END IF;
    END IF;

    IF current_setting('transaction_isolation') = 'repeatable read' THEN
        RAISE EXCEPTION 'Content candidate deletion/key changes require READ COMMITTED or SERIALIZABLE isolation'
            USING ERRCODE = '0A000';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM customer360.cdp_agent_workflow
        WHERE tenant_id = OLD.tenant_id
          AND candidate_content_item_ids @> ARRAY[OLD.content_item_id]
    ) THEN
        RAISE EXCEPTION 'Content item % is referenced by an agent workflow', OLD.content_item_id
            USING ERRCODE = '23503';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_restrict_workflow_candidate_changes ON customer360.cdp_content_items;
CREATE TRIGGER trg_restrict_workflow_candidate_changes
    BEFORE DELETE OR UPDATE OF tenant_id, content_item_id
    ON customer360.cdp_content_items
    FOR EACH ROW EXECUTE FUNCTION customer360.restrict_workflow_candidate_changes();

ALTER TABLE customer360.cdp_agent_workflow ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_agent_workflow FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_agent_workflow;
CREATE POLICY tenant_policy ON customer360.cdp_agent_workflow
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMIT;
