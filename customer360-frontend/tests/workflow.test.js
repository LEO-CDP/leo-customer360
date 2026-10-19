"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const Core = require("../static/js/workflow/workflow-core.js");

function node(id, type, config, children = [], branch) {
  return { id, type, config, children, ...(branch ? { branch } : {}) };
}

function workflow(child) {
  return {
    schema_version: 2,
    root: node("n1", "trigger", { event: "signup", segment_id: "audience" }, [child]),
  };
}

function campaignHelpers(jquery, api) {
  const file = path.join(__dirname, "../static/js/campaign-view.js");
  const source = fs.readFileSync(file, "utf8").replace(
    "C360.campaignView = { load: loadAll };",
    "C360.campaignView = { workflowProfile, campaignStatusPresentation, channels: CAMPAIGN_CHANNELS, platforms: CAMPAIGN_PLATFORMS, save: saveCampaignEditor, state: campaignWorkflowState, actions: campaignStateActions, transitions: campaignHistoryTransitions, submitState: submitCampaignState, detailState: campaignDetailState, mountCampaignWorkflowStudio, loadDetails: loadCampaignDetails };",
  );
  const context = {
    $: jquery,
    window: { C360: {
      fmt: { realName: (profile) => profile.first_name, shortId: (id) => id },
      config: { api, current: { tenantId: "tenant" } },
      router: { define() {} },
    } },
  };
  vm.runInNewContext(source, context);
  return context.window.C360.campaignView;
}

test("campaign profile adapter preserves condition fields, nulls and consent", () => {
  const profile = campaignHelpers().workflowProfile({
    master_profile_id: "profile-1", first_name: "Sam", engagement_score: "80",
    churn_probability: "0.2", lead_conversion_probability: "0.8",
    historical_clv: "1000", latest_nps_score: null, average_csat: "4.5",
    email: "private@example.com", phone_number: "123",
    push_tokens: { device: "token" },
    communication_preferences: { email_opt_in: true, push_opt_in: true },
    segmentation_tags: ["vip"],
  }, 0);

  assert.equal(profile.engagement_score, 80);
  assert.equal(profile.churn_probability, 0.2);
  assert.equal(profile.historical_clv, 1000);
  assert.equal(profile.lead_conversion_probability, 0.8);
  assert.equal(profile.latest_nps_score, null);
  assert.equal(profile.average_csat, 4.5);
  assert.equal(profile.email, 1);
  assert.equal(profile.sms, 0);
  assert.equal(profile.push, 1);
  assert.deepEqual(Array.from(profile.tags), ["vip"]);
});

test("campaign metadata exposes the runtime notification providers", () => {
  const helpers = campaignHelpers();
  assert.ok(helpers.channels.some((channel) => channel.value === "notification"));
  assert.deepEqual(
    Array.from(helpers.platforms.notification, (provider) => provider.value),
    ["sms", "zalo_oa", "whatsapp", "mobile_native"],
  );
  assert.equal(helpers.campaignStatusPresentation("Running").label, "Running");
  assert.equal(helpers.campaignStatusPresentation("Failed").label, "Failed");
});

test("partial branches are errors, not saveable simulator-only workflows", () => {
  const flow = workflow(node("n2", "condition", {
    field: "engagement_score", op: "ge", value: "60",
  }, [node("n3", "exit", { kind: "goal" }, [], "yes")]));
  assert.ok(Core.validate(flow).some((issue) =>
    issue.level === "error" && issue.code === "MISSING_BRANCH"));
});

test("validation rejects infinite durations, thresholds, unknown placeholders and schema versions", () => {
  for (const action of [
    node("n2", "wait", { amount: Infinity, unit: "hours" }),
    node("n2", "condition", { field: "engagement_score", op: "ge", value: "Infinity" }, [
      node("n3", "exit", { kind: "goal" }, [], "yes"),
      node("n4", "exit", { kind: "exit" }, [], "no"),
    ]),
    node("n2", "email", { subject: "Hello {{unknown}}", body: "Welcome" }),
    node("n2", "email", { subject: 123, body: "Welcome" }),
  ]) {
    assert.ok(Core.validate(workflow(action)).some((issue) =>
      issue.level === "error" && ["BAD_CONFIG", "UNKNOWN_PLACEHOLDER"].includes(issue.code)));
  }
  const flow = workflow(node("n2", "email", { subject: "Hi", body: "Welcome" }));
  flow.schema_version = 3;
  assert.ok(Core.validate(flow).some((issue) => issue.code === "SCHEMA_VERSION"));
});

test("tag-only workflows are valid actions", () => {
  const flow = workflow(node("n2", "update_profile", { action: "add_tag", tag: "vip" }));
  assert.equal(Core.validate(flow).filter((issue) => issue.level === "error").length, 0);
});

