-- Generalize the email-only template table into a reusable CRM message
-- template table while preserving template_id references from campaigns and
-- dispatch logs.
DO $$
BEGIN
    IF to_regclass('customer360.crm_email_templates') IS NOT NULL
       AND to_regclass('customer360.crm_message_templates') IS NULL THEN
        ALTER TABLE customer360.crm_email_templates
            RENAME TO crm_message_templates;
    END IF;
END $$;

ALTER TABLE IF EXISTS customer360.crm_message_templates
    ADD COLUMN IF NOT EXISTS message_type TEXT NOT NULL DEFAULT 'EMAIL',
    ADD COLUMN IF NOT EXISTS persona_id UUID,
    ADD COLUMN IF NOT EXISTS context JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS message_body TEXT;

ALTER TABLE IF EXISTS customer360.crm_message_templates
    ALTER COLUMN message_type TYPE TEXT;

DO $$
BEGIN
    IF to_regclass('customer360.crm_message_templates') IS NOT NULL THEN
        UPDATE customer360.crm_message_templates
        SET message_type = 'EMAIL'
        WHERE message_type IS NULL OR btrim(message_type) = '';

        UPDATE customer360.crm_message_templates
        SET message_body = COALESCE(message_body, text_body, html_body)
        WHERE message_body IS NULL;

        ALTER TABLE customer360.crm_message_templates
            ALTER COLUMN message_type SET NOT NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('customer360.crm_message_templates') IS NULL THEN
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_crm_message_templates_persona'
          AND conrelid = 'customer360.crm_message_templates'::regclass
    ) THEN
        ALTER TABLE customer360.crm_message_templates
            ADD CONSTRAINT fk_crm_message_templates_persona
            FOREIGN KEY (persona_id)
            REFERENCES customer360.cdp_persona_archetypes(persona_archetype_id)
            ON DELETE SET NULL;
    END IF;

    ALTER TABLE customer360.crm_message_templates
        DROP CONSTRAINT IF EXISTS chk_crm_message_templates_type;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_crm_message_templates_message_type_not_blank'
          AND conrelid = 'customer360.crm_message_templates'::regclass
    ) THEN
        ALTER TABLE customer360.crm_message_templates
            ADD CONSTRAINT chk_crm_message_templates_message_type_not_blank
            CHECK (btrim(message_type) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_crm_message_templates_context_object'
          AND conrelid = 'customer360.crm_message_templates'::regclass
    ) THEN
        ALTER TABLE customer360.crm_message_templates
            ADD CONSTRAINT chk_crm_message_templates_context_object
            CHECK (jsonb_typeof(context) = 'object');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_crm_message_templates_variables_object'
          AND conrelid = 'customer360.crm_message_templates'::regclass
    ) THEN
        ALTER TABLE customer360.crm_message_templates
            ADD CONSTRAINT chk_crm_message_templates_variables_object
            CHECK (jsonb_typeof(variables) = 'object');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_crm_message_templates_tenant
    ON customer360.crm_message_templates (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_message_templates_tenant_status
    ON customer360.crm_message_templates (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_crm_message_templates_tenant_type
    ON customer360.crm_message_templates (tenant_id, message_type);
CREATE INDEX IF NOT EXISTS idx_crm_message_templates_persona
    ON customer360.crm_message_templates (persona_id)
    WHERE persona_id IS NOT NULL;