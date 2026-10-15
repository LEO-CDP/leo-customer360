/*!
 * campaign-workflow.js : decision-tree campaign journey builder (Flowy) for the customer360 schema.
 *
 * INTEGRATION
 *   <link rel="stylesheet" href="flowy.css">
 *   <script src="jquery-3.7.1-or-4.x.min.js"></script> <script src="handlebars.min.js"></script>
 *   <script src="flowy.js"></script> <script src="workflow-template.js"></script>
 *   <script src="campaign-workflow.js"></script>
 *   CampaignWorkflow.mount(document.getElementById('host'), { adapter, locale: 'en' }).then(wf => ...);
 *
 *   The host element needs a fixed height (e.g. 100vh) and must not move after mount (Flowy limitation).
 *   Flowy is a page-level singleton: mount one builder per page.
 *
 * ADAPTER (all methods return a Promise; map them to your REST/GraphQL API)
 *   getContext()                       -> { tenant:{tenant_id,tenant_name}, campaign:crm_campaign row,
 *                                           segments:[cdp_segments rows], templates:[crm_message_templates rows],
 *                                           profiles?:[cdp_master_profiles rows]  (only for local preview),
 *                                           suppressions?:[crm_suppression_list rows], fields?:[field catalog] }
 *   loadWorkflow(campaignId)           -> workflow JSON | null          (crm_campaign.metadata -> 'workflow')
 *   saveWorkflow(campaignId, wf, meta) -> any    meta = { statements: compiled SQL per send node }
 *   previewAudience(wf)  [optional]    -> { total, nodes: { nodeId: { reach, deliverable } } }  (else evaluated locally)
 *   getReport(campaignId)              -> { metrics: vw_campaign_performance_metrics row,
 *                                           daily: crm_campaign_performance_daily rows,
 *                                           dispatch: [{ node_id, status, count }] }
 *
 * WORKFLOW JSON  { schema_version:1, root:{ id, type:'segment'|'condition'|'email'|'push', branch?:'yes'|'no',
 *                  config:{...}, children:[...] } }
 *   segment   config { segment_id }                     -> cdp_segments.segment_id (matched via segmentation_tags)
 *   condition config { field, op, value }               -> field codes come from the field catalog (allow-list)
 *   email/push config { template_id }                   -> crm_message_templates.template_id (EMAIL / PUSH, Approved)
 */