test("simulation does not treat missing numeric fields as zero", () => {
  const flow = workflow(node("n2", "condition", {
    field: "latest_nps_score", op: "lt", value: 5,
  }, [
    node("n3", "exit", { kind: "goal" }, [], "yes"),
    node("n4", "exit", { kind: "exit" }, [], "no"),
  ]));
  const result = Core.simulate(flow, [{ id: 1, latest_nps_score: null }]);
  assert.equal(result.nodes.n2.out.no, 1);
  assert.equal(result.profiles[0].end, "exit");
});

test("mobile-native simulation uses push reachability rather than phone", () => {
  const flow = workflow(node("n2", "notification", {
    provider: "mobile_native", body: "Hello",
  }));
  const profiles = Core.sampleProfiles(32).map((profile) => ({
    ...profile, sms: 0, push: 1,
  }));
  assert.ok(Core.simulate(flow, profiles).totals.received > 0);
  assert.equal(Core.simulate(flow, profiles.map((profile) => ({
    ...profile, sms: 1, push: 0,
  }))).totals.received, 0);
});

test("campaign save uses the iframe validation API before persisting its workflow", () => {
  const flow = workflow(node("n2", "notification", { provider: "sms", body: "Hello" }));
  const messages = {};
  const requests = [];
  const values = {
    "#campaign-field-name": "UAT campaign",
    "#campaign-field-segment": "audience",
  };
  let issues = [{ level: "error", msg: "Complete both branches." }];
  const studio = { getWorkflow: () => flow, validate: () => issues };
  function jquery(selector) {
    return {
      val: () => values[selector] || "",
      data: () => studio,
      toggleClass() { return this; },
      text(value) { messages[selector] = value; return this; },
      prop() { return this; },
    };
  }
  const deferred = {
    done() { return this; },
    fail() { return this; },
    always() { return this; },
  };
  const helpers = campaignHelpers(jquery, (...args) => {
    requests.push(args);
    return deferred;
  });
  helpers.state.ready = true;

  helpers.save({ preventDefault() {} });
  assert.equal(requests.length, 0);
  assert.equal(messages["#campaign-editor-error"], "Complete both branches.");

  issues = [];
  helpers.save({ preventDefault() {} });
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], "/campaigns/with-workflow");
  assert.equal(requests[0][1].workflow, flow);
  assert.equal(requests[0][2], "POST");
});

test("state actions distinguish workflow review, campaign approval and activation", () => {
  const helpers = campaignHelpers();
  const campaign = { campaign_id: "campaign", segment_id: "segment", approval_status: "InReview", status: "Draft" };
  const workflow = { workflow_id: "flow", workflow_version: 3, approval_status: "InReview" };
  const keys = (admin, approval = workflow.approval_status) =>
    Array.from(helpers.actions(campaign, { ...workflow, approval_status: approval }, admin), (action) => action.key);
  assert.deepEqual(keys(true), ["approve-workflow", "reject-workflow", "reject"]);
  assert.deepEqual(keys(false), []);
  assert.deepEqual(keys(true, "Approved"), ["approve", "reject"]);
  campaign.approval_status = "Approved";
  campaign.status = "Draft";
  assert.deepEqual(keys(true, "Approved"), ["start"]);
  campaign.status = "Running";
  assert.deepEqual(keys(true, "Approved"), []);
  campaign.status = "Completed";
  const restart = helpers.actions(campaign, { ...workflow, approval_status: "Approved" }, true);
  assert.deepEqual(Array.from(restart, (action) => action.key), ["restart"]);
  assert.equal(restart[0].payload.restart, true);
  assert.match(restart[0].description, /no restart limit/);
  campaign.status = "Failed";
  assert.deepEqual(keys(true, "Approved"), ["start"]);
  campaign.segment_id = null;
  assert.deepEqual(keys(true, "Approved"), []);
});

test("drafts submit for review while rejected campaigns require edits", () => {
  const helpers = campaignHelpers();
  const campaign = { campaign_id: "campaign", approval_status: "Draft", status: "Draft" };
  assert.equal(helpers.actions(campaign, {}, false)[0].key, "submit");
  campaign.approval_status = "Rejected";
  assert.equal(helpers.actions(campaign, {}, true).length, 0);
});

test("review payloads capture reasons only when supported by the endpoint", () => {
  const helpers = campaignHelpers();
  const campaign = { campaign_id: "campaign", approval_status: "InReview", status: "Draft" };
  const workflow = { workflow_id: "flow", workflow_version: 3, approval_status: "Approved" };
  const actions = helpers.actions(campaign, workflow, true);
  assert.equal(actions.find((action) => action.key === "approve").reason, false);
  assert.equal(actions.find((action) => action.key === "reject").reason, true);
  workflow.approval_status = "InReview";
  const review = helpers.actions(campaign, workflow, true).find((action) => action.key === "approve-workflow");
  assert.equal(review.payload.decision, "approve");
  assert.equal(review.reason, true);
  assert.equal(review.path, "/workflows/flow/versions/3/review");
});

