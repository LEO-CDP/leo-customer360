"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function setup() {
  const nodes = new Map();
  const events = {};
  const tables = [];
  const calls = [];
  const dialog = { open: false, showModal() { this.open = true; }, close() { this.open = false; } };
  const document = { getElementById(id) { return id === "item-promotion-modal" ? dialog : null; }, contains() { return true; }, activeElement: null };
  const doc = {
    off() { return this; },
    on(name, selector, handler) { events[name.split(".")[0] + ":" + selector] = handler; return this; }
  };
  function node(selector) {
    if (!nodes.has(selector)) {
      const entry = {
        value: "", content: "", disabled: false, classes: new Set(),
        0: { reset() {} },
        val(value) { if (arguments.length) { this.value = value; return this; } return this.value; },
        text(value) { if (arguments.length) { this.content = value; return this; } return this.content; },
        prop(name, value) { if (arguments.length === 2) { this[name] = value; return this; } return this[name]; },
        addClass(names) { names.split(" ").forEach((name) => this.classes.add(name)); return this; },
        removeClass(names) { names.split(" ").forEach((name) => this.classes.delete(name)); return this; },
        toggleClass() { return this; }, attr() { return this; }, empty() { this.content = ""; return this; },
        trigger() { return this; }, find() { return this; }, remove() { return this; },
        append() { return this; }, filter() { return this; }, children() { return []; }
      };
      nodes.set(selector, entry);
    }
    return nodes.get(selector);
  }
  function $(selector) { return selector === document ? doc : node(selector); }
  $.extend = (...args) => Object.assign(...args);
  $.trim = (value) => String(value == null ? "" : value).trim();
  const config = {
    current: { tenantId: "tenant-a" },
    isAdmin: () => true,
    formatApiErrorDetail: (detail) => typeof detail === "string" ? detail : "",
    api(url, payload, method) {
      const request = {
        url, payload, method,
        done(callback) { this.onDone = callback; return this; },
        fail(callback) { this.onFail = callback; return this; },
        always(callback) { this.onAlways = callback; return this; },
        resolve(value) { this.onDone(value); this.onAlways(); },
        reject(value) { this.onFail(value); this.onAlways(); }
      };
      calls.push(request);
      return request;
    }
  };
  const C360 = {
    config,
    fmt: { DOMAIN_LABELS: {}, domainLabel: (s) => s, titleCase: (s) => s, dateTime: () => "" },
    DataTableView: { create(options) {
      tables.push(options);
      return {
        bindSearch() {}, bindSelect() {}, bindLoadMore() {}, bindRowEdit() {},
        clearFilters() {}, reload() {}, load() {}
      };
    } },
    router: { define() {} }
  };
  const context = { window: { C360 }, document, $, showToast() {}, FormData: function () {}, Set, Number };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../static/js/item-view.js"), "utf8"), context);
  C360.itemView.bindEvents();
  return { C360, nodes, node, events, tables, calls, dialog };
}

function submit(harness) {
  harness.events["submit:#item-promotion-form"]({ preventDefault() {} });
}

const OPTIONS = {
  ad_types: [{ value: "display", label: "Display" }],
  format_codes: [
    { value: "single_banner", label: "Single banner" },
    { value: "native", label: "Native" }
  ]
};

test("content creative conversion validates fields and sends a scoped PUT", () => {
  const h = setup();
  const item = { content_item_id: "content-1", title: "Article", item_type: "article", segment_tags: [] };
  h.tables[0].rowVm(item);
  h.tables[0].onSecondaryAction(item.content_item_id, { focus() {} });
  assert.equal(h.dialog.open, true);
  assert.equal(h.calls[0].url, "/promotions/creative-options");
  assert.equal(h.node("#item-promotion-submit").disabled, true);
  h.calls[0].resolve(OPTIONS);
  submit(h);
  assert.match(h.node("#item-promotion-error").content, /select an ad type and format/i);
  assert.equal(h.calls.length, 1);
  h.node("#item-promotion-ad-type").val("display");
  h.node("#item-promotion-format").val("single_banner");
  h.events["change:#item-promotion-ad-type, #item-promotion-format"]();
  submit(h);
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].url, "/promotions/content/content-1");
  assert.equal(h.calls[1].method, "PUT");
  assert.equal(h.calls[1].payload.ad_type, "display");
  assert.equal(h.calls[1].payload.format_code, "single_banner");
  submit(h);
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ creative_id: 42, status: "draft" });
  assert.match(h.node("#item-promotion-success").content, /Creative #42/);
  assert.equal(h.node("#item-promotion-submit").disabled, false);
});

