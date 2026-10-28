/*!
 * workflow-core.js : UI-free engine for customer-engagement flows.
 *
 * One registry (NODES) is the single source of truth. The palette, the block forms,
 * validation, the simulator and the AI agent manifest are all generated from it,
 * so adding a node type means adding one entry here.
 *
 * Workflow JSON (schema_version 2), a tree:
 *   { schema_version: 2, root: { id, type, config:{...}, children:[ { id, type, branch?, config, children } ] } }
 *   - `branch` is only set on children of a branching node (If / else: yes|no, Wait for event: happened|timeout).
 *
 * Agent loop:  manifest() -> applyOps()/recipes -> validate() -> simulate() -> fix -> repeat.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.WorkflowCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const EVENTS = [
    "signup",
    "cart_abandoned",
    "purchase",
    "inactive_30d",
    "birthday",
  ];
  const EVENT_LABELS = {
    signup: "Signs up",
    cart_abandoned: "Abandons cart",
    purchase: "Makes a purchase",
    inactive_30d: "Inactive for 30 days",
    birthday: "Has a birthday",
  };
  const WAIT_EVENTS = ["purchase", "email_opened", "email_clicked"];
  const WAIT_EVENT_LABELS = {
    purchase: "Makes a purchase",
    email_opened: "Opens an email",
    email_clicked: "Clicks an email",
  };
  const UNITS = ["minutes", "hours", "days"];
  const FIELDS = ["score", "cart", "recency", "push", "tags"];
  const FIELD_LABELS = {
    score: "Engagement score (0-100)",
    cart: "Cart value ($)",
    recency: "Days since purchase",
    push: "Push opt-in (1 = yes)",
    tags: "Profile tags",
  };
  const OPS = ["ge", "lt", "eq", "has", "hasnt"];
  const OP_LABELS = {
    ge: "≥",
    lt: "<",
    eq: "=",
    has: "has tag",
    hasnt: "does not have tag",
  };
  const BRANCH_LABELS = {
    yes: "Yes",
    no: "No",
    happened: "Happened",
    timeout: "Timed out",
  };

  /* ===================================================================
   * 1. Node registry
   * config field: { type: enum|number|text|longtext, label, options?, labels?, default, required?, min?, help? }
   * =================================================================== */
  const NODES = {
    trigger: {
      category: "Input",
      label: "Event trigger",
      entry: true,
      description: "Starts the flow for a customer segment",
      use_when:
        "Always the root. Pick the audience segment, then the behaviour the journey reacts to.",
      config: {
        segment_id: {
          type: "segment",
          label: "Customer segment",
          default: null,
          required: true,
          help: "Profiles in this segment enter the flow",
        },
        event: {
          type: "enum",
          label: "When a customer",
          options: EVENTS,
          labels: EVENT_LABELS,
          default: "cart_abandoned",
        },
      },
    },
    condition: {
      category: "Logic",
      label: "If / else",
      branches: ["yes", "no"],
      description: "Split customers by a profile field",
      use_when:
        "Personalise by value, engagement or consent. First child = yes, second = no.",
      config: {
        field: {
          type: "enum",
          label: "Field",
          options: FIELDS,
          labels: FIELD_LABELS,
          default: "score",
        },
        op: {
          type: "enum",
          label: "Operator",
          options: OPS,
          labels: OP_LABELS,
          default: "ge",
          help: "tags only accepts has / hasnt; other fields accept ge / lt / eq",
        },
        value: {
          type: "text",
          label: "Value",
          default: "60",
          required: true,
          help: "number, or a tag name when field = tags",
        },
      },
    },
    wait: {
      category: "Logic",
      label: "Wait / Delay",
      description: "Pause before the next step",
      use_when:
        "Space out messages, or give a customer time to act before you nudge.",
      config: {
        amount: {
          type: "number",
          label: "Wait for",
          default: 1,
          min: 1,
          required: true,
        },
        unit: { type: "enum", label: "Unit", options: UNITS, default: "days" },
      },
    },
    wait_event: {
      category: "Logic",
      label: "Wait for event",
      branches: ["happened", "timeout"],
      description: "Wait until the customer acts, or time runs out",
      use_when:
        "Follow-ups that must stop once the customer converts. First child = happened, second = timeout.",
      config: {
        event: {
          type: "enum",
          label: "Customer",
          options: WAIT_EVENTS,
          labels: WAIT_EVENT_LABELS,
          default: "purchase",
        },
        amount: {
          type: "number",
          label: "Give up after",
          default: 2,
          min: 1,
          required: true,
        },
        unit: { type: "enum", label: "Unit", options: UNITS, default: "days" },
      },
    },
    exit: {
      category: "Logic",
      label: "Goal / Exit",
      terminal: true,
      description: "End the journey here",
      use_when:
        "End every branch. Use kind=goal where the customer achieved what you wanted, kind=exit otherwise.",
      config: {
        kind: {
          type: "enum",
          label: "Outcome",
          options: ["goal", "exit"],
          labels: { goal: "Goal reached", exit: "Exit (no goal)" },
          default: "goal",
        },
      },
    },
    email: {
      category: "Activate",
      label: "Send email",
      channel: "email",
      description: "Subject and body template",
      use_when: "Rich content, offers, receipts. Needs an email address.",
      config: {
        subject: {
          type: "text",
          label: "Subject",
          default: "{{first_name}}, a note for you",
          required: true,
        },
        body: {
          type: "longtext",
          label: "Body",
          default: "Hi {{first_name}}, your cart is waiting.",
          required: true,
        },
      },
    },
    sms: {
      category: "Activate",
      label: "Send notification",
      channel: "sms",
      description: "Send text notification",
      use_when: "Urgent or time-limited nudges. Keep under 160 characters.",
      config: {
        body: {
          type: "longtext",
          label: "Message",
          default: "{{first_name}}, your cart is saved for 24h.",
          required: true,
        },
      },
    },
    push: {
      category: "Activate",
      label: "Send web push",
      channel: "push",
      description: "Title and body template",
      use_when: "Light reminders for customers who opted in to push.",
      config: {
        title: {
          type: "text",
          label: "Title",
          default: "Still thinking it over?",
          required: true,
        },
        body: {
          type: "longtext",
          label: "Body",
          default: "{{first_name}}, your cart is one tap away.",
          required: true,
        },
      },
    },
    update_profile: {
      category: "Activate",
      label: "Update profile / tag",
      description: "Add or remove a tag",
      use_when:
        "Record what happened so later flows and If / else nodes can use it.",
      config: {
        action: {
          type: "enum",
          label: "Action",
          options: ["add_tag", "remove_tag"],
          labels: { add_tag: "Add tag", remove_tag: "Remove tag" },
          default: "add_tag",
        },
        tag: { type: "text", label: "Tag", default: "engaged", required: true },
      },
    },
  };

  const TEMPLATE_VARS = ["first_name", "cart_value"];
  const FATAL = [
    "NO_ROOT",
    "ROOT_NOT_ENTRY",
    "ENTRY_NOT_ROOT",
    "UNKNOWN_TYPE",
    "TOO_MANY_CHILDREN",
    "TERMINAL_HAS_CHILDREN",
    "DUP_ID",
    "NO_ID",
  ];

  const clone = (o) => JSON.parse(JSON.stringify(o));
  const maxChildren = (def) =>
    def.branches ? def.branches.length : def.terminal ? 0 : 1;
  function defaults(type) {
    const out = {};
    Object.entries(NODES[type].config).forEach(
      ([k, f]) => (out[k] = f.default),
    );
    return out;
  }
  function walk(node, fn, parent) {
    fn(node, parent || null);
    (node.children || []).forEach((c) => walk(c, fn, node));
  }

  /* ===================================================================
   * 2. Validation: structured, machine-readable, with a fix hint
   * =================================================================== */
  function validate(wf) {
    const issues = [];
    const add = (n, level, code, msg, fix) =>
      issues.push({
        node: (n && n.id) || null,
        level,
        code,
        msg,
        fix: fix || "",
      });
    if (!wf || !wf.root) {
      add(
        null,
        "error",
        "NO_ROOT",
        "The workflow is empty.",
        "Set root to an Event trigger node.",
      );
      return issues;
    }
    const seen = {};
    let sends = 0,
      goals = 0;
    walk(wf.root, (n, parent) => {
      const def = NODES[n.type];
      const kids = n.children || [];
      if (!n.id)
        add(
          n,
          "error",
          "NO_ID",
          "A node has no id.",
          "Give every node a unique id such as n1, n2.",
        );
      else if (seen[n.id])
        add(
          n,
          "error",
          "DUP_ID",
          "Duplicate id " + n.id + ".",
          "Make ids unique.",
        );
      seen[n.id] = 1;
      if (!def)
        return add(
          n,
          "error",
          "UNKNOWN_TYPE",
          'Unknown node type "' + n.type + '".',
          "Use one of: " + Object.keys(NODES).join(", "),
        );

      if (def.entry && parent)
        add(
          n,
          "error",
          "ENTRY_NOT_ROOT",
          "An Event trigger can only be the first node.",
          "Remove it or move it to root.",
        );
      if (!def.entry && !parent)
        add(
          n,
          "error",
          "ROOT_NOT_ENTRY",
          "The flow must start with an Event trigger.",
          "Make the root an Event trigger.",
        );

      const cfg = n.config || {};
      Object.entries(def.config).forEach(([k, f]) => {
        const v = cfg[k];
        const empty = v === undefined || v === null || String(v).trim() === "";
        if (empty && (f.required || f.type === "enum"))
          return add(
            n,
            "error",
            "MISSING_CONFIG",
            def.label + ': "' + f.label + '" is required.',
            "Set config." + k + ".",
          );
        if (f.type === "enum" && f.options.indexOf(String(v)) < 0)
          add(
            n,
            "error",
            "BAD_CONFIG",
            def.label +
              ": " +
              k +
              " must be one of " +
              f.options.join(", ") +
              ".",
            "Set config." + k + " to a valid option.",
          );
        if (f.type === "number" && (isNaN(+v) || +v < (f.min || 0)))
          add(
            n,
            "error",
            "BAD_CONFIG",
            def.label + ": " + k + " must be a number ≥ " + (f.min || 0) + ".",
            "Set config." + k + " to a positive number.",
          );
      });
      if (n.type === "condition" && cfg.field) {
        const isTag = cfg.field === "tags";
        if (isTag !== (cfg.op === "has" || cfg.op === "hasnt"))
          add(
            n,
            "error",
            "BAD_OPERATOR",
            isTag
              ? "Tags only support has / hasnt."
              : "Numeric fields support ge, lt, eq.",
            "Change config.op.",
          );
        else if (!isTag && isNaN(+cfg.value))
          add(
            n,
            "error",
            "BAD_CONFIG",
            "If / else needs a number for this field.",
            "Set config.value to a number.",
          );
      }

      if (def.terminal && kids.length)
        add(
          n,
          "error",
          "TERMINAL_HAS_CHILDREN",
          "Goal / Exit ends the branch.",
          "Remove its children.",
        );
      else if (kids.length > maxChildren(def))
        add(
          n,
          "error",
          "TOO_MANY_CHILDREN",
          def.label + " allows at most " + maxChildren(def) + " next step(s).",
          def.branches
            ? "Use one child per branch."
            : "Chain nodes one after another.",
        );
      else if (def.branches) {
        const used = {};
        kids.forEach((k) => {
          if (def.branches.indexOf(k.branch) < 0 || used[k.branch])
            add(
              k,
              "error",
              "BAD_BRANCH",
              "Child of " +
                def.label +
                " needs a unique branch: " +
                def.branches.join(" or ") +
                ".",
              "Set branch.",
            );
          used[k.branch] = 1;
        });
        if (!kids.length)
          add(
            n,
            "error",
            "NO_BRANCH",
            def.label + " has no branches.",
            "Add children for: " + def.branches.join(", ") + ".",
          );
        else if (kids.length === 1 && used[kids[0].branch])
          add(
            n,
            "warn",
            "MISSING_BRANCH",
            'The "' +
              def.branches.filter((b) => !used[b])[0] +
              '" branch is empty, so those customers stop here.',
            "Add a child for the missing branch.",
          );
      }
      if (def.channel) {
        sends++;
        if (kids.some((k) => NODES[k.type] && NODES[k.type].channel))
          add(
            n,
            "warn",
            "BACK_TO_BACK_SENDS",
            "Two messages are sent with no wait between them.",
            "Insert a Wait / Delay between the messages.",
          );
      }
      if (n.type === "exit" && cfg.kind === "goal") goals++;
      if (
        (!kids.length && !def.terminal && def.channel !== undefined) ||
        (!kids.length && n.type === "update_profile")
      )
        add(
          n,
          "info",
          "OPEN_END",
          "This branch ends without a Goal / Exit.",
          "Add a Goal / Exit so the outcome is recorded.",
        );
    });
    if (!sends)
      add(
        null,
        "error",
        "NO_ACTION",
        "No message is sent.",
        "Add Send email, Send notification or Send web push.",
      );
    if (!goals)
      add(
        null,
        "warn",
        "NO_GOAL",
        "No Goal / Exit with kind=goal, so conversions cannot be attributed to the flow.",
        "End the successful branch with Goal / Exit (kind=goal).",
      );
    return issues;
  }

  /* ===================================================================
   * 3. Patch operations: small, safe edits an agent can make and re-validate
   *   {op:'add', parent:id|null, branch?:'yes', node:{type, config?, id?}}
   *   {op:'update', id, config:{...}}      {op:'remove', id}
   * =================================================================== */
  function applyOps(wf, ops) {
    const w = clone(wf || { schema_version: 2, root: null });
    const errors = [];
    const find = (id) => {
      let f = null;
      if (w.root)
        walk(w.root, (n) => {
          if (n.id === id) f = n;
        });
      return f;
    };
    let seq = 0;
    if (w.root)
      walk(w.root, (n) => {
        seq = Math.max(seq, parseInt(String(n.id).replace(/\D/g, ""), 10) || 0);
      });
    (ops || []).forEach((op, i) => {
      try {
        if (op.op === "add") {
          const type = op.node && op.node.type;
          if (!NODES[type]) throw new Error('unknown node type "' + type + '"');
          const node = {
            id: op.node.id || "n" + ++seq,
            type,
            config: Object.assign(defaults(type), op.node.config || {}),
            children: [],
          };
          if (op.branch) node.branch = op.branch;
          if (op.parent == null) {
            if (w.root) throw new Error("root already exists");
            w.root = node;
          } else {
            const p = find(op.parent);
            if (!p) throw new Error("parent not found: " + op.parent);
            p.children.push(node);
          }
        } else if (op.op === "update") {
          const n = find(op.id);
          if (!n) throw new Error("node not found: " + op.id);
          Object.assign(n.config, op.config || {});
        } else if (op.op === "remove") {
          if (w.root && w.root.id === op.id) w.root = null;
          else if (w.root)
            walk(w.root, (n) => {
              n.children = (n.children || []).filter((c) => c.id !== op.id);
            });
        } else throw new Error('unknown op "' + op.op + '"');
      } catch (e) {
        errors.push({ op: i, msg: e.message });
      }
    });
    return { workflow: w, errors, issues: validate(w) };
  }

  /* ===================================================================
   * 4. Simulator: virtual-time journey per profile (deterministic)
   * =================================================================== */
  const HOURS = { minutes: 1 / 60, hours: 1, days: 24 };
  function rng(seed) {
    return function () {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const nodeSeed = (id) => parseInt(String(id).replace(/\D/g, ""), 10) || 1;
  const fill = (s, p) =>
    String(s == null ? "" : s)
      .replace(/\{\{first_name\}\}/g, p.name)
      .replace(/\{\{cart_value\}\}/g, p.cart);

  function testCondition(c, p, tags) {
    if (c.field === "tags") {
      const has = tags.has(String(c.value));
      return c.op === "hasnt" ? !has : has;
    }
    const a = Number(p[c.field]),
      b = Number(c.value);
    return c.op === "ge" ? a >= b : c.op === "lt" ? a < b : a === b;
  }
  function messageOutcome(channel, p, r) {
    if (channel === "email") {
      const delivered = p.email !== 0 && r() < 0.97;
      const opened = delivered && r() < 0.14 + p.score / 200;
      const clicked = opened && r() < 0.3 + Math.min(p.cart, 300) / 700;
      return { delivered, opened, clicked, converted: clicked && r() < 0.5 };
    }
    if (channel === "sms") {
      const delivered = p.sms !== 0 && r() < 0.98;
      const clicked = delivered && r() < 0.08 + p.score / 500;
      return {
        delivered,
        opened: delivered,
        clicked,
        converted: clicked && r() < 0.4,
      };
    }
    const delivered = p.push === 1;
    const opened = delivered && r() < 0.2 + p.score / 150;
    const clicked = opened && r() < 0.4;
    return { delivered, opened, clicked, converted: clicked && r() < 0.5 };
  }
  function eventHappens(event, p, st, r) {
    if (event === "purchase" && st.converted) return true;
    if (event === "email_clicked" && st.clicked) return true;
    if (event === "email_opened" && st.opened) return true;
    return (
      r() < (event === "purchase" ? 0.04 + p.score / 800 : 0.1 + p.score / 300)
    );
  }

  function simulate(wf, profiles) {
    const nodes = {};
    const stat = (id) =>
      (nodes[id] = nodes[id] || {
        reach: 0,
        delivered: 0,
        opened: 0,
        clicked: 0,
        conversions: 0,
        revenue: 0,
        out: {},
      });
    const totals = {
      entered: 0,
      received: 0,
      opened: 0,
      clicked: 0,
      converted: 0,
      revenue: 0,
      goal: 0,
      exited: 0,
      ended: 0,
    };
    const results = [];
    (profiles || []).forEach((p) => {
      const st = {
        t: 0,
        tags: new Set(p.tags || []),
        converted: false,
        clicked: false,
        opened: false,
        received: false,
        last: "",
        end: "ended",
      };
      let n = wf.root,
        guard = 0;
      totals.entered++;
      while (n && guard++ < 60) {
        const def = NODES[n.type],
          s = stat(n.id),
          kids = n.children || [];
        const r = rng(p.id * 131 + nodeSeed(n.id) * 7919);
        s.reach++;
        let next = kids[0];
        if (n.type === "condition") {
          const b = testCondition(n.config, p, st.tags) ? "yes" : "no";
          s.out[b] = (s.out[b] || 0) + 1;
          next = kids.find((k) => k.branch === b);
        } else if (n.type === "wait") {
          st.t += n.config.amount * HOURS[n.config.unit];
        } else if (n.type === "wait_event") {
          const limit = n.config.amount * HOURS[n.config.unit];
          const hit = eventHappens(n.config.event, p, st, r);
          const b = hit ? "happened" : "timeout";
          st.t += hit ? limit * r() : limit;
          s.out[b] = (s.out[b] || 0) + 1;
          next = kids.find((k) => k.branch === b);
        } else if (def.channel) {
          const o = messageOutcome(def.channel, p, r);
          if (o.delivered) {
            s.delivered++;
            st.received = true;
          }
          if (o.opened) {
            s.opened++;
            st.opened = true;
          }
          if (o.clicked) {
            s.clicked++;
            st.clicked = true;
          }
          if (o.converted && !st.converted) {
            s.conversions++;
            s.revenue += p.cart;
            st.converted = true;
          }
          st.last =
            { email: "Email", sms: "SMS", push: "Push" }[def.channel] +
            " · " +
            (o.converted
              ? "Converted"
              : o.clicked
                ? "Clicked"
                : o.opened && def.channel !== "sms"
                  ? "Opened"
                  : o.delivered
                    ? "Delivered"
                    : "Not reachable");
        } else if (n.type === "update_profile") {
          st.tags[n.config.action === "add_tag" ? "add" : "delete"](
            String(n.config.tag),
          );
        } else if (n.type === "exit") {
          st.end = n.config.kind;
          next = null;
        }
        n = next;
      }
      if (st.received) totals.received++;
      if (st.opened) totals.opened++;
      if (st.clicked) totals.clicked++;
      if (st.converted) {
        totals.converted++;
        totals.revenue += p.cart;
      }
      totals[
        st.end === "goal" ? "goal" : st.end === "exit" ? "exited" : "ended"
      ]++;
      results.push({
        id: p.id,
        name: p.name,
        days: Math.round((st.t / 24) * 10) / 10,
        outcome: st.converted
          ? "Converted"
          : st.end === "goal"
            ? "Goal reached"
            : st.last || "No action",
        end: st.end,
      });
    });
    return { totals, nodes, profiles: results };
  }

  /* ===================================================================
   * 5. Sample profiles
   * =================================================================== */
  const NAMES = [
    "Ines",
    "Dev",
    "Ben",
    "Aiko",
    "Maya",
    "Luca",
    "Noor",
    "Tariq",
    "Sofia",
    "Kenji",
    "Amara",
    "Jonas",
    "Lan",
    "Omar",
    "Priya",
    "Mateo",
    "Hana",
    "Felix",
    "Zara",
    "Ravi",
    "Elif",
    "Chen",
    "Nia",
    "Hugo",
    "Yuki",
    "Sami",
    "Ada",
    "Leo",
    "Mina",
    "Idris",
    "Tess",
    "Bao",
  ];
  const SAMPLE_SEGMENTS = [
    {
      segment_id: "seg-high-intent",
      segment_name: "High-intent shoppers",
      description: "Sample segment 1",
      seed: 42,
    },
    {
      segment_id: "seg-lapsed",
      segment_name: "Lapsed customers",
      description: "Sample segment 2",
      seed: 7,
    },
    {
      segment_id: "seg-new",
      segment_name: "New sign-ups",
      description: "Sample segment 3",
      seed: 99,
    },
  ];
  function sampleProfiles(count, seed) {
    const r = rng(seed == null ? 42 : seed),
      out = [];
    for (let i = 0; i < (count || 32); i++) {
      out.push({
        id: i + 1,
        name: NAMES[i % NAMES.length],
        score: Math.round(10 + r() * 85),
        cart: Math.round((40 + r() * 340) / 5) * 5,
        recency: Math.round(1 + r() * 59),
        push: r() < 0.7 ? 1 : 0,
        sms: r() < 0.85 ? 1 : 0,
        email: 1,
        tags: [],
      });
    }
    return out;
  }
  /* ===================================================================
   * 6. Agent interface
   * =================================================================== */
  function catalog() {
    return Object.entries(NODES).map(([type, d]) => ({
      type,
      category: d.category,
      label: d.label,
      description: d.description,
      use_when: d.use_when,
      entry: !!d.entry,
      terminal: !!d.terminal,
      branches: d.branches || null,
      max_children: maxChildren(d),
      config: Object.fromEntries(
        Object.entries(d.config).map(([k, f]) => [
          k,
          {
            type: f.type,
            required: !!f.required,
            default: f.default,
            options: f.options || undefined,
            min: f.min,
            help: f.help,
          },
        ]),
      ),
    }));
  }
  const INSTRUCTIONS = [
    "You build customer-engagement workflows for marketers as JSON trees (schema_version 2).",
    "1. Start from the marketer's goal and pick the matching recipe as a template, then adapt it.",
    "2. The root is always an Event trigger. Its config.segment_id names the audience segment. Every branch ends with Goal / Exit.",
    "3. Only If / else (yes, no) and Wait for event (happened, timeout) branch. Give each child its branch name. Everything else has exactly one child.",
    "4. Put a Wait / Delay between messages. Use Wait for event before any follow-up that must stop once the customer converts.",
    "5. Message templates may use {{" + TEMPLATE_VARS.join("}}, {{") + "}}.",
    "6. Edit with applyOps (add / update / remove), then call validate. Fix every error and read each warning. Call simulate to check reach and conversion before showing the marketer.",
    "7. Explain the flow in plain language: who enters, what they receive and when, and how it ends.",
  ].join("\n");
  function manifest() {
    return {
      instructions: INSTRUCTIONS,
      template_variables: TEMPLATE_VARS,
      nodes: catalog(),
      tools: [
        {
          name: "get_workflow",
          description: "Return the current workflow JSON.",
        },
        {
          name: "set_workflow",
          description:
            "Replace the canvas with a full workflow JSON. Returns { ok, issues }.",
          input: "workflow",
        },
        {
          name: "apply_ops",
          description:
            "Apply add / update / remove operations, re-render and validate.",
          input: "ops[]",
        },
        {
          name: "validate",
          description:
            "Return structured issues: { node, level, code, msg, fix }.",
        },
        {
          name: "simulate",
          description:
            "Run the flow against sample profiles. Returns per-node reach, branch counts and conversion.",
        },
      ],
    };
  }

  return {
    NODES,
    FATAL,
    BRANCH_LABELS,
    TEMPLATE_VARS,
    defaults,
    maxChildren,
    walk,
    validate,
    applyOps,
    simulate,
    sampleProfiles,
    SAMPLE_SEGMENTS,
    fill,
    catalog,
    manifest,
  };
});