test("state history derives only transitions actually recorded in audit or review data", () => {
  const helpers = campaignHelpers();
  assert.deepEqual(Array.from(helpers.transitions({
    type: "audit", before_data: { approval_status: "Draft" },
    after_data: { approval_status: "InReview", workflow_version: 2 },
  })), ["Campaign approval: Draft \u2192 InReview", "Workflow version: Not recorded \u2192 2"]);
  assert.deepEqual(Array.from(helpers.transitions({ type: "review", decision: "reject" })),
    ["Campaign approval: InReview \u2192 Rejected"]);
  assert.deepEqual(Array.from(helpers.transitions({
    type: "audit", before_data: { status: "Draft" }, after_data: { status: "Draft" },
  })), []);
});

test("modal prevents duplicate submissions and preserves reason after an API error", () => {
  const fields = { "#campaign-state-action": "reject", "#campaign-state-reason": "  Revise audience.  " };
  const messages = {};
  const callbacks = {};
  const calls = [];
  function jquery(selector) {
    return {
      val: () => fields[selector],
      attr() { return this; }, find() { return this; },
      prop() { return this; }, toggleClass() { return this; },
      trigger() { return this; },
      text(value) { messages[selector] = value; return this; },
    };
  }
  jquery.extend = Object.assign;
  const deferred = {
    done(fn) { callbacks.done = fn; return this; },
    fail(fn) { callbacks.fail = fn; return this; },
    always(fn) { callbacks.always = fn; return this; },
  };
  const helpers = campaignHelpers(jquery, (...args) => { calls.push(args); return deferred; });
  helpers.detailState.campaign = { campaign_id: "uat" };
  helpers.detailState.actions = [{
    key: "reject", label: "Reject campaign", path: "/campaigns/uat/reject", payload: {}, reason: true,
  }];
  const event = { preventDefault() {} };
  helpers.submitState(event);
  helpers.submitState(event);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/campaigns/uat/reject");
  assert.equal(calls[0][1].reason, "Revise audience.");
  assert.equal(calls[0][2], "POST");
  assert.equal(helpers.detailState.busy, true);

  callbacks.fail({ responseJSON: { detail: "State changed" } });
  callbacks.always();
  assert.equal(helpers.detailState.busy, false);
  assert.match(messages["#campaign-state-error"], /State changed/);
  assert.equal(fields["#campaign-state-reason"], "  Revise audience.  ");
  helpers.submitState(event);
  assert.equal(calls.length, 2);
});

test("full campaign replay requires acknowledgement and sends restart flag", () => {
  const fields = {
    "#campaign-state-action": "restart",
    "#campaign-state-restart-ack": false,
  };
  const messages = {};
  const callbacks = {};
  const calls = [];
  function jquery(selector) {
    return {
      val: () => fields[selector],
      prop(name, value) {
        if (arguments.length === 1) return name === "checked" ? fields[selector] : false;
        if (name === "checked") fields[selector] = value;
        return this;
      },
      attr() { return this; }, find() { return this; },
      toggleClass() { return this; }, trigger() { return this; },
      text(value) { messages[selector] = value; return this; },
    };
  }
  jquery.extend = Object.assign;
  const deferred = {
    done(fn) { callbacks.done = fn; return this; },
    fail(fn) { callbacks.fail = fn; return this; },
    always(fn) { callbacks.always = fn; return this; },
  };
  const helpers = campaignHelpers(jquery, (...args) => { calls.push(args); return deferred; });
  helpers.detailState.campaign = { campaign_id: "uat" };
  helpers.detailState.actions = [{
    key: "restart", label: "Restart campaign",
    path: "/admin/campaigns/uat/activate",
    payload: { restart: true },
  }];
  const event = { preventDefault() {} };
  helpers.submitState(event);
  assert.equal(calls.length, 0);
  assert.match(messages["#campaign-state-error"], /Confirm that you understand/);

  fields["#campaign-state-restart-ack"] = true;
  helpers.submitState(event);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/admin/campaigns/uat/activate");
  assert.equal(calls[0][1].restart, true);
  assert.equal(calls[0][2], "POST");
});

