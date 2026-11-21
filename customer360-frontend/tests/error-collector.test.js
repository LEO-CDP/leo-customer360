"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../static/js/common/config.js"), "utf8");

// opts: serverConfig, noJquery, beacon ("ok" | "throw"), fetchImpl
function load(opts = {}) {
  const beacons = [];
  const fetches = [];
  const state = { handler: null };
  const document = { documentElement: { classList: { toggle() {} }, setAttribute() {} }, getElementById() { return null; } };
  const navigator = {
    sendBeacon: (url, blob) => {
      beacons.push({ url, blob });
      if (opts.beacon === "throw") throw new Error("beacon boom");
      return true;
    },
  };
  const context = {
    window: { C360: {}, C360_SERVER_CONFIG: opts.serverConfig || {} },
    document,
    navigator,
    localStorage: { getItem() { return null; } },
    atob: (v) => Buffer.from(v, "base64").toString("binary"),
    setTimeout() {}, clearTimeout() {},
    Blob,
    fetch: (url, init) => {
      fetches.push({ url, init });
      return opts.fetchImpl ? opts.fetchImpl(url, init) : Promise.resolve({ ok: true, json: async () => ({}) });
    },
  };
  if (!opts.noJquery) {
    const $ = () => ({ ajaxError(fn) { state.handler = fn; } });
    $.fn = {};
    context.$ = $;
  }
  vm.runInNewContext(source, context);
  return {
    context, beacons, fetches,
    ajaxError: (status, settings = { type: "GET", url: "/api/v1/x/1" }) => state.handler({}, { status }, settings),
    hasHandler: () => typeof state.handler === "function",
  };
}

test("500 reports via sendBeacon with {method,url,status} Blob", async () => {
  const env = load();
  env.ajaxError(500, { type: "POST", url: "/api/v1/x/1" });
  assert.equal(env.beacons.length, 1);
  assert.equal(env.beacons[0].url, "/client-errors");
  assert.equal(JSON.parse(await env.beacons[0].blob.text()).status, 500);
  assert.deepEqual(JSON.parse(await env.beacons[0].blob.text()), { method: "POST", url: "/api/v1/x/1", status: 500 });
  assert.equal(env.fetches.length, 0);
});

test("uses injected errorCollectorPath", () => {
  const env = load({ serverConfig: { errorCollectorPath: "/c360/client-errors" } });
  env.ajaxError(500);
  assert.equal(env.beacons[0].url, "/c360/client-errors");
});

test("only 400-599 are sent; 0/200/302 are not", () => {
  const env = load();
  for (const s of [0, 200, 302]) env.ajaxError(s);
  assert.equal(env.beacons.length, 0);
  assert.equal(env.fetches.length, 0);
  env.ajaxError(400);
  env.ajaxError(599);
  assert.equal(env.beacons.length, 2);
});

test("requests to the collector itself are never reported", () => {
  const env = load({ serverConfig: { errorCollectorPath: "/c360/client-errors" } });
  env.ajaxError(500, { type: "POST", url: "/c360/client-errors" });
  assert.equal(env.beacons.length + env.fetches.length, 0);
});

test("sendBeacon throwing does not escape the handler", () => {
  const env = load({ beacon: "throw" });
  assert.doesNotThrow(() => env.ajaxError(500));
});

test("docsAsk non-ok response reports POST <docsAiBase>/ask with its status", async () => {
  const env = load({
    fetchImpl: (url) => Promise.resolve({ ok: false, status: 502, json: async () => ({}) }),
  });
  await assert.rejects(env.context.window.C360.config.docsAsk("hi"), (e) => e.status === 502);
  assert.equal(env.beacons.length, 1);
  assert.equal(env.beacons[0].url, "/client-errors");
  assert.deepEqual(JSON.parse(await env.beacons[0].blob.text()), { method: "POST", url: "/ai/ask", status: 502 });
});

test("config.js loads without jQuery", () => {
  const env = load({ noJquery: true });
  assert.equal(env.hasHandler(), false);
  assert.equal(typeof env.context.window.C360.config.docsAsk, "function");
});
