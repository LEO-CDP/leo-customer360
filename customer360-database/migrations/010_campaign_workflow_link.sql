-- Link each campaign to the exact immutable workflow version it saves with.
ALTER TABLE customer360.crm_campaign
    ADD COLUMN IF NOT EXISTS workflow_id UUID,
    ADD COLUMN IF NOT EXISTS workflow_version INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_campaign_workflow_version_fk'
          AND conrelid = 'customer360.crm_campaign'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign
            ADD CONSTRAINT crm_campaign_workflow_version_fk
            FOREIGN KEY (workflow_id, workflow_version, tenant_id)
            REFERENCES customer360.crm_flow_version (flow_id, version, tenant_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_campaign_workflow_version_pair_check'
          AND conrelid = 'customer360.crm_campaign'::regclass
    ) THEN
        ALTER TABLE customer360.crm_campaign
            ADD CONSTRAINT crm_campaign_workflow_version_pair_check
            CHECK ((workflow_id IS NULL) = (workflow_version IS NULL));
    END IF;
END $$;