function assertSupportedJQuery() {
  const versionParts = jQuery.fn.jquery.split(".").map((part) => Number(part));
  const [major, minor, patch] = versionParts;
  const supportsJQuery37 = major === 3 &&
    (minor > 7 || (minor === 7 && patch >= 1));
  if (!supportsJQuery37 && major !== 4) {
    throw new Error("Campaign workflow supports jQuery 3.7.1 and jQuery 4.x.");
  }
}

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CampaignWorkflow = factory(root.WorkflowTemplates);
})(typeof self !== "undefined" ? self : this, function (workflowTemplates) {
  "use strict";

  /* ===================================================================
   * 1. Catalog, operators, small helpers
   * =================================================================== */
  const TYPES = {
    segment: { label: "Customer segment", code: "1" },
    condition: { label: "If / else", code: "2" },
    email: { label: "Send email", code: "3", channel: "EMAIL" },
    push: { label: "Send web push", code: "4", channel: "PUSH" },
  };
  const CODE = { 1: "segment", 2: "condition", 3: "email", 4: "push" };
  const OP_SQL = { gte: ">=", gt: ">", lte: "<=", lt: "<", eq: "=", neq: "<>" };
  const OP_LABEL = {
    gte: "≥",
    gt: ">",
    lte: "≤",
    lt: "<",
    eq: "=",
    neq: "≠",
    in: "is one of",
  };
  const OP_WORDS = {
    gte: "is at least",
    gt: "is above",
    lte: "is at most",
    lt: "is below",
    eq: "is",
    neq: "is not",
    in: "is one of",
  };
  const OPS_BY_TYPE = {
    NUMERIC: ["gte", "gt", "lte", "lt", "eq", "neq"],
    TEXT: ["eq", "neq", "in"],
    BOOLEAN: ["eq"],
  };

  // Allow-listed fields. `sql` is trusted code (never built from user input); values are always bound parameters.
  // Mirrors cdp_profile_attributes / cdp_master_profiles columns.
  const DEFAULT_FIELDS = [
    {
      code: "engagement_score",
      name: "Engagement score",
      data_type: "NUMERIC",
      sql: "mp.engagement_score",
      hint: "0 to 100",
    },
    {
      code: "churn_probability",
      name: "Churn probability",
      data_type: "NUMERIC",
      sql: "mp.churn_probability",
      hint: "0 to 1",
    },
    {
      code: "churn_risk_tier",
      name: "Churn risk tier",
      data_type: "TEXT",
      sql: "mp.churn_risk_tier",
      options: ["low", "medium", "high", "critical"],
    },
    {
      code: "lifecycle_stage",
      name: "Lifecycle stage",
      data_type: "TEXT",
      sql: "mp.lifecycle_stage",
      options: ["prospect", "lead", "customer", "vip", "dormant", "churn_risk"],
    },
    {
      code: "historical_clv",
      name: "Historical CLV",
      data_type: "NUMERIC",
      sql: "mp.historical_clv",
      hint: "campaign currency",
    },
    {
      code: "predictive_clv",
      name: "Predictive CLV",
      data_type: "NUMERIC",
      sql: "mp.predictive_clv",
      hint: "campaign currency",
    },
    {
      code: "lead_conversion_probability",
      name: "Lead conversion probability",
      data_type: "NUMERIC",
      sql: "mp.lead_conversion_probability",
      hint: "0 to 1",
    },
    {
      code: "latest_nps_score",
      name: "Latest NPS",
      data_type: "NUMERIC",
      sql: "mp.latest_nps_score",
      hint: "0 to 10",
    },
    {
      code: "days_since_last_activity",
      name: "Days since last activity",
      data_type: "NUMERIC",
      sql: "(EXTRACT(EPOCH FROM (now() - mp.last_activity_at)) / 86400)",
      hint: "days",
    },
    {
      code: "preferred_channel",
      name: "Preferred channel",
      data_type: "TEXT",
      sql: "mp.preferred_channel",
      options: ["Email", "Mobile App", "Website"],
    },
    {
      code: "email_opt_in",
      name: "Email opt-in",
      data_type: "BOOLEAN",
      sql: "COALESCE((mp.communication_preferences->>'email_opt_in')::boolean, false)",
    },
    {
      code: "push_opt_in",
      name: "Push opt-in",
      data_type: "BOOLEAN",
      sql: "COALESCE((mp.communication_preferences->>'push_opt_in')::boolean, false)",
    },
  ];
  const fieldMap = (fields) =>
    (fields || DEFAULT_FIELDS).reduce((m, f) => ((m[f.code] = f), m), {});
  const list = (v) =>
    String(v == null ? "" : v)
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  const findBy = (arr, k, v) => (arr || []).find((x) => x[k] === v);
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isAction = (n) => n.type === "email" || n.type === "push";

  // Depth-first walk. `path` = the yes/no conditions that lead to a node.
  function walk(node, fn, path, parent) {
    path = path || [];
    fn(node, path, parent);
    (node.children || []).forEach((c) => {
      walk(
        c,
        fn,
        node.type === "condition"
          ? path.concat([{ cond: node.config, branch: c.branch }])
          : path,
        node,
      );
    });
  }
  function conditionText(c, F) {
    const f = F[c.field];
    if (!f) return "Choose a field";
    const v =
      f.data_type === "BOOLEAN"
        ? String(c.value) === "true"
          ? "yes"
          : "no"
        : c.value;
    return (
      f.name +
      " " +
      (OP_WORDS[c.op] || c.op) +
      " " +
      (v === "" || v == null ? "…" : v)
    );
  }

  /* ===================================================================
   * 2. Pure logic (no DOM): evaluate, compile to SQL, validate
   * =================================================================== */
  const ACC = {
    days_since_last_activity: (p) =>
      p.days_since_last_activity != null
        ? +p.days_since_last_activity
        : p.last_activity_at
          ? (Date.now() - Date.parse(p.last_activity_at)) / 864e5
          : null,
    email_opt_in: (p) =>
      String((p.communication_preferences || {}).email_opt_in) === "true",
    push_opt_in: (p) =>
      String((p.communication_preferences || {}).push_opt_in) === "true",
  };
  const getVal = (p, code) =>
    ACC[code] ? ACC[code](p) : p[code] == null ? null : p[code];

  // Same semantics as the generated SQL: NULL never matches (profile falls to the "no" branch).
  function testCond(c, p, F) {
    const f = F[c.field];
    if (!f) return false;
    const v = getVal(p, c.field);
    if (v == null) return false;
    const x = c.value;
    if (c.op === "in") return list(x).indexOf(String(v)) >= 0;
    if (f.data_type === "BOOLEAN")
      return (v === true || v === "true") === (String(x) === "true");
    if (f.data_type === "NUMERIC") {
      const a = +v,
        b = +x;
      return {
        gte: a >= b,
        gt: a > b,
        lte: a <= b,
        lt: a < b,
        eq: a === b,
        neq: a !== b,
      }[c.op];
    }
    return c.op === "neq" ? String(v) !== String(x) : String(v) === String(x);
  }
  const hasToken = (p) =>
    !!p.push_tokens && Object.keys(p.push_tokens).length > 0;
  function suppressed(p, channel, ctx) {
    const cid = ctx.campaign && ctx.campaign.campaign_id;
    return (ctx.suppressions || []).some(
      (s) =>
        s.status === "ACTIVE" &&
        (!s.expires_at || Date.parse(s.expires_at) > Date.now()) &&
        (s.scope === "GLOBAL" || s.campaign_id === cid) &&
        (s.channel === channel || s.channel === "GLOBAL") &&
        ((s.identifier_type === "EMAIL" &&
          s.identifier === String(p.email || "").toLowerCase()) ||
          (s.identifier_type === "PROFILE_ID" &&
            s.identifier === p.master_profile_id)),
    );
  }
  function deliverable(n, p, ctx) {
    const ch = TYPES[n.type].channel;
    const ok =
      ch === "EMAIL"
        ? !!p.email && ACC.email_opt_in(p)
        : hasToken(p) && ACC.push_opt_in(p);
    return ok && !suppressed(p, ch, ctx);
  }

  // Dry run against ctx.profiles (a sample or the full set). Production: implement adapter.previewAudience with the SQL.
  function evaluate(wf, ctx, fields) {
    const F = fieldMap(fields);
    const seg = findBy(ctx.segments, "segment_id", wf.root.config.segment_id);
    const members = (ctx.profiles || []).filter(
      (p) =>
        seg &&
        (p.segmentation_tags || []).indexOf(seg.segment_tag) >= 0 &&
        p.status_code !== 0,
    );
    const nodes = {};
    let unrouted = 0;
    const bump = (id, k) => {
      (nodes[id] = nodes[id] || { reach: 0, deliverable: 0 })[k]++;
    };
    members.forEach((p) => {
      let n = wf.root;
      for (;;) {
        bump(n.id, "reach");
        if (isAction(n)) {
          if (deliverable(n, p, ctx)) bump(n.id, "deliverable");
          return;
        }
        const next =
          n.type === "condition"
            ? (n.children || []).find(
                (c) => c.branch === (testCond(n.config, p, F) ? "yes" : "no"),
              )
            : (n.children || [])[0];
        if (!next) {
          unrouted++;
          return;
        }
        n = next;
      }
    });
    return { total: members.length, unrouted, nodes };
  }

  // One parameterised statement per send node: audience SELECT + dispatch-ledger INSERT.
  function compile(wf, ctx, fields) {
    const F = fieldMap(fields),
      out = [];
    const seg = findBy(ctx.segments, "segment_id", wf.root.config.segment_id);
    if (!seg) return out;
    walk(wf.root, (n, path) => {
      if (!isAction(n)) return;
      const ch = TYPES[n.type].channel,
        tpl = findBy(ctx.templates, "template_id", n.config.template_id);
      const params = [ctx.tenant.tenant_id, seg.segment_tag];
      const w = [
        "mp.tenant_id = $1",
        "mp.status_code = 1",
        "mp.segmentation_tags @> ARRAY[$2]::text[]",
      ];
      path.forEach((s) => {
        const c = s.cond,
          f = F[c.field];
        let p;
        if (c.op === "in") {
          params.push(list(c.value));
          p =
            "COALESCE(" +
            f.sql +
            " = ANY($" +
            params.length +
            "::text[]), false)";
        } else {
          params.push(
            f.data_type === "BOOLEAN"
              ? String(c.value) === "true"
              : f.data_type === "NUMERIC"
                ? Number(c.value)
                : String(c.value),
          );
          p =
            "COALESCE(" +
            f.sql +
            " " +
            OP_SQL[c.op] +
            " $" +
            params.length +
            { BOOLEAN: "::boolean", NUMERIC: "::numeric", TEXT: "::text" }[
              f.data_type
            ] +
            ", false)";
        }
        w.push(s.branch === "no" ? "NOT " + p : p);
      });
      if (ch === "EMAIL") w.push("mp.email IS NOT NULL", ACC_SQL.email);
      else w.push("mp.push_tokens <> '{}'::jsonb", ACC_SQL.push);
      params.push(ctx.campaign.campaign_id);
      const cp = "$" + params.length;
      w.push(
        "NOT EXISTS (\n    SELECT 1 FROM customer360.crm_suppression_list s\n    WHERE s.tenant_id = mp.tenant_id AND s.status = 'ACTIVE'\n" +
          "      AND (s.expires_at IS NULL OR s.expires_at > now())\n      AND (s.scope = 'GLOBAL' OR s.campaign_id = " +
          cp +
          ")\n" +
          "      AND s.channel IN ('" +
          ch +
          "', 'GLOBAL')\n" +
          "      AND ((s.identifier_type = 'EMAIL' AND s.identifier = lower(mp.email))\n" +
          "        OR (s.identifier_type = 'PROFILE_ID' AND s.identifier = mp.master_profile_id::text)))",
      );
      const where = w.join("\n  AND ");
      const audience =
        "SELECT mp.master_profile_id, mp.email, mp.first_name\nFROM customer360.cdp_master_profiles mp\nWHERE " +
        where;
      const ap = params.concat([n.config.template_id, n.id, ch]),
        L = ap.length;
      const activation =
        "INSERT INTO customer360.cdp_campaign_dispatch_logs\n  (tenant_id, campaign_id, master_profile_id, template_id, recipient_email, status, metadata)\n" +
        "SELECT mp.tenant_id, " +
        cp +
        "::uuid, mp.master_profile_id, $" +
        (L - 2) +
        "::uuid, " +
        (ch === "EMAIL" ? "mp.email" : "NULL") +
        ", 'Pending',\n       jsonb_build_object('workflow_node_id', $" +
        (L - 1) +
        "::text, 'channel', $" +
        L +
        "::text)\n" +
        "FROM customer360.cdp_master_profiles mp\nWHERE " +
        where +
        "\nON CONFLICT (campaign_id, master_profile_id) DO NOTHING";
      out.push({
        node_id: n.id,
        channel: ch,
        template_id: n.config.template_id,
        template_name: tpl ? tpl.name : null,
        audience_sql: audience + ";",
        audience_params: params,
        activation_sql: activation + ";",
        activation_params: ap,
      });
    });
    return out;
  }
  const ACC_SQL = {
    email:
      "COALESCE((mp.communication_preferences->>'email_opt_in')::boolean, false)",
    push: "COALESCE((mp.communication_preferences->>'push_opt_in')::boolean, false)",
  };

  function validate(wf, ctx, fields) {
    const F = fieldMap(fields),
      issues = [];
    const add = (n, level, msg) => issues.push({ node: n && n.id, level, msg });
    if (!wf || !wf.root)
      return [
        {
          level: "error",
          msg: "Add a customer segment to start the workflow.",
        },
      ];
    let sends = 0;
    walk(wf.root, (n) => {
      const kids = n.children || [];
      if (n.type === "segment") {
        if (!findBy(ctx.segments, "segment_id", n.config.segment_id))
          add(n, "error", "Choose a customer segment.");
        if (!kids.length)
          add(
            n,
            "error",
            "Connect an If / else or a send block under the segment.",
          );
      } else if (n.type === "condition") {
        const c = n.config,
          f = F[c.field];
        if (!f) add(n, "error", "Choose a field for this If / else.");
        else if (
          f.data_type !== "BOOLEAN" &&
          (c.value === "" ||
            c.value == null ||
            (f.data_type === "NUMERIC" && isNaN(+c.value)))
        )
          add(n, "error", 'Enter a value for "' + f.name + '".');
        if (!kids.length)
          add(n, "error", "Add at least one branch under this If / else.");
        else if (kids.length === 1)
          add(
            n,
            "warn",
            "The " +
              (kids[0].branch === "yes" ? "No" : "Yes") +
              " branch is empty, so those profiles are not contacted.",
          );
        if (kids.some((k) => !k.branch))
          add(n, "error", "Each branch needs to be Yes or No.");
      } else {
        sends++;
        const tpl = findBy(ctx.templates, "template_id", n.config.template_id);
        if (!tpl) add(n, "error", "Choose a template.");
        else if (
          String(tpl.message_type).toUpperCase() !== TYPES[n.type].channel
        )
          add(
            n,
            "error",
            "This template is not a " + TYPES[n.type].channel + " template.",
          );
        else if (tpl.status !== "Approved")
          add(
            n,
            "error",
            'Template "' +
              tpl.name +
              '" is ' +
              tpl.status +
              ". Only Approved templates can be sent.",
          );
        if (kids.length)
          add(
            n,
            "error",
            "A send block ends its branch. Remove the blocks below it.",
          );
      }
    });
    if (!sends)
      add(null, "error", "Add at least one Send email or Send web push block.");
    return issues;
  }

  /* ===================================================================
   * 3. UI
   * =================================================================== */
  const PALETTE = [
    { type: "segment", label: "Customer segment", description: "Who enters the journey" },
    { type: "condition", label: "If / else", description: "Split on a profile field" },
    { type: "email", label: "Send email", description: "Approved EMAIL template" },
    { type: "push", label: "Send web push", description: "Approved PUSH template" },
  ];
  const WORKFLOW_TABS = [
    { id: "inspector", label: "Inspector" },
    { id: "audience", label: "Audience" },
    { id: "sql", label: "SQL" },
    { id: "report", label: "Report" },
    { id: "json", label: "JSON" },
  ];

  function mount(host, opts) {
    opts = opts || {};
    const adapter = opts.adapter;
    if (!adapter)
      throw new Error("CampaignWorkflow.mount: opts.adapter is required");
    if (typeof jQuery === "undefined")
      throw new Error("CampaignWorkflow.mount: jQuery 3.7.1 or 4.x is required");
    assertSupportedJQuery();
    if (!workflowTemplates)
      throw new Error("CampaignWorkflow.mount: workflow-template.js is not loaded");
    if (typeof flowy === "undefined")
      throw new Error(
        "Flowy is not loaded. Include flowy.js before campaign-workflow.js",
      );
    const SNAP_MS = opts.snapDelay == null ? 70 : opts.snapDelay;
    let ctx,
      F,
      wfBase = "",
      uid = 0,
      issues = [],
      counts = null,
      busyNow = false,
      tab = "inspector",
      reportCache = null;
    const state = { nodes: {}, sel: null };
    const handlers = {};
    const $host = jQuery(host);
    if (!$host.length)
      throw new TypeError("CampaignWorkflow.mount: host must be a DOM element");
    const $ = (selector) =>
      selector && selector.nodeType ? jQuery(selector) : $host.find(selector);
    const money = (v, compact) => {
      const cur = (ctx.campaign && ctx.campaign.currency) || "VND";
      try {
        return new Intl.NumberFormat(opts.locale || "en", {
          style: "currency",
          currency: cur,
          maximumFractionDigits: compact ? 1 : 0,
          notation: compact ? "compact" : "standard",
        }).format(v);
      } catch (e) {
        return String(Math.round(v));
      }
    };
    const num = (v) => Number(v || 0).toLocaleString(opts.locale || "en");
    const emit = (e, d) => (handlers[e] || []).forEach((fn) => fn(d));

    const paletteItems = PALETTE.map((item) => ({
      ...item,
      code: TYPES[item.type].code,
    }));
    $host.html(
      workflowTemplates.builder({ palette: paletteItems, tabs: WORKFLOW_TABS }),
    );
    const $canvas = $("#cw-canvas");
    const canvas = $canvas[0];

    function toast(m) {
      const t = $("[data-r=toast]");
      t.text(m).addClass("on");
      clearTimeout(toast.h);
      toast.h = setTimeout(() => t.removeClass("on"), 3200);
    }
    function busy(b) {
      busyNow = b;
      $("[data-r=busy]").prop("hidden", !b);
    }
    const nodeEl = (fid) => {
      return $canvas.find('.blockid[value="' + fid + '"]').parent();
    };
    const flowyBlocks = () => flowy.output().blockarr;
    const childrenOf = (fid) =>
      flowyBlocks().filter((b) => String(b.parent) === String(fid));
    const hasNodes = () => flowyBlocks().length > 0;

    /* ---------- node content ---------- */
    function renderWorkflowNode(node) {
      let title = "";
      let subtitle = "";
      if (node.type === "segment") {
        const segment = findBy(ctx.segments, "segment_id", node.cfg.segment_id);
        title = segment ? segment.segment_name : "Choose a segment";
        subtitle = segment ? num(segment.member_count) + " profiles" : "";
      } else if (node.type === "condition") {
        const field = F[node.cfg.field];
        title = field ? field.name : "Choose a field";
        subtitle = field ? conditionText(node.cfg, F).slice(field.name.length + 1) : "";
      } else {
        const messageTemplate = findBy(ctx.templates, "template_id", node.cfg.template_id);
        title = messageTemplate ? messageTemplate.name : "Choose a template";
        subtitle = messageTemplate ? messageTemplate.subject || "" : "";
      }
      const nodeAudienceCounts = counts && counts.nodes[node.uid];
      return workflowTemplates.nodeBody({
        type: node.type,
        label: TYPES[node.type].label,
        branch: node.branch,
        branchLabel: node.branch === "yes" ? "Yes" : "No",
        title,
        subtitle,
        hasCounts: !!nodeAudienceCounts,
        reach: nodeAudienceCounts ? num(nodeAudienceCounts.reach) : "",
        deliverable: nodeAudienceCounts ? num(nodeAudienceCounts.deliverable) : "",
        isAction: isAction(node),
      });
    }
    function paintWorkflowNodes() {
      const sev = {};
      issues.forEach((i) => {
        if (i.node) sev[i.node] = sev[i.node] === "error" ? "error" : i.level;
      });
      Object.keys(state.nodes).forEach((fid) => {
        const el = nodeEl(fid),
          n = state.nodes[fid];
        if (!el.length) return;
        el.find(".cw-nb").html(renderWorkflowNode(n));
        el.toggleClass("is-sel", String(fid) === String(state.sel));
        el.toggleClass("is-error", sev[n.uid] === "error");
        el.toggleClass("is-warn", sev[n.uid] === "warn");
      });
      $("[data-r=empty]").prop("hidden", hasNodes());
    }

    /* ---------- model <-> Flowy ---------- */
    function createDefaultNodeConfiguration(type) {
      if (type === "segment")
        return {
          segment_id:
            (ctx.campaign && ctx.campaign.segment_id) ||
            (ctx.segments[0] || {}).segment_id ||
            null,
        };
      if (type === "condition")
        return { field: Object.keys(F)[0], op: "gte", value: 50 };
      const t = ctx.templates.find(
        (x) =>
          String(x.message_type).toUpperCase() === TYPES[type].channel &&
          x.status === "Approved",
      );
      return { template_id: t ? t.template_id : null };
    }
    function normalizeConditionBranches() {
      const a = flowyBlocks();
      a.forEach((b) => {
        const n = state.nodes[b.id],
          p = b.parent < 0 ? null : state.nodes[b.parent];
        if (n && (!p || p.type !== "condition")) n.branch = null;
      });
      a.filter(
        (b) => state.nodes[b.id] && state.nodes[b.id].type === "condition",
      ).forEach((c) => {
        const kids = a
            .filter((b) => b.parent === c.id)
            .sort((x, y) => x.x - y.x),
          used = {};
        kids.forEach((k) => {
          const n = state.nodes[k.id];
          if (n.branch && !used[n.branch]) used[n.branch] = 1;
          else n.branch = null;
        });
        kids.forEach((k) => {
          const n = state.nodes[k.id];
          if (!n.branch) {
            n.branch = !used.yes ? "yes" : !used.no ? "no" : null;
            if (n.branch) used[n.branch] = 1;
          }
        });
      });
    }
    function getWorkflow() {
      const a = flowyBlocks();
      if (!a.length) return null;
      const build = (b) => {
        const n = state.nodes[b.id];
        const kids = a
          .filter((c) => c.parent === b.id)
          .sort((x, y) => x.x - y.x)
          .map(build);
        if (n.type === "condition")
          kids.sort(
            (x, y) =>
              (x.branch === "yes" ? 0 : x.branch === "no" ? 1 : 2) -
              (y.branch === "yes" ? 0 : y.branch === "no" ? 1 : 2),
          );
        const o = { id: n.uid, type: n.type };
        if (n.branch) o.branch = n.branch;
        o.config = clone(n.cfg);
        o.children = kids;
        return o;
      };
      return {
        schema_version: 1,
        campaign_id: ctx.campaign.campaign_id,
        root: build(a.find((b) => b.parent === -1)),
      };
    }
    // Light update: no inspector re-render, so typing keeps focus.
    function updateValidationAndDirtyState() {
      const wf = getWorkflow();
      issues = validate(wf, ctx, ctx.fields);
      paintWorkflowNodes();
      const pill = $("[data-r=valid]"),
        errs = issues.filter((i) => i.level === "error").length,
        warns = issues.length - errs;
      pill.className = "cw-pill " + (errs ? "bad" : warns ? "warn" : "ok");
      pill.text(errs
        ? errs + (errs === 1 ? " issue to fix" : " issues to fix")
        : warns
          ? warns + " warning" + (warns > 1 ? "s" : "")
          : "Ready to save");
      $("[data-r=dirty]").prop("hidden", JSON.stringify(wf) === wfBase);
      return wf;
    }
    function refreshWorkflowUI() {
      normalizeConditionBranches();
      const wf = updateValidationAndDirtyState();
      renderPane();
      emit("change", wf);
      return wf;
    }
    let timer;
    const schedule = () => {
      clearTimeout(timer);
      timer = setTimeout(refresh, 60);
    };

    function onSnap(drag, first, parentEl) {
      const $drag = $(drag);
      const code = $drag.find(".blockelemtype").val(),
        type = CODE[code],
        fid = $drag.find(".blockid").val();
      if (first && type !== "segment") {
        toast("Start the workflow with a customer segment.");
        return false;
      }
      if (!first && type === "segment") {
        toast("A workflow has one customer segment.");
        return false;
      }
      if (!first) {
        const pid = $(parentEl).find(".blockid").val(),
          pt = state.nodes[pid],
          kids = childrenOf(pid).length;
        if (isAction(pt)) {
          toast(
            "A send block ends its branch. Add blocks under an If / else instead.",
          );
          return false;
        }
        if (pt.type === "condition" && kids >= 2) {
          toast("An If / else has two branches: Yes and No.");
          return false;
        }
        if (pt.type === "segment" && kids >= 1) {
          toast("Connect one If / else or send block under the segment.");
          return false;
        }
      }
      state.nodes[fid] = {
        uid: "n" + ++uid,
        type,
        branch: null,
        cfg: createDefaultNodeConfiguration(type),
      };
      $drag
        .attr("class", "block cw-node cw-t-" + type)
        .html(workflowTemplates.workflowNode({ code, blockId: fid }));
      if (!busyNow) {
        state.sel = fid;
        schedule();
      }
      return true;
    }
    flowy(
      canvas,
      () => {},
      () => {},
      onSnap,
      () => {
        schedule();
        return false;
      },
      24,
      64,
    );

    // Scripted drop that goes through Flowy's own snapping, so layout and arrows are always Flowy's.
    async function dragNew(type, parentFid) {
      const $item = $('.cw-pitem[data-type="' + type + '"]');
      if (!$item.length) throw new Error("Unknown workflow block type: " + type);
      const item = $item[0];
      const itemOffset = $item.offset();
      const itemLeft = itemOffset.left - window.scrollX;
      const itemTop = itemOffset.top - window.scrollY;
      const canvasRect = canvas.getBoundingClientRect();
      let x = canvasRect.left + canvasRect.width / 2 - $item.outerWidth() / 2;
      let y = canvasRect.top + 40;
      if (parentFid != null) {
        const $parent = nodeEl(parentFid);
        if (!$parent.length) throw new Error("Could not find parent block " + parentFid);
        const parentRect = $parent[0].getBoundingClientRect();
        x = parentRect.left + parentRect.width / 2 - $item.outerWidth() / 2;
        y = parentRect.bottom + 8;
      }
      const before = flowyBlocks().length,
        ev = (t, el) =>
          el.dispatchEvent(
            new MouseEvent(t, {
              bubbles: true,
              cancelable: true,
              clientX: t === "mousedown" ? itemLeft + 10 : x + 10,
              clientY: t === "mousedown" ? itemTop + 10 : y + 10,
              button: 0,
              which: 1,
              view: window,
            }),
          );
      ev("mousedown", item);
      await sleep(20);
      ev("mousemove", document);
      await sleep(20);
      ev("mouseup", document);
      await sleep(SNAP_MS);
      const a = flowyBlocks();
      return a.length > before
        ? Math.max.apply(
            null,
            a.map((b) => b.id),
          )
        : null;
    }
    function clearCanvas() {
      flowy.deleteBlocks();
      state.nodes = {};
      state.sel = null;
      counts = null;
    }
    async function renderWorkflow(wf) {
      busy(true);
      try {
        clearCanvas();
        uid = 0;
        const ids = [];
        walk(wf.root, (n) =>
          ids.push(parseInt(String(n.id).replace(/\D/g, ""), 10) || 0),
        );
        uid = Math.max.apply(null, ids.concat(0));
        const q = [[wf.root, null]];
        while (q.length) {
          const [n, p] = q.shift(),
            fid = await dragNew(n.type, p);
          if (fid == null) throw new Error("Could not place node " + n.id);
          Object.assign(state.nodes[fid], {
            uid: n.id,
            branch: n.branch || null,
            cfg: clone(n.config || {}),
          });
          (n.children || [])
            .slice()
            .sort((x, y) => (x.branch === "no") - (y.branch === "no"))
            .forEach((c) => q.push([c, fid]));
        }
      } finally {
        busy(false);
      }
      state.sel = null;
      refreshWorkflowUI();
    }
    async function addNode(type) {
      if (busyNow) return;
      if (!hasNodes()) {
        if (type !== "segment")
          return toast("Start the workflow with a customer segment.");
      } else if (type === "segment")
        return toast("A workflow has one customer segment.");
      else if (state.sel == null)
        return toast(
          "Select the node this block should follow, then click a block.",
        );
      const fid = await dragNew(type, hasNodes() ? state.sel : null);
      if (fid != null) {
        state.sel = String(fid);
        refreshWorkflowUI();
      }
    }
    async function deleteSelected() {
      const n = state.nodes[state.sel];
      if (!n || busyNow) return;
      const wf = getWorkflow();
      if (n.type === "segment") {
        clearCanvas();
        return refreshWorkflowUI();
      }
      (function strip(x) {
        x.children = (x.children || []).filter((c) => c.id !== n.uid);
        x.children.forEach(strip);
      })(wf.root);
      await renderWorkflow(wf);
    }

    /* ---------- inspector + tabs ---------- */
    const sampleVars = () => {
      const p = (ctx.profiles || [])[0] || {};
      return {
        first_name: p.first_name || "Alex",
        persona_name: p.persona_name || "",
        unsubscribe_url: "#",
      };
    };
    const fill = (s, v) =>
      String(s || "").replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k) =>
        v[k] != null ? v[k] : m,
      );
    function renderInspector() {
      const selectedNode = state.nodes[state.sel];
      if (!selectedNode) {
        return workflowTemplates.checklistInspector({
          hasIssues: issues.length > 0,
          issuesHtml: issues.length ? workflowTemplates.issues({ issues }) : "",
        });
      }

      const nodeIssues = issues.filter((issue) => issue.node === selectedNode.uid);
      const viewModel = {
        type: selectedNode.type,
        label: TYPES[selectedNode.type].label,
        id: selectedNode.uid,
        issuesHtml: nodeIssues.length ? workflowTemplates.issues({ issues: nodeIssues }) : "",
        isSegment: selectedNode.type === "segment",
        isCondition: selectedNode.type === "condition",
        isAction: isAction(selectedNode),
        deleteLabel: selectedNode.type === "segment" ? "workflow" : "node and branch",
      };

      if (viewModel.isSegment) {
        const selectedSegment = findBy(ctx.segments, "segment_id", selectedNode.cfg.segment_id);
        viewModel.segments = ctx.segments.map((segment) => ({
          ...segment,
          selected: segment.segment_id === selectedNode.cfg.segment_id,
        }));
        viewModel.segment = selectedSegment;
        viewModel.memberCount = selectedSegment ? num(selectedSegment.member_count) : "";
      } else if (viewModel.isCondition) {
        const selectedField = F[selectedNode.cfg.field] || {};
        viewModel.fields = Object.keys(F).map((fieldCode) => ({
          code: fieldCode,
          name: F[fieldCode].name,
          selected: fieldCode === selectedNode.cfg.field,
        }));
        viewModel.operators = (OPS_BY_TYPE[selectedField.data_type] || []).map((operator) => ({
          value: operator,
          label: OP_LABEL[operator] + (OP_WORDS[operator] ? " (" + OP_WORDS[operator] + ")" : ""),
          selected: operator === selectedNode.cfg.op,
        }));
        viewModel.booleanValue = selectedField.data_type === "BOOLEAN";
        viewModel.valueIsTrue = String(selectedNode.cfg.value) === "true";
        viewModel.valueIsFalse = String(selectedNode.cfg.value) === "false";
        viewModel.optionValues = selectedField.options && selectedNode.cfg.op !== "in"
          ? selectedField.options.map((option) => ({
              value: option,
              label: option,
              selected: String(selectedNode.cfg.value) === option,
            }))
          : [];
        viewModel.numericValue = selectedField.data_type === "NUMERIC";
        viewModel.hint = selectedField.hint || "";
        viewModel.placeholder = selectedField.options ? selectedField.options.slice(0, 2).join(", ") : "";
        viewModel.value = selectedNode.cfg.value == null ? "" : selectedNode.cfg.value;
        viewModel.conditionSummary = conditionText(selectedNode.cfg, F);
      } else {
        const channel = TYPES[selectedNode.type].channel;
        const previewVariables = sampleVars();
        const selectedTemplate = findBy(ctx.templates, "template_id", selectedNode.cfg.template_id);
        viewModel.templates = ctx.templates
          .filter((template) => String(template.message_type).toUpperCase() === channel)
          .map((template) => ({
            ...template,
            selected: template.template_id === selectedNode.cfg.template_id,
            disabled: template.status !== "Approved",
            statusNote: template.status !== "Approved",
          }));
        viewModel.emailPreview = !!selectedTemplate && channel === "EMAIL";
        viewModel.pushPreview = !!selectedTemplate && channel === "PUSH";
        viewModel.subject = selectedTemplate ? fill(selectedTemplate.subject, previewVariables) : "";
        viewModel.messageBody = selectedTemplate ? fill(selectedTemplate.message_body, previewVariables) : "";
        viewModel.htmlEmail = selectedTemplate && selectedTemplate.html_body
          ? fill(selectedTemplate.html_body, previewVariables)
          : "";
      }

      const selectedFlowyBlock = flowyBlocks().find((block) => String(block.id) === String(state.sel));
      const parentNode = selectedFlowyBlock && state.nodes[selectedFlowyBlock.parent];
      viewModel.branchOptions = !!parentNode && parentNode.type === "condition";
      viewModel.branchIsYes = selectedNode.branch === "yes";
      viewModel.branchIsNo = selectedNode.branch === "no";
      return workflowTemplates.inspector(viewModel);
    }
    function bindInspector() {
      const n = state.nodes[state.sel];
      if (!n) return;
      $("[data-f]").each(function () {
        const $input = $(this);
        $input.on("change", function () {
          const value = $input.val();
          const k = $input.attr("data-f");
          if (k === "branch") {
            n.branch = value;
            const sib = childrenOf(
              (flowyBlocks().find((b) => String(b.id) === String(state.sel)) || {})
                .parent,
            )
              .map((b) => state.nodes[b.id])
              .filter((x) => x !== n && x.branch === n.branch);
            sib.forEach((x) => {
              x.branch = n.branch === "yes" ? "no" : "yes";
            });
          } else if (k === "field") {
            const f = F[value];
            n.cfg = {
              field: value,
              op: OPS_BY_TYPE[f.data_type][0],
              value:
                f.data_type === "BOOLEAN"
                  ? "true"
                  : f.options
                    ? f.options[0]
                    : 0,
            };
          } else if (k === "op" && value === "in") {
            n.cfg.op = "in";
            n.cfg.value = "";
          } else
            n.cfg[k] =
              k === "value" &&
              F[n.cfg.field] &&
              F[n.cfg.field].data_type === "NUMERIC" &&
              value !== ""
                ? Number(value)
                : value || null;
          if (k === "op" && value !== "in") n.cfg.op = value;
          updateValidationAndDirtyState();
          renderPane();
        });
        if ($input.is("input"))
          $input.on("input", () => {
            const k = $input.attr("data-f");
            const value = $input.val();
            n.cfg[k] =
              (F[n.cfg.field] || {}).data_type === "NUMERIC" && value !== ""
                ? Number(value)
                : value;
            updateValidationAndDirtyState();
          });
      });
      const d = $("[data-a=delete]");
      d.on("click", deleteSelected);
    }
    function renderPane() {
      const pane = $("[data-r=pane]");
      $("[data-tab]").each(function () {
        $(this).attr("aria-selected", $(this).attr("data-tab") === tab);
      });
      const wf = getWorkflow(),
        errs = issues.some((i) => i.level === "error");
      if (tab === "inspector") {
        pane.html(renderInspector());
        bindInspector();
      } else if (tab === "audience") {
        if (!counts || !wf) {
          pane.html(workflowTemplates.audienceEmpty());
        } else {
          const audienceRows = [];
          walk(wf.root, (n) => {
            if (isAction(n)) {
              const nodeCounts = counts.nodes[n.id] || { reach: 0, deliverable: 0 };
              const messageTemplate = findBy(ctx.templates, "template_id", n.config.template_id);
              audienceRows.push({
                id: n.id,
                name: messageTemplate ? messageTemplate.name : n.id,
                channel: TYPES[n.type].channel,
                reach: num(nodeCounts.reach),
                deliverable: num(nodeCounts.deliverable),
                rate: nodeCounts.reach
                  ? Math.round((100 * nodeCounts.deliverable) / nodeCounts.reach) + "%"
                  : "–",
              });
            }
          });
          pane.html(workflowTemplates.audienceResults({
            total: num(counts.total),
            unrouted: num(counts.unrouted),
            rows: audienceRows,
          }));
        }
      } else if (tab === "sql") {
        if (!wf || errs)
          pane.html(workflowTemplates.sqlUnavailable());
        else {
          const statements = compile(wf, ctx, ctx.fields).map((statement) => ({
            nodeId: statement.node_id,
            channel: statement.channel,
            templateName: statement.template_name || "",
            audienceCode: workflowTemplates.sqlCode({
              sql: statement.audience_sql,
              params: JSON.stringify(statement.audience_params),
            }),
            activationCode: workflowTemplates.sqlCode({
              sql: statement.activation_sql,
              params: JSON.stringify(statement.activation_params),
            }),
          }));
          pane.html(workflowTemplates.sqlResults({ statements }));
        }
      } else if (tab === "report") {
        if (!reportCache) {
          pane.html(workflowTemplates.reportLoading());
          loadReport();
        } else pane.html(renderCampaignReport(reportCache, wf));
      } else if (tab === "json") {
        pane.html(workflowTemplates.workflowJson({
          workflowJson: JSON.stringify(wf, null, 2),
        }));
      }
    }
    function renderSparkChart(values, color, label) {
      const width = 200;
      const height = 44;
      const maxValue = Math.max.apply(null, values.concat(1));
      const barWidth = width / Math.max(values.length, 1);
      const bars = values.map((value, index) => {
        const barHeight = (value / maxValue) * height;
        return {
          x: index * barWidth + 1,
          y: height - barHeight,
          width: barWidth - 2,
          height: barHeight,
          color,
        };
      });
      return workflowTemplates.sparkChart({ width, height, label, bars });
    }
    function renderCampaignReport(report, workflow) {
      const metrics = report.metrics || {};
      const dailyReports = (report.daily || []).slice().sort((left, right) => {
        const leftDate = String(left.report_date);
        const rightDate = String(right.report_date);
        return leftDate < rightDate ? -1 : leftDate > rightDate ? 1 : 0;
      });
      const messageLabelsByNode = {};
      if (workflow) {
        walk(workflow.root, (node) => {
          if (!isAction(node)) return;
          const messageTemplate = findBy(
            ctx.templates,
            "template_id",
            node.config.template_id,
          );
          messageLabelsByNode[node.id] =
            (messageTemplate ? messageTemplate.name : node.id) +
            " (" +
            TYPES[node.type].channel +
            ")";
        });
      }

      const deliveryByNode = {};
      (report.dispatch || []).forEach((dispatch) => {
        (deliveryByNode[dispatch.node_id] =
          deliveryByNode[dispatch.node_id] || {})[dispatch.status] =
          Number(dispatch.count);
      });
      const deliveryStatuses = ["Sent", "Failed", "Pending", "Skipped", "Suppressed"];
      const deliveryRows = Object.keys(deliveryByNode)
        .sort()
        .map((nodeId) => ({
          id: nodeId,
          label: messageLabelsByNode[nodeId] || nodeId,
          counts: deliveryStatuses.map((status) => deliveryByNode[nodeId][status] || 0),
        }));

      const charts = [
        { label: "Clicks", field: "clicks", color: "#4f46e5" },
        { label: "Conversions", field: "conversions", color: "#0e8f7e" },
        { label: "Revenue", field: "revenue_estimated", color: "#d6336c" },
      ].map((chart) => ({
        label: chart.label,
        chart: renderSparkChart(
          dailyReports.map((dailyReport) => Number(dailyReport[chart.field])),
          chart.color,
          chart.label + " per day",
        ),
      }));

      const kpis = [
        { value: money(metrics.total_spend || 0, true), label: "Spend" },
        { value: num(metrics.total_impressions), label: "Impressions" },
        {
          value: num(metrics.total_clicks),
          label: "Clicks",
          detail: (metrics.ctr_percentage || 0) + "% CTR",
        },
        {
          value: num(metrics.total_conversions),
          label: "Conversions",
          detail: (metrics.cvr_percentage || 0) + "% CVR",
        },
        { value: money(metrics.total_revenue || 0, true), label: "Revenue" },
        {
          value: (metrics.roas || 0) + "x",
          label: "ROAS",
          detail: "CPA " + money(metrics.cpa || 0, true),
        },
      ];

      return workflowTemplates.campaignReport({
        kpis,
        charts,
        statuses: deliveryStatuses,
        dispatchRows: deliveryRows,
      });
    }
    async function loadReport() {
      try {
        reportCache = await adapter.getReport(ctx.campaign.campaign_id);
      } catch (e) {
        reportCache = { metrics: {}, daily: [], dispatch: [] };
        toast("Could not load report: " + e.message);
      }
      if (tab === "report") renderPane();
    }
    async function preview() {
      const wf = getWorkflow();
      if (!wf) return toast("Add a customer segment first.");
      if (issues.some((i) => i.level === "error")) {
        tab = "inspector";
        state.sel = null;
        renderPane();
        return toast("Fix the issues first.");
      }
      counts = adapter.previewAudience
        ? await adapter.previewAudience(wf)
        : evaluate(wf, ctx, ctx.fields);
      tab = "audience";
      paintWorkflowNodes();
      renderPane();
    }
    async function save() {
      const wf = getWorkflow();
      if (!wf) return toast("Nothing to save yet.");
      if (issues.some((i) => i.level === "error")) {
        tab = "inspector";
        state.sel = null;
        renderPane();
        return toast("Fix the issues before saving.");
      }
      try {
        await adapter.saveWorkflow(ctx.campaign.campaign_id, wf, {
          statements: compile(wf, ctx, ctx.fields),
        });
        wfBase = JSON.stringify(wf);
        updateValidationAndDirtyState();
        toast("Workflow saved.");
        emit("save", wf);
      } catch (e) {
        toast("Save failed: " + e.message);
      }
    }
    const copy = (t) =>
      (navigator.clipboard
        ? navigator.clipboard.writeText(t)
        : Promise.reject()
      ).then(
        () => toast("Copied."),
        () => toast("Copy failed. Select the text and copy manually."),
      );

    /* ---------- events ---------- */
    $host.on("click", (e) => {
      const $target = $(e.target);
      const $action = $target.closest("[data-a]");
      const $tab = $target.closest("[data-tab]");
      const $paletteItem = $target.closest(".cw-pitem");
      const $node = $target.closest(".cw-node");
      if ($tab.length) {
        tab = $tab.attr("data-tab");
        return renderPane();
      }
      if ($action.length) {
        const k = $action.attr("data-a");
        if (k === "preview") preview();
        else if (k === "save") save();
        else if (k === "reload") {
          reportCache = null;
          renderPane();
        } else if (k === "copysql") {
          const wf = getWorkflow();
          copy(
            compile(wf, ctx, ctx.fields)
              .map(
                (s) =>
                  "-- " +
                  s.node_id +
                  "\n" +
                  s.audience_sql +
                  "\n-- params: " +
                  JSON.stringify(s.audience_params) +
                  "\n" +
                  s.activation_sql +
                  "\n-- params: " +
                  JSON.stringify(s.activation_params),
              )
              .join("\n\n"),
          );
        } else if (k === "copyjson") copy($(".cw-json").val());
        else if (k === "applyjson") {
          try {
            const j = JSON.parse($(".cw-json").val());
            if (!j.root) throw new Error('missing "root"');
            renderWorkflow(j);
          } catch (err) {
            toast("Invalid JSON: " + err.message);
          }
        }
        return;
      }
      if ($paletteItem.length) return addNode($paletteItem.attr("data-type"));
      if ($node.length) {
        state.sel = $node.find(".blockid").val();
        tab = "inspector";
        paintWorkflowNodes();
        renderPane();
      } else if (
        $target.closest(".cw-stage").length &&
        !$node.length
      ) {
        if (state.sel != null) {
          state.sel = null;
          paintWorkflowNodes();
          renderPane();
        }
      }
    });
    $host.on("keydown", (e) => {
      const $paletteItem = $(e.target).closest(".cw-pitem");
      if ($paletteItem.length && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        addNode($paletteItem.attr("data-type"));
      }
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        state.sel != null &&
        !$(e.target).is("input, select, textarea")
      ) {
        e.preventDefault();
        deleteSelected();
      }
      if (e.key === "Escape" && state.sel != null) {
        state.sel = null;
        paintWorkflowNodes();
        renderPane();
      }
    });
    // Typing in the inspector must never start a Flowy drag.
    ["mousedown", "touchstart"].forEach((ev) =>
      $(".cw-side").on(ev, (e) => e.stopPropagation()),
    );

    /* ---------- boot ---------- */
    return Promise.resolve(adapter.getContext()).then(async (c) => {
      ctx = c;
      ctx.fields = c.fields || DEFAULT_FIELDS;
      F = fieldMap(ctx.fields);
      $("[data-r=title]").text(ctx.campaign.name);
      $("[data-r=chips]").html(workflowTemplates.chips({
        chips: [ctx.campaign.status, ctx.campaign.approval_status].filter(Boolean),
      }));
      const wf = await adapter.loadWorkflow(ctx.campaign.campaign_id);
      if (wf && wf.root) await renderWorkflow(wf);
      else refreshWorkflowUI();
      wfBase = JSON.stringify(getWorkflow());
      updateValidationAndDirtyState();
      return {
        getWorkflow,
        setWorkflow: renderWorkflow,
        validate: () => validate(getWorkflow(), ctx, ctx.fields),
        compile: () => compile(getWorkflow(), ctx, ctx.fields),
        previewAudience: preview,
        save,
        on: (e, fn) => {
          (handlers[e] = handlers[e] || []).push(fn);
        },
        context: ctx,
      };
    });
  }

  return { mount, compile, evaluate, validate, walk, DEFAULT_FIELDS, TYPES };
});


