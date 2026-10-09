-- LEO Assistant chat memory: one row per message (long-term tier; the last few rows of a
-- conversation are also the short-term window the model sees). See sys_assistant_message below.
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS ux_sys_user_tenant_id
    ON customer360.sys_user (tenant_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_master_profiles_tenant_id
    ON customer360.cdp_master_profiles (tenant_id, master_profile_id);

CREATE TABLE IF NOT EXISTS customer360.sys_assistant_message (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    conversation_id UUID NOT NULL,
    user_id UUID NOT NULL,
    page VARCHAR(120),
    master_profile_id UUID,
    role VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    message_text TEXT NOT NULL CHECK (char_length(message_text) <= 2000),
    status VARCHAR(20),
    clarify VARCHAR(10) CHECK (clarify IN ('accents', 'question')),
    sources JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sources) = 'array'),
    -- clock_timestamp() (not now()) so a question and its answer written in one transaction keep their order.
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT fk_sys_assistant_message_tenant_user
        FOREIGN KEY (tenant_id, user_id)
        REFERENCES customer360.sys_user (tenant_id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_sys_assistant_message_tenant_profile
        FOREIGN KEY (tenant_id, master_profile_id)
        REFERENCES customer360.cdp_master_profiles (tenant_id, master_profile_id) ON DELETE CASCADE
);

-- Running summary of the whole chat so far, written by the model with each answer (assistant rows only).
-- ADD COLUMN IF NOT EXISTS so a database that already ran this file earlier gets it too.
ALTER TABLE customer360.sys_assistant_message
    ADD COLUMN IF NOT EXISTS summary TEXT CHECK (char_length(summary) <= 800);

COMMENT ON TABLE customer360.sys_assistant_message IS
    'LEO Assistant chat messages (long-term memory). One row per user question or assistant answer, grouped by conversation_id and bound to the page and customer the chat started on. message_text is stored with emails/phone numbers masked. Readable only by its author (every query filters user_id) for 30 days: reads ignore older rows and each write deletes them.';
COMMENT ON COLUMN customer360.sys_assistant_message.conversation_id IS
    'Client-held chat id (no conversation table). A request whose page or master_profile_id differs from the first message of the id starts a new conversation.';
COMMENT ON COLUMN customer360.sys_assistant_message.summary IS
    'For assistant rows: model-written summary of the whole conversation up to and including this answer (masked, at most 800 chars). The newest one is sent back to the model with the last exchange instead of the older messages.';
COMMENT ON COLUMN customer360.sys_assistant_message.sources IS
    'For assistant rows: the documents the answer used, [{path, title, heading}]. Empty for user rows and refusals.';

CREATE INDEX IF NOT EXISTS idx_sys_assistant_message_conversation
    ON customer360.sys_assistant_message (tenant_id, user_id, conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sys_assistant_message_scope
    ON customer360.sys_assistant_message (tenant_id, user_id, page, master_profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sys_assistant_message_created
    ON customer360.sys_assistant_message (tenant_id, created_at);

ALTER TABLE customer360.sys_assistant_message ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.sys_assistant_message FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.sys_assistant_message;
CREATE POLICY tenant_policy ON customer360.sys_assistant_message
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

COMMIT;
