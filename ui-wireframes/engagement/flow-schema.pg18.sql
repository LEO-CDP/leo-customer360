-- flow-schema.pg18.sql : persist customer-engagement flows (workflow JSON, schema_version 2) in PostgreSQL 18+.
--
-- Model
--   flow          one row per flow (name, status, which version is live)
--   flow_version  immutable snapshot of the full JSON per save. Source of truth for the UI and agents (exact round trip).
--   flow_node     one row per node, shredded from the JSON, so SQL can query and join flow content.
--   flow_edge     parent -> child links. Enforces "one parent per node" and "one child per branch" in the database.
--
-- PostgreSQL 18 features used: uuidv7(), VIRTUAL generated columns, RETURNING OLD/NEW.
-- Not run against a live server here: apply in a scratch database first.

CREATE SCHEMA IF NOT EXISTS engagement;

/* ---------- tables ---------- */
CREATE TABLE engagement.flow (
  flow_id         uuid        PRIMARY KEY DEFAULT uuidv7(),      -- time-ordered, index friendly
  tenant_id       uuid        NOT NULL,
  name            text        NOT NULL,
  status          text        NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'paused', 'archived')),
  live_version    int,                                           -- NULL until first publish
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE TABLE engagement.flow_version (
  flow_id         uuid        NOT NULL REFERENCES engagement.flow ON DELETE CASCADE,
  version         int         NOT NULL,
  schema_version  int         NOT NULL DEFAULT 2,
  definition      jsonb       NOT NULL CHECK (jsonb_typeof(definition -> 'root') = 'object'),
  created_by      text        NOT NULL DEFAULT 'user' CHECK (created_by IN ('user', 'agent')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (flow_id, version)
);

CREATE TABLE engagement.flow_node (
  flow_id         uuid        NOT NULL,
  version         int         NOT NULL,
  node_id         text        NOT NULL,                          -- "n1", "n2", ... as in the JSON
  node_type       text        NOT NULL CHECK (node_type IN
                    ('trigger', 'condition', 'wait', 'wait_event', 'exit', 'email', 'sms', 'push', 'update_profile')),
  config          jsonb       NOT NULL DEFAULT '{}',
  -- derived, computed on read, never stored (PG18 virtual generated columns)
  channel         text GENERATED ALWAYS AS (
                    CASE node_type WHEN 'email' THEN 'EMAIL' WHEN 'sms' THEN 'SMS' WHEN 'push' THEN 'PUSH' END) VIRTUAL,
  wait_for        interval GENERATED ALWAYS AS (
                    CASE WHEN node_type IN ('wait', 'wait_event')
                         THEN (config ->> 'amount')::int *
                              CASE config ->> 'unit' WHEN 'minutes' THEN interval '1 minute'
                                                     WHEN 'hours'   THEN interval '1 hour'
                                                     WHEN 'days'    THEN interval '1 day' END
                    END) VIRTUAL,
  PRIMARY KEY (flow_id, version, node_id),
  FOREIGN KEY (flow_id, version) REFERENCES engagement.flow_version ON DELETE CASCADE,
  CHECK (node_type NOT IN ('wait', 'wait_event') OR (config ? 'amount' AND config ? 'unit')),
  CHECK (node_type <> 'email' OR (config ? 'subject' AND config ? 'body')),
  CHECK (node_type <> 'update_profile' OR (config ? 'action' AND config ? 'tag'))
);

CREATE TABLE engagement.flow_edge (
  flow_id         uuid        NOT NULL,
  version         int         NOT NULL,
  from_node       text        NOT NULL,
  to_node         text        NOT NULL,
  branch          text        CHECK (branch IN ('yes', 'no', 'happened', 'timeout')),  -- NULL for single-child nodes
  position        smallint    NOT NULL DEFAULT 0,                -- left-to-right order on the canvas
  PRIMARY KEY (flow_id, version, from_node, to_node),
  UNIQUE (flow_id, version, to_node),                            -- a tree: every node has one parent
  UNIQUE NULLS NOT DISTINCT (flow_id, version, from_node, branch),  -- one child per branch; one child when branch is NULL
  FOREIGN KEY (flow_id, version, from_node) REFERENCES engagement.flow_node ON DELETE CASCADE,
  FOREIGN KEY (flow_id, version, to_node)   REFERENCES engagement.flow_node ON DELETE CASCADE
);

CREATE INDEX flow_node_type_idx ON engagement.flow_node (flow_id, version, node_type);

/* ---------- save: store the JSON and shred it into node and edge rows ---------- */
CREATE OR REPLACE FUNCTION engagement.save_flow(
  p_tenant   uuid,
  p_flow_id  uuid,        -- NULL creates a new flow
  p_name     text,
  p_workflow jsonb,       -- the object from workflowAgent.getWorkflow()
  p_actor    text DEFAULT 'user'
) RETURNS TABLE (flow_id uuid, version int)
LANGUAGE plpgsql AS $$
DECLARE
  v_flow uuid := p_flow_id;
  v_ver  int;
BEGIN
  IF v_flow IS NULL THEN
    INSERT INTO engagement.flow (tenant_id, name) VALUES (p_tenant, p_name) RETURNING engagement.flow.flow_id INTO v_flow;
  ELSE
    UPDATE engagement.flow f SET name = p_name, updated_at = now()
    WHERE f.flow_id = v_flow AND f.tenant_id = p_tenant;
    IF NOT FOUND THEN RAISE EXCEPTION 'flow % not found for this tenant', v_flow; END IF;
  END IF;

  SELECT COALESCE(max(fv.version), 0) + 1 INTO v_ver FROM engagement.flow_version fv WHERE fv.flow_id = v_flow;

  INSERT INTO engagement.flow_version (flow_id, version, schema_version, definition, created_by)
  VALUES (v_flow, v_ver, COALESCE((p_workflow ->> 'schema_version')::int, 2), p_workflow, p_actor);

  -- Walk the JSON tree once. Nodes and edges go in one statement: foreign keys are checked when it ends.
  WITH RECURSIVE t AS (
    SELECT p_workflow -> 'root' AS n, NULL::text AS parent, NULL::text AS branch, 0::smallint AS pos
    UNION ALL
    SELECT c.value, t.n ->> 'id', c.value ->> 'branch', (c.ord - 1)::smallint
    FROM t, jsonb_array_elements(COALESCE(t.n -> 'children', '[]')) WITH ORDINALITY AS c(value, ord)
  ),
  ins_nodes AS (
    INSERT INTO engagement.flow_node (flow_id, version, node_id, node_type, config)
    SELECT v_flow, v_ver, n ->> 'id', n ->> 'type', COALESCE(n -> 'config', '{}') FROM t
    RETURNING 1
  )
  INSERT INTO engagement.flow_edge (flow_id, version, from_node, to_node, branch, position)
  SELECT v_flow, v_ver, parent, n ->> 'id', branch, pos FROM t WHERE parent IS NOT NULL;

  flow_id := v_flow; version := v_ver;
  RETURN NEXT;
END $$;

/* ---------- publish: PG18 RETURNING OLD/NEW shows what changed ---------- */
CREATE OR REPLACE FUNCTION engagement.publish_flow(p_flow_id uuid, p_version int)
RETURNS TABLE (previous_version int, live_version int)
LANGUAGE sql AS $$
  UPDATE engagement.flow f
  SET live_version = p_version, status = 'active', updated_at = now()
  WHERE f.flow_id = p_flow_id
    AND EXISTS (SELECT 1 FROM engagement.flow_version v WHERE v.flow_id = p_flow_id AND v.version = p_version)
  RETURNING old.live_version, new.live_version;
$$;

/* ---------- load: rebuild the JSON tree from the tables (proves the rows are complete) ---------- */
CREATE OR REPLACE FUNCTION engagement.node_json(p_flow uuid, p_ver int, p_node text, p_branch text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
           'id', n.node_id, 'type', n.node_type, 'config', n.config,
           'children', COALESCE((
             SELECT jsonb_agg(engagement.node_json(p_flow, p_ver, e.to_node, e.branch) ORDER BY e.position)
             FROM engagement.flow_edge e
             WHERE e.flow_id = p_flow AND e.version = p_ver AND e.from_node = n.node_id), '[]'))
         || CASE WHEN p_branch IS NULL THEN '{}'::jsonb ELSE jsonb_build_object('branch', p_branch) END
  FROM engagement.flow_node n
  WHERE n.flow_id = p_flow AND n.version = p_ver AND n.node_id = p_node;
$$;

CREATE OR REPLACE FUNCTION engagement.load_flow(p_flow_id uuid, p_version int DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object('schema_version', v.schema_version, 'root',
           engagement.node_json(v.flow_id, v.version,
             (SELECT n.node_id FROM engagement.flow_node n
              WHERE n.flow_id = v.flow_id AND n.version = v.version AND n.node_type = 'trigger')))
  FROM engagement.flow_version v
  JOIN engagement.flow f USING (flow_id)
  WHERE v.flow_id = p_flow_id AND v.version = COALESCE(p_version, f.live_version, (SELECT max(version) FROM engagement.flow_version WHERE flow_id = p_flow_id));
$$;

/* ---------- usage ---------- */
-- SELECT * FROM engagement.save_flow('00000000-0000-0000-0000-000000000001', NULL, 'Cart abandonment', :'wf'::jsonb, 'agent');
-- SELECT * FROM engagement.publish_flow('<flow_id>', 1);
-- SELECT engagement.load_flow('<flow_id>');                       -- live version, or latest draft
-- SELECT node_id, channel, wait_for FROM engagement.flow_node WHERE flow_id = '<flow_id>' AND version = 1;
-- Which flows send SMS?
-- SELECT DISTINCT f.name FROM engagement.flow f JOIN engagement.flow_node n ON n.flow_id = f.flow_id AND n.version = f.live_version WHERE n.node_type = 'sms';