test("refresh updates an existing workflow studio instead of mounting it twice", () => {
  const flow = workflow(node("n2", "email", { subject: "New version", body: "Hello" }));
  const calls = [];
  const studio = {
    setSegment: (segment) => calls.push(["setSegment", segment]),
    setWorkflow: (workflow) => { calls.push(["setWorkflow", workflow]); return { ok: true }; },
  };
  const values = { workflowStudio: studio, workflowStudioReady: true };
  const host = {
    data: function (key, value) {
      if (arguments.length === 2) values[key] = value;
      return values[key];
    },
    workflowStudio() { throw new Error("studio must not mount a second time"); },
  };
  const contextJQuery = function () {};
  contextJQuery.extend = Object.assign;
  const helpers = campaignHelpers(contextJQuery);
  helpers.mountCampaignWorkflowStudio(host, {
    workflow: flow,
    segments: [{ segment_id: "audience", segment_name: "Audience" }],
  });

  assert.deepEqual(calls.map((call) => call[0]), ["setSegment", "setWorkflow"]);
  assert.equal(calls[1][1], flow);
});

test("refresh queues the latest workflow until its iframe studio is ready", () => {
  const values = { workflowStudioReady: false };
  const applied = [];
  let mountOptions;
  let mountCount = 0;
  const studio = {
    setSegment() {},
    setWorkflow: (workflow) => { applied.push(workflow); return { ok: true }; },
  };
  const host = {
    data(key, value) {
      if (arguments.length === 2) values[key] = value;
      return values[key];
    },
    removeData(key) {
      delete values[key];
      return this;
    },
    workflowStudio(options) {
      mountCount += 1;
      mountOptions = options;
      values.workflowStudio = studio;
    },
  };
  const jquery = function () {};
  jquery.extend = Object.assign;
  const helpers = campaignHelpers(jquery);
  const initialFlow = workflow(node("n2", "email", { subject: "Initial", body: "Hello" }));
  const latestFlow = workflow(node("n2", "email", { subject: "Latest", body: "Hello" }));
  const options = (flow) => ({
    workflow: flow,
    segments: [{ segment_id: "audience", segment_name: "Audience" }],
  });
  helpers.mountCampaignWorkflowStudio(host, options(initialFlow));
  helpers.mountCampaignWorkflowStudio(host, options(latestFlow));
  assert.equal(mountCount, 1);
  assert.equal(values.workflowStudioPendingOptions.workflow, latestFlow);

  mountOptions.onReady();
  assert.equal(values.workflowStudioReady, true);
  assert.equal(values.workflowStudioPendingOptions, undefined);
  assert.deepEqual(applied, [latestFlow]);

  values.workflowStudioMount = {};
  values.workflowStudioPendingOptions = options(initialFlow);
  mountOptions.onReady();
  assert.deepEqual(applied, [latestFlow]);
  assert.equal(values.workflowStudioPendingOptions.workflow, initialFlow);
});

test("campaign state modal stays centered and keeps lifecycle states scannable", () => {
  const css = fs.readFileSync(path.join(__dirname, "../static/css/app.css"), "utf8");
  const template = fs.readFileSync(
    path.join(__dirname, "../static/templates/campaign/campaign-details.html"),
    "utf8",
  );
  const modalRule = css.match(/#campaign-state-modal\s*\{([^}]*)\}/);
  assert.ok(modalRule);
  assert.match(modalRule[1], /position:\s*fixed/);
  assert.match(modalRule[1], /inset:\s*50%\s+auto\s+auto\s+50%/);
  assert.match(modalRule[1], /transform:\s*translate\(-50%,\s*-50%\)/);
  assert.match(css, /campaign-state-restart-confirm[\s\S]*display:\s*block/);
  assert.match(css, /overflow-wrap:\s*anywhere/);
  for (const state of ["approval", "workflow", "execution"]) {
    assert.match(template, new RegExp(`id="campaign-state-modal-${state}"`));
  }
});

test("obsolete details responses do not overwrite a newer refresh or error state", () => {
  const callbacks = [];
  const messages = {};
  const jquery = (selector) => ({
    data() { return "history"; },
    toggleClass() { return this; },
    addClass() { return this; },
    removeClass() { return this; },
    text(value) { messages[selector] = value; return this; },
  });
  jquery.when = () => {
    const entry = {};
    callbacks.push(entry);
    return {
      done(fn) { entry.done = fn; return this; },
      fail(fn) { entry.fail = fn; return this; },
    };
  };
  const helpers = campaignHelpers(jquery, () => ({}));
  helpers.loadDetails("old-campaign");
  helpers.loadDetails("new-campaign");
  assert.doesNotThrow(() => callbacks[0].done());
  callbacks[0].fail({ responseJSON: { detail: "obsolete error" } });
  assert.equal(messages["#campaign-details-error"], "");
  callbacks[1].fail({ responseJSON: { detail: "current error" } });
  assert.equal(messages["#campaign-details-error"], "current error");
});