test("product conversion uses product endpoint and reports errors", () => {
  const h = setup();
  const item = { product_item_id: "sku/1", content_item_id: "content-1", title: "Product", keywords: [] };
  h.tables[1].rowVm(item);
  h.tables[1].onSecondaryAction(item.product_item_id, { focus() {} });
  h.calls[0].resolve(OPTIONS);
  h.node("#item-promotion-ad-type").val("display");
  h.node("#item-promotion-format").val("native");
  submit(h);
  assert.equal(h.calls[1].url, "/promotions/products/sku%2F1");
  h.calls[1].reject({ responseJSON: { detail: "Pause the active creative first." } });
  assert.match(h.node("#item-promotion-error").content, /Pause/);
  assert.equal(h.node("#item-promotion-submit").disabled, false);
});

test("unlinked products and changed tenants cannot submit", () => {
  const h = setup();
  const item = { product_item_id: "sku-2", content_item_id: null, title: "Product", keywords: [] };
  h.tables[1].rowVm(item);
  h.tables[1].onSecondaryAction(item.product_item_id, { focus() {} });
  assert.equal(h.node("#item-promotion-submit").disabled, true);
  assert.match(h.node("#item-promotion-error").content, /no linked content/i);
  submit(h);
  assert.equal(h.calls.length, 0);
  h.C360.config.current.tenantId = "tenant-b";
  submit(h);
  assert.match(h.node("#item-promotion-error").content, /tenant or permissions changed/i);
  assert.equal(h.calls.length, 0);
});

test("creative actions are hidden and cannot open for non-admins", () => {
  const h = setup();
  h.C360.config.isAdmin = () => false;
  assert.equal(h.tables[0].canSecondaryAction(), false);
  h.tables[0].onSecondaryAction("missing", { focus() {} });
  assert.equal(h.dialog.open, false);
});

test("failed or malformed choices disable conversion until retry succeeds", () => {
  const h = setup();
  const item = { content_item_id: "content-1", title: "Article", item_type: "article", segment_tags: [] };
  h.tables[0].rowVm(item);
  h.tables[0].onSecondaryAction(item.content_item_id, { focus() {} });
  h.calls[0].reject({ responseJSON: { detail: "Choices unavailable" } });
  assert.equal(h.node("#item-promotion-error").content, "Choices unavailable");
  assert.equal(h.node("#item-promotion-submit").disabled, true);
  assert.equal(h.node("#item-promotion-options-retry").classes.has("hidden"), false);
  h.events["click:#item-promotion-options-retry"]();
  h.calls[1].resolve({ ad_types: [], format_codes: OPTIONS.format_codes });
  assert.match(h.node("#item-promotion-error").content, /invalid creative choices/i);
  assert.equal(h.node("#item-promotion-submit").disabled, true);
  h.events["click:#item-promotion-options-retry"]();
  h.calls[2].resolve(OPTIONS);
  h.node("#item-promotion-ad-type").val("display");
  h.node("#item-promotion-format").val("native");
  h.events["change:#item-promotion-ad-type, #item-promotion-format"]();
  assert.equal(h.node("#item-promotion-submit").disabled, false);
});

test("late choice responses cannot update a closed or different dialog", () => {
  const h = setup();
  const item = { content_item_id: "content-1", title: "Article", item_type: "article", segment_tags: [] };
  h.tables[0].rowVm(item);
  h.tables[0].onSecondaryAction(item.content_item_id, { focus() {} });
  h.events["click:#item-promotion-close, #item-promotion-cancel"]();
  h.calls[0].resolve(OPTIONS);
  assert.equal(h.dialog.open, false);
  assert.equal(h.node("#item-promotion-submit").disabled, true);
});