(function initCampaignWorkflowDemo() {
  "use strict";
  if (typeof document === "undefined") return;
  if (typeof jQuery === "undefined")
    throw new Error("Campaign workflow demo requires jQuery 3.7.1 or 4.x.");
  assertSupportedJQuery();
  if (typeof window.WorkflowTemplates === "undefined")
    throw new Error("Load workflow-template.js before campaign-workflow.js.");
  if (!jQuery("#canvas").length) return;

  const $ = jQuery;
  const workflowTemplates = window.WorkflowTemplates;
  function validateProfiles(list) {
    if (!Array.isArray(list) || !list.length) throw new Error("Expected a non-empty array");
    list.forEach((profile, index) => {
      const label = "Profile " + (index + 1);
      if (!profile || typeof profile !== "object" || Array.isArray(profile)) {
        throw new Error(label + " must be an object");
      }
      if (!Number.isFinite(profile.id)) throw new Error(label + ' must have a numeric "id"');
      if (typeof profile.name !== "string") throw new Error(label + ' must have a string "name"');
      ["score", "cart", "recency"].forEach((key) => {
        if (!Number.isFinite(profile[key])) throw new Error(label + ' must have a numeric "' + key + '"');
      });
      if (profile.push !== 0 && profile.push !== 1) {
        throw new Error(label + ' must have "push" set to 0 or 1');
      }
    });
    return list;
  }
  $.ajax({
    url: "sample.json",
    dataType: "json",
  })
    .done((sample) => {
      try {
        const profiles = validateProfiles(sample.profiles);
        if (!sample.workflow || typeof sample.workflow !== "object" || !sample.workflow.type) {
          throw new Error('The sample is missing a valid "workflow".');
        }
        initializeDemo(profiles, sample.workflow);
      } catch (error) {
        showLoadError(error.message || String(error));
      }
    })
    .fail((request, status, error) => {
      showLoadError(error || status || "Request failed");
    });

  function showLoadError(message) {
    const detail = window.location.protocol === "file:"
      ? "Serve this page over HTTP to load sample.json with AJAX."
      : "Could not load sample.json: " + message;
    $("#toast").text(detail).addClass("on");
    $("#segTable").text(detail);
  }

  $("#bSample").prop("disabled", true);
  $("#segTable").text("Loading sample data…");
  function initializeDemo(sampleProfiles, sampleWorkflow) {

/* ---------- Input: customer segment (sample profiles) ---------- */
      let profileRecords = sampleProfiles;
      const sampleWorkflowTree = sampleWorkflow;
      const conditionFields = {
          score: "Engagement score",
          cart: "Cart value ($)",
          recency: "Days since purchase",
          push: "Push opt-in (1 = yes)"
      };
      const conditionOperators = { ge: "≥", lt: "<", eq: "=" };

      /* ---------- Block templates ---------- */
      const demoBlockMarkupByType = {
          1: () => workflowTemplates.demoBlockSegment({
              profileCount: profileRecords.length
          }),
          2: () => workflowTemplates.demoBlockCondition({
              fields: Object.entries(conditionFields).map(([value, label]) => ({ value, label })),
              operators: Object.entries(conditionOperators).map(([value, label]) => ({ value, label }))
          }),
          3: () => workflowTemplates.demoBlockEmail({
              subject: "{{first_name}}, your cart is waiting",
              body: "Complete your order today."
          }),
          4: () => workflowTemplates.demoBlockPush({
              subject: "Still thinking it over?",
              body: "Your cart is one tap away."
          })
      };
      const $canvas = $("#canvas");
      const canvas = $canvas[0];
      const getFlowBlockElement = (blockId) =>
          $canvas.find('.blockid[value="' + blockId + '"]').parent();
      const getFlowBlockType = (blockId) =>
          getFlowBlockElement(blockId).find(".blockelemtype").val();
      const getFlowBlockFieldValue = (blockId, fieldClass) =>
          getFlowBlockElement(blockId).find("." + fieldClass).val() || "";
      const getFlowBlockLeft = (blockId) => getFlowBlockElement(blockId).offset().left;
      const getOrderedChildFlowBlocks = (flowBlocks, parentBlockId) =>
          flowBlocks
              .filter((block) => block.parent === parentBlockId)
              .sort((leftBlock, rightBlock) =>
                  getFlowBlockLeft(leftBlock.id) - getFlowBlockLeft(rightBlock.id),
              );
      function toast(m) { const t = $("#toast"); t.text(m).addClass("on"); clearTimeout(toast.h); toast.h = setTimeout(() => t.removeClass("on"), 2600) }
      const exactProfileCountFormatter = new Intl.NumberFormat();
      const compactProfileCountFormatter = new Intl.NumberFormat(undefined, {
          notation: "compact",
          maximumFractionDigits: 1
      });
      function updateBlockReachLabel(flowNodeId, profileCount) {
          const exactCount = exactProfileCountFormatter.format(profileCount);
          const compactCount = compactProfileCountFormatter.format(profileCount);
          const visibleCount = profileCount >= 1000
              ? compactCount
              : exactCount + " profiles";
          getFlowBlockElement(flowNodeId).find(".hits")
              .text(visibleCount)
              .attr("title", exactCount + " profiles reached")
              .attr("aria-label", exactCount + " profiles reached");
      }
      function clearBlockReachLabels() {
          $(".hits").text("").removeAttr("title aria-label");
      }

      /* ---------- Flowy ---------- */
      if (typeof flowy === "undefined") {
          $("body").prepend(workflowTemplates.flowyError({ message: "Flowy failed to load." }));
          throw new Error("flowy missing");
      }
      function onSnap(drag, first, parentEl) {
          const $drag = $(drag);
          const type = $drag.find(".blockelemtype").val();
          if (first && type !== "1") { toast("Start the workflow with a customer segment"); return false }
          if (!first && type === "1") { toast("A workflow has one customer segment"); return false }
          if (!first) {
              const parentId = Number($(parentEl).find(".blockid").val());
              const parentType = getFlowBlockType(parentId);
              const childCount = flowy.output().blockarr.filter(block => block.parent === parentId).length;
              if (parentType === "3" || parentType === "4") {
                  toast("A send block ends its branch.");
                  return false;
              }
              if (parentType === "1" && childCount >= 1) {
                  toast("Connect one If / else or send block under the segment.");
                  return false;
              }
              if (parentType === "2" && childCount >= 2) {
                  toast("An If / else has two branches: Yes and No.");
                  return false;
              }
          }
          $drag.html(workflowTemplates.demoFlowyBlock({
              code: type,
              blockId: $drag.find(".blockid").val(),
              body: demoBlockMarkupByType[type]()
          }));
          setTimeout(relabel, 400);
          return true;
      }
      flowy(canvas, () => { }, () => { }, onSnap, () => { setTimeout(relabel, 400); return false }, 20, 70);
      /* typing in a block must not start a drag */
      $canvas.on("mousedown touchstart", "input, select, textarea", e => e.stopPropagation());

      let panModeEnabled = false;
      let canvasZoom = 1;
      let activeCanvasPan = null;
      const $canvasNavigation = $("#wrap");
      const minimumCanvasZoom = 0.5;
      const maximumCanvasZoom = 1.5;
      const canvasZoomStep = 0.1;
      function endCanvasPan(event) {
          if (!activeCanvasPan) return;
          const pointerEvent = event.originalEvent || event;
          if (event.type !== "pointercancel" &&
              pointerEvent.pointerId !== activeCanvasPan.pointerId) return;
          activeCanvasPan = null;
          $canvasNavigation.removeClass("pan-active");
      }
      $("#bPanCanvas").on("click", function () {
          panModeEnabled = !panModeEnabled;
          $(this).attr("aria-pressed", String(panModeEnabled));
          $(this).text(panModeEnabled ? "Pan mode on" : "Pan canvas");
          $canvas.toggleClass("pan-mode", panModeEnabled);
      });
      function setCanvasZoom(nextZoom) {
            const newZoom = Math.max(
                minimumCanvasZoom,
                Math.min(maximumCanvasZoom, Math.round(nextZoom * 10) / 10),
            );
            const viewportWidth = $canvasNavigation.innerWidth();
            const viewportHeight = $canvasNavigation.innerHeight();
            const centerX = $canvas.scrollLeft() + viewportWidth / (2 * canvasZoom);
            const centerY = $canvas.scrollTop() + viewportHeight / (2 * canvasZoom);
            canvasZoom = newZoom;
            $canvas.css({
                width: viewportWidth / canvasZoom + "px",
                height: viewportHeight / canvasZoom + "px",
                transform: "scale(" + canvasZoom + ")",
            });
            flowy.setZoom(canvasZoom);
            $canvas.scrollLeft(Math.max(0, centerX - viewportWidth / (2 * canvasZoom)));
            $canvas.scrollTop(Math.max(0, centerY - viewportHeight / (2 * canvasZoom)));
            $("#canvasZoomLevel").text(Math.round(canvasZoom * 100) + "%");
            $("#bZoomOut").prop("disabled", canvasZoom <= minimumCanvasZoom);
            $("#bZoomIn").prop("disabled", canvasZoom >= maximumCanvasZoom);
      }
      $("#bZoomOut").on("click", () => setCanvasZoom(canvasZoom - canvasZoomStep));
      $("#bZoomIn").on("click", () => setCanvasZoom(canvasZoom + canvasZoomStep));
      $("#bResetCanvasView").on("click", () => {
            endCanvasPan({ type: "pointercancel" });
            setCanvasZoom(1);
            $canvas.scrollLeft(0).scrollTop(0);
      });
      $canvas.on("pointerdown", (event) => {
          const pointerEvent = event.originalEvent || event;
          if (!panModeEnabled || pointerEvent.button !== 0) return;
          if ($(pointerEvent.target).closest(".block, .create-flowy, .indicator").length)
              return;

          activeCanvasPan = {
              pointerId: pointerEvent.pointerId,
              startX: pointerEvent.clientX,
              startY: pointerEvent.clientY,
              startScrollLeft: $canvas.scrollLeft(),
              startScrollTop: $canvas.scrollTop(),
          };
          if ($canvas[0].setPointerCapture && pointerEvent.pointerId != null) {
              $canvas[0].setPointerCapture(pointerEvent.pointerId);
          }
          $canvasNavigation.addClass("pan-active");
          event.preventDefault();
      });
      $(document).on("pointermove.workflowCanvasPan", (event) => {
          const pointerEvent = event.originalEvent || event;
          if (!activeCanvasPan ||
              pointerEvent.pointerId !== activeCanvasPan.pointerId) return;
          $canvas.scrollLeft(
              activeCanvasPan.startScrollLeft +
                  (activeCanvasPan.startX - pointerEvent.clientX) / canvasZoom,
          );
          $canvas.scrollTop(
              activeCanvasPan.startScrollTop +
                  (activeCanvasPan.startY - pointerEvent.clientY) / canvasZoom,
          );
          event.preventDefault();
      });
      $(document).on(
          "pointerup.workflowCanvasPan pointercancel.workflowCanvasPan",
          endCanvasPan,
      );
      $canvas.on("lostpointercapture", endCanvasPan);

      /* Left child of an if/else = Yes, right child = No */
      function relabel() {
          const arr = flowy.output().blockarr;
          arr.forEach((block) => getFlowBlockElement(block.id).find(".branch").text("").attr("class", "branch"));
          arr.filter((block) => getFlowBlockType(block.id) === "2").forEach((decisionBlock) => getOrderedChildFlowBlocks(arr, decisionBlock.id).forEach((childBlock, index) => {
              const branch = getFlowBlockElement(childBlock.id).find(".branch"); if (!branch.length) return;
              branch.text(index === 0 ? "Yes" : index === 1 ? "No" : "Extra").attr("class", "branch " + (index === 0 ? "y" : index === 1 ? "n" : ""));
          }));
      }

      /* ---------- Campaign simulation ---------- */
      function createSeededRandom(seed) {
          return function () {
              seed |= 0;
              seed = seed + 0x6D2B79F5 | 0;
              let mixed = Math.imul(seed ^ seed >>> 15, 1 | seed);
              mixed = mixed + Math.imul(mixed ^ mixed >>> 7, 61 | mixed) ^ mixed;
              return ((mixed ^ mixed >>> 14) >>> 0) / 4294967296;
          };
      }
      function profileMatchesCondition(profile, fieldName, operator, expectedValue) {
          const actualValue = profile[fieldName];
          if (operator === "ge") return actualValue >= expectedValue;
          if (operator === "lt") return actualValue < expectedValue;
          return actualValue === expectedValue;
      }
      function fillMessageTemplate(messageTemplate, profile) {
          return String(messageTemplate == null ? "" : messageTemplate)
              .replace(/\{\{first_name\}\}/g, profile.name)
              .replace(/\{\{cart_value\}\}/g, profile.cart);
      }
      function simulateMessageOutcome(profile, flowNodeId, blockType) {
          const random = createSeededRandom(profile.id * 131 + flowNodeId * 7919 + Number(blockType));
          const isEmail = blockType === "3";
          const delivered = isEmail ? random() < .97 : profile.push === 1;
          const opened = delivered && random() < (isEmail ? .14 + profile.score / 200 : .2 + profile.score / 150);
          const clicked = opened && random() < (isEmail ? .3 + Math.min(profile.cart, 300) / 700 : .4);
          const converted = clicked && random() < .5;
          return { delivered, opened, clicked, converted };
      }

      let simulationResult = null;
      function runCampaignSimulation() {
          const flowBlocks = flowy.output().blockarr;
          const rootBlock = flowBlocks.find((block) => block.parent === -1);
          if (!rootBlock || getFlowBlockType(rootBlock.id) !== "1") {
              toast("Add a customer segment and some blocks first");
              return;
          }

          const profileReachByBlock = {};
          const messageMetricsByBlock = {};
          const profileOutcomes = [];
          profileRecords.forEach((profile) => {
              let currentBlockId = rootBlock.id;
              let outcome = "No action";
              while (true) {
                  profileReachByBlock[currentBlockId] = (profileReachByBlock[currentBlockId] || 0) + 1;
                  const blockType = getFlowBlockType(currentBlockId);
                  const childBlocks = getOrderedChildFlowBlocks(flowBlocks, currentBlockId);
                  if (blockType === "1") {
                      if (!childBlocks.length) break;
                      currentBlockId = childBlocks[0].id;
                      continue;
                  }
                  if (blockType === "2") {
                      const isMatch = profileMatchesCondition(
                          profile,
                          getFlowBlockFieldValue(currentBlockId, "f-field"),
                          getFlowBlockFieldValue(currentBlockId, "f-op"),
                          Number(getFlowBlockFieldValue(currentBlockId, "f-val")),
                      );
                      const branchBlock = childBlocks[isMatch ? 0 : 1];
                      if (!branchBlock) break;
                      currentBlockId = branchBlock.id;
                      continue;
                  }

                  const messageOutcome = simulateMessageOutcome(profile, currentBlockId, blockType);
                  const messageMetrics = messageMetricsByBlock[currentBlockId] ||
                      (messageMetricsByBlock[currentBlockId] = {
                          id: currentBlockId,
                          type: blockType,
                          sent: 0,
                          delivered: 0,
                          opened: 0,
                          clicked: 0,
                          conversions: 0,
                          revenue: 0,
                          sampleProfile: profile,
                      });
                  messageMetrics.sent++;
                  messageMetrics.delivered += messageOutcome.delivered;
                  messageMetrics.opened += messageOutcome.opened;
                  messageMetrics.clicked += messageOutcome.clicked;
                  messageMetrics.conversions += messageOutcome.converted;
                  if (messageOutcome.converted) messageMetrics.revenue += profile.cart;
                  const channelName = blockType === "3" ? "Email" : "Push";
                  const engagementResult = messageOutcome.converted ? "Converted"
                      : messageOutcome.clicked ? "Clicked"
                      : messageOutcome.opened ? "Opened"
                      : messageOutcome.delivered ? "Delivered"
                      : "Not reachable";
                  outcome = channelName + " · " + engagementResult;
                  break;
              }
              profileOutcomes.push({ profile, outcome });
          });

          clearBlockReachLabels();
          Object.keys(profileReachByBlock).forEach((flowNodeId) => {
              updateBlockReachLabel(flowNodeId, profileReachByBlock[flowNodeId]);
          });
          simulationResult = {
              profiles: profileOutcomes,
              messages: Object.values(messageMetricsByBlock),
          };
          renderSampleProfileTable();
          renderSimulationReport();
          setActiveDemoTab("Rep");
      }

      /* ---------- Demo views ---------- */
      const formatPercent = (numerator, denominator) =>
          denominator ? Math.round(numerator / denominator * 100) + "%" : "–";
      function renderSampleProfileTable() {
          const rows = profileRecords.map((profile, index) => ({
              ...profile,
              pushLabel: profile.push ? "yes" : "no",
              hasOutcome: !!simulationResult,
              outcome: simulationResult ? simulationResult.profiles[index].outcome : "",
          }));
          $("#segTable").html(workflowTemplates.demoSegmentTable({
              profileCount: profileRecords.length,
              hasOutcome: !!simulationResult,
              profiles: rows,
          }));
      }
      function renderSimulationReport() {
          const messages = simulationResult.messages;
          if (!messages.length) {
              $("#paneRep").html(workflowTemplates.demoReportEmpty());
              return;
          }
          const sumMessageMetric = (metricName) =>
              messages.reduce((total, message) => total + message[metricName], 0);
          const activated = sumMessageMetric("sent");
          const delivered = sumMessageMetric("delivered");
          const opened = sumMessageMetric("opened");
          const clicked = sumMessageMetric("clicked");
          const conversions = sumMessageMetric("conversions");
          const revenue = sumMessageMetric("revenue");
          const noActionCount = simulationResult.profiles
              .filter((result) => result.outcome === "No action").length;
          const kpis = [
              { value: activated, label: "Activated" },
              { value: formatPercent(opened, delivered), label: "Open rate" },
              { value: formatPercent(clicked, delivered), label: "Click rate" },
              { value: conversions, label: "Conversions" },
              { value: formatPercent(conversions, activated), label: "Conversion rate" },
              { value: "$" + revenue.toLocaleString(), label: "Revenue" },
          ];
          const funnel = [
              { label: "Activated", value: activated },
              { label: "Delivered", value: delivered },
              { label: "Opened", value: opened },
              { label: "Clicked", value: clicked },
              { label: "Converted", value: conversions },
          ].map((stage) => ({
              ...stage,
              width: activated ? stage.value / activated * 100 : 0,
          }));
          const messageCards = messages.map((message) => ({
              ...message,
              channel: message.type === "3" ? "Email" : "Web push",
              subject: fillMessageTemplate(getFlowBlockFieldValue(message.id, "f-a"), message.sampleProfile),
              body: fillMessageTemplate(getFlowBlockFieldValue(message.id, "f-b"), message.sampleProfile),
              openRate: formatPercent(message.opened, message.delivered),
              clickRate: formatPercent(message.clicked, message.delivered),
          }));
          $("#paneRep").html(workflowTemplates.demoReport({
              profileCount: profileRecords.length,
              noActionCount,
              kpis,
              funnel,
              messages: messageCards,
          }));
      }
      function setActiveDemoTab(tabName) {
          ["Seg", "Rep"].forEach((tabSuffix) => {
              $("#tab" + tabSuffix).attr("aria-selected", tabSuffix === tabName);
              $("#pane" + tabSuffix).prop("hidden", tabSuffix !== tabName);
          });
      }
      $("#tabSeg").on("click", () => setActiveDemoTab("Seg"));
      $("#tabRep").on("click", () => setActiveDemoTab("Rep"));

      /* ---------- Buttons ---------- */
      function clearWorkflow() {
          flowy.deleteBlocks();
          simulationResult = null;
          renderSampleProfileTable();
          $("#paneRep").html(workflowTemplates.demoReportIntro());
      }
      $("#bClear").on("click", clearWorkflow);
      $("#bRun").on("click", runCampaignSimulation);

      /* Load sample: replays real drag-and-drop on the palette so Flowy lays out and connects the blocks */
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const fire = (t, x, y, target) => target.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window }));
      async function addSampleBlock(type, parent, fieldValues) {
          const $item = $('.create-flowy[data-type="' + type + '"]');
          if (!$item.length) throw new Error("Missing palette block for type " + type);
          const item = $item[0];
          const itemOffset = $item.offset();
          const canvasRect = canvas.getBoundingClientRect();
          let x = canvasRect.left + 60;
          let y = canvasRect.top + 30;
          if (parent != null) {
              const $parent = $canvas.find('.blockid[value="' + parent + '"]').parent();
              if (!$parent.length) throw new Error("Could not find parent block " + parent);
              const parentRect = $parent[0].getBoundingClientRect();
              x = parentRect.left + parentRect.width / 2 - $item.outerWidth() / 2;
              y = parentRect.bottom + 8;
          }
          fire("mousedown", itemOffset.left - window.scrollX + 10, itemOffset.top - window.scrollY + 10, item); await sleep(30);
          fire("mousemove", x + 10, y + 10, document); await sleep(30);
          fire("mouseup", x + 10, y + 10, document); await sleep(450);
          const ids = $canvas.find(".blockid").map(function () { return Number(this.value); }).get();
          if (!ids.length) throw new Error("Could not place block type " + type);
          const id = Math.max(...ids);
          const $block = $canvas.find('.blockid[value="' + id + '"]').parent();
          Object.keys(fieldValues || {}).forEach((selector) => {
              const $input = $block.find(selector);
              if (!$input.length) throw new Error("Could not set sample value for " + selector);
              $input.val(fieldValues[selector]);
          });
          return id;
      }
      async function loadSampleWorkflow() {
          const $button = $("#bSample");
          $button.prop("disabled", true);
          clearWorkflow();
          try {
              const pendingBlocks = [[sampleWorkflowTree, null]];
              while (pendingBlocks.length) {
                  const [workflowNode, parentBlockId] = pendingBlocks.shift();
                  const createdBlockId = await addSampleBlock(
                      workflowNode.type,
                      parentBlockId,
                      workflowNode.vals,
                  );
                  (workflowNode.children || []).forEach((childNode) => {
                      pendingBlocks.push([childNode, createdBlockId]);
                  });
              }
              relabel();
              toast("Sample loaded. Select Run campaign to see the report.");
          } catch (error) {
              clearWorkflow();
              toast("Could not load sample: " + (error.message || String(error)));
          } finally {
              $button.prop("disabled", false);
          }
      }
      const sampleProfilesJson = JSON.stringify(profileRecords, null, 1);
      function applyProfiles(list) {
          profileRecords = validateProfiles(list);
          simulationResult = null;
          $(".seglen").text(profileRecords.length + " profiles");
          clearBlockReachLabels();
          renderSampleProfileTable();
          $("#paneRep").html(workflowTemplates.demoReportIntro());
      }
      $("#bApply").on("click", () => {
          try {
              const l = validateProfiles(JSON.parse($("#jsonData").val()));
              applyProfiles(l);
              toast(l.length + " profiles applied");
          } catch (err) { toast("Invalid JSON: " + err.message) }
      });
      $("#bReset").on("click", () => {
          $("#jsonData").val(sampleProfilesJson);
          applyProfiles(JSON.parse(sampleProfilesJson));
      });
      $("#jsonData").val(sampleProfilesJson);
      $("#bSample").prop("disabled", false).on("click", loadSampleWorkflow);
      renderSampleProfileTable();
      clearWorkflow();
      setActiveDemoTab("Seg");
  }
})();
