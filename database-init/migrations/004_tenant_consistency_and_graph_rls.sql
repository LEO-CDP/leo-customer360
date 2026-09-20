-- Tenant consistency and graph isolation upgrade.
-- This migration runs after database-schema.sql in deployments/postgres/run-sql.sh.
-- It upgrades legacy volumes where graph_edges and sys_user_role predate tenant_id.

---------------------------------------------------
-- GRAPH EDGES: add and backfill tenant ownership
---------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute a
        JOIN pg_class r ON r.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'graph_edges'
          AND a.attname = 'tenant_id'
          AND a.attnum > 0
          AND NOT a.attisdropped
    ) THEN
        ALTER TABLE customer360.graph_edges ADD COLUMN tenant_id UUID;
    END IF;
END;
$$;

UPDATE customer360.graph_edges
SET tenant_id = CASE
    WHEN COALESCE(metadata ->> 'tenant_id', metadata ->> 'demo_tenant')
         ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    THEN COALESCE(metadata ->> 'tenant_id', metadata ->> 'demo_tenant')::uuid
    ELSE NULL
END
WHERE tenant_id IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM customer360.graph_edges
        WHERE tenant_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot enable graph_edges tenant isolation: one or more existing edges have no tenant_id lineage';
    END IF;

    ALTER TABLE customer360.graph_edges
        ALTER COLUMN tenant_id SET NOT NULL;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'graph_edges'
          AND c.conname = 'fk_graph_edges_tenant'
    ) THEN
        ALTER TABLE customer360.graph_edges
            ADD CONSTRAINT fk_graph_edges_tenant
            FOREIGN KEY (tenant_id)
            REFERENCES customer360.sys_tenant(tenant_id);
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_graph_edges_tenant
    ON customer360.graph_edges (tenant_id);

ALTER TABLE customer360.graph_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.graph_edges FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.graph_edges;
CREATE POLICY tenant_policy ON customer360.graph_edges
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);

---------------------------------------------------
-- USER ROLE ASSIGNMENTS: add tenant ownership
---------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute a
        JOIN pg_class r ON r.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'sys_user_role'
          AND a.attname = 'tenant_id'
          AND a.attnum > 0
          AND NOT a.attisdropped
    ) THEN
        ALTER TABLE customer360.sys_user_role ADD COLUMN tenant_id UUID;
    END IF;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM customer360.sys_user_role ur
        JOIN customer360.sys_user u ON u.user_id = ur.user_id
        JOIN customer360.sys_role r ON r.role_id = ur.role_id
        WHERE u.tenant_id <> r.tenant_id
    ) THEN
        RAISE EXCEPTION
            'Cannot enable sys_user_role tenant isolation: existing user/role assignment crosses tenants';
    END IF;

    UPDATE customer360.sys_user_role ur
    SET tenant_id = u.tenant_id
    FROM customer360.sys_user u
    WHERE u.user_id = ur.user_id
      AND ur.tenant_id IS NULL;

    UPDATE customer360.sys_user_role ur
    SET tenant_id = r.tenant_id
    FROM customer360.sys_role r
    WHERE r.role_id = ur.role_id
      AND ur.tenant_id IS NULL;

    IF EXISTS (
        SELECT 1
        FROM customer360.sys_user_role
        WHERE tenant_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot enable sys_user_role tenant isolation: an assignment has no tenant lineage';
    END IF;

    ALTER TABLE customer360.sys_user_role
        ALTER COLUMN tenant_id SET NOT NULL;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'sys_user_role'
          AND c.conname = 'fk_sys_user_role_tenant'
    ) THEN
        ALTER TABLE customer360.sys_user_role
            ADD CONSTRAINT fk_sys_user_role_tenant
            FOREIGN KEY (tenant_id)
            REFERENCES customer360.sys_tenant(tenant_id)
            ON DELETE CASCADE;
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_sys_user_tenant_id
    ON customer360.sys_user (tenant_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sys_role_tenant_id
    ON customer360.sys_role (tenant_id, role_id);

DO $$
DECLARE
    primary_key_name TEXT;
BEGIN
    SELECT c.conname
    INTO primary_key_name
    FROM pg_constraint c
    JOIN pg_class r ON r.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = r.relnamespace
    WHERE n.nspname = 'customer360'
      AND r.relname = 'sys_user_role'
      AND c.contype = 'p'
      AND c.conkey <> ARRAY[
          (SELECT attnum FROM pg_attribute WHERE attrelid = r.oid AND attname = 'tenant_id'),
          (SELECT attnum FROM pg_attribute WHERE attrelid = r.oid AND attname = 'user_id'),
          (SELECT attnum FROM pg_attribute WHERE attrelid = r.oid AND attname = 'role_id')
      ]::smallint[];

    IF primary_key_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE customer360.sys_user_role DROP CONSTRAINT %I', primary_key_name);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'sys_user_role'
          AND c.contype = 'p'
          AND c.conkey = ARRAY[
              (SELECT attnum FROM pg_attribute WHERE attrelid = r.oid AND attname = 'tenant_id'),
              (SELECT attnum FROM pg_attribute WHERE attrelid = r.oid AND attname = 'user_id'),
              (SELECT attnum FROM pg_attribute WHERE attrelid = r.oid AND attname = 'role_id')
          ]::smallint[]
    ) THEN
        ALTER TABLE customer360.sys_user_role
            ADD CONSTRAINT sys_user_role_pkey PRIMARY KEY (tenant_id, user_id, role_id);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'sys_user_role'
          AND c.conname = 'fk_sys_user_role_tenant_user'
    ) THEN
        ALTER TABLE customer360.sys_user_role
            ADD CONSTRAINT fk_sys_user_role_tenant_user
            FOREIGN KEY (tenant_id, user_id)
            REFERENCES customer360.sys_user(tenant_id, user_id)
            ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'sys_user_role'
          AND c.conname = 'fk_sys_user_role_tenant_role'
    ) THEN
        ALTER TABLE customer360.sys_user_role
            ADD CONSTRAINT fk_sys_user_role_tenant_role
            FOREIGN KEY (tenant_id, role_id)
            REFERENCES customer360.sys_role(tenant_id, role_id)
            ON DELETE CASCADE;
    END IF;
END;
$$;

ALTER TABLE customer360.sys_user_role ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.sys_user_role FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_policy ON customer360.sys_user_role;
CREATE POLICY tenant_policy ON customer360.sys_user_role
    USING (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(btrim(current_setting('app.tenant_id', true)), '')::uuid);
