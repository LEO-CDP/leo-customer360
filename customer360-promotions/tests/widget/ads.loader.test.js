"use strict";

// Widget loader tests: serve parameters, event batches, consent and retries.
// Run with: node --test tests/widget/ads.loader.test.js (no dependencies).

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const LOADER = fs.readFileSync(
  path.join(__dirname, "../../static/ads.loader.js"),
  "utf8",
);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REQUEST_ID = "7f3c2b1e-5d4a-4a7e-9b18-0c6d2f4e8a11";

function makeEnv({ scriptAttrs = {}, fetchImpl, beaconImpl } = {}) {
  const storage = new Map();
  const timers = [];
  const listeners = { window: {}, document: {} };
  const calls = { fetch: [], beacon: [] };
  const clock = { now: Date.parse("2026-10-18T09:00:00Z") };

  // Date whose "now" the test controls (dwell time is measured with Date.now()).
  class FakeDate extends Date {
    constructor(...args) {
      super(...(args.length ? args : [clock.now]));
    }
    static now() {
      return clock.now;
    }
  }

  const script = {
    src: "https://ads.example.com/static/ads.loader.js",
    getAttribute: (name) => (name in scriptAttrs ? scriptAttrs[name] : null),
  };

  const document = {
    readyState: "complete",
    currentScript: script,
    referrer: "",
    location: { href: "https://news.example.org/a?utm_source=x", hostname: "news.example.org" },
    visibilityState: "visible",
    head: { appendChild() {} },
    getElementsByClassName: () => [],
    createElement: () => ({
      attrs: {},
      setAttribute(name, value) {
        this.attrs[name] = value;
      },
    }),
    addEventListener: (name, fn) => (listeners.document[name] = fn),
  };

  const window = {
    document,
    screen: { width: 1280, height: 720 },
    location: document.location,
    addEventListener: (name, fn) => (listeners.window[name] = fn),
    __LEO_ADS_TEST__: {},
  };

  const context = {
    window,
    document,
    console: { log() {}, warn() {}, error() {} },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
    navigator: {
      sendBeacon: beaconImpl
        ? (url, blob) => {
            calls.beacon.push({ url, blob });
            return beaconImpl();
          }
        : undefined,
    },
    fetch: async (url, init) => {
      calls.fetch.push({ url, init });
      return fetchImpl ? fetchImpl(url, init) : { ok: true, status: 202, headers: { get: () => null } };
    },
    Blob,
    URL,
    TextEncoder,
    crypto: globalThis.crypto,
    setTimeout: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimeout: () => {},
    __FakeDate: FakeDate,
  };
  context.window.localStorage = context.localStorage;
  vm.createContext(context);
  // A context's own Date wins over a property passed in, so replace it explicitly.
  vm.runInContext("Date = __FakeDate;", context);
  vm.runInContext(LOADER, context);

  return { context, document, window, calls, timers, clock, storage, listeners, internals: window.__LEO_ADS_TEST__ };
}

function makeWidget(env, containerAttrs = {}) {
  const { LeoAdWidget } = env.internals;
  const widget = Object.create(LeoAdWidget.prototype);
  widget.container = {
    getAttribute: (name) => (name in containerAttrs ? containerAttrs[name] : null),
    dataset: {},
    children: [],
    attrs: {},
    addEventListener() {},
    appendChild(child) {
      this.children.push(child);
    },
    querySelector: () => null,
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
  };
  widget.theme = "light";
  widget.adData = { adId: "ad_1001" };
  widget.requestId = REQUEST_ID;
  // What the constructor sets up (it is bypassed to avoid needing a DOM).
  Object.assign(widget, { inView: false, visibleSince: null, dwellMs: 0, hasSentView: false });
  return widget;
}

async function sentBody(call) {
  const body = call.init.body;
  return JSON.parse(typeof body === "string" ? body : await body.text());
}

function flush(env) {
  env.internals.transport.flush();
}

test("the serve request carries leovid, session, site, tenant and consent", async () => {
  const env = makeEnv();
  const widget = makeWidget(env, { "data-ads-tenant": "acme", "data-ads-consent": "granted" });

  const url = new URL(await widget.getAdDataUrl("homepage_top"));

  assert.match(url.searchParams.get("leovid"), UUID);
  assert.match(url.searchParams.get("session"), UUID);
  assert.equal(url.searchParams.get("site"), "news.example.org");
  assert.equal(url.searchParams.get("tenant"), "acme");
  assert.equal(url.searchParams.get("consent"), "granted");
});

test("consent defaults to unknown and junk values are treated as unknown", async () => {
  const env = makeEnv();
  assert.equal(makeWidget(env).getConsent(), "unknown");
  assert.equal(makeWidget(env, { "data-ads-consent": "yes please" }).getConsent(), "unknown");
});

test("only leovid is persisted; the session id stays in memory and is stable per page", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);

  const first = new URL(await widget.getAdDataUrl("p"));
  const second = new URL(await widget.getAdDataUrl("p"));

  assert.deepEqual([...env.storage.keys()], ["leovid"]);
  assert.equal(first.searchParams.get("session"), second.searchParams.get("session"));
  assert.equal(first.searchParams.get("leovid"), env.storage.get("leovid"));
});

test("events go to /events on the loader's own origin with leovid, request_id and event_id", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);

  widget.trackImpression();
  flush(env);

  assert.equal(env.calls.fetch.length, 1);
  assert.equal(env.calls.fetch[0].url, "https://ads.example.com/events");
  assert.equal(env.calls.fetch[0].init.method, "POST");
  assert.equal(env.calls.fetch[0].init.keepalive, true);

  const body = await sentBody(env.calls.fetch[0]);
  assert.equal(body.tenant, "demo");
  assert.equal(body.leovid, env.storage.get("leovid"));
  assert.match(body.session_id, UUID);
  assert.equal(body.site, "news.example.org");
  assert.equal(body.events.length, 1);
  const [event] = body.events;
  assert.equal(event.event_type, "impression");
  assert.equal(event.request_id, REQUEST_ID);
  assert.equal(event.ad_key, "ad_1001");
  assert.match(event.event_id, UUID);
  assert.ok(!Number.isNaN(Date.parse(event.event_time)));
});

test("the events endpoint can be overridden, or follows data-ads-api-base", async () => {
  const override = makeEnv({ scriptAttrs: { "data-ads-events-url": "https://other.example/e" } });
  makeWidget(override).trackImpression();
  flush(override);
  assert.equal(override.calls.fetch[0].url, "https://other.example/e");

  const base = makeEnv({ scriptAttrs: { "data-ads-api-base": "https://api.example.com/ads" } });
  makeWidget(base).trackImpression();
  flush(base);
  assert.equal(base.calls.fetch[0].url, "https://api.example.com/ads/events");
});

test("events queued together share one batch with distinct, stable event ids", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);

  widget.queueEvent("impression");
  widget.queueEvent("view", { dwell_ms: 1500 });
  widget.queueEvent("click");
  widget.queueEvent("dismiss");
  flush(env);

  assert.equal(env.calls.fetch.length, 1);
  const { events } = await sentBody(env.calls.fetch[0]);
  assert.deepEqual(events.map((e) => e.event_type), ["impression", "view", "click", "dismiss"]);
  assert.equal(events[1].dwell_ms, 1500);
  assert.equal(new Set(events.map((e) => e.event_id)).size, 4);
});

test("a batch never exceeds 50 events", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);

  for (let i = 0; i < 120; i += 1) widget.queueEvent("impression");
  flush(env);

  const sizes = [];
  for (const call of env.calls.fetch) sizes.push((await sentBody(call)).events.length);
  assert.deepEqual(sizes, [50, 50, 20]);
});

test("consent denied sends nothing", async () => {
  const env = makeEnv();
  const widget = makeWidget(env, { "data-ads-consent": "denied" });

  widget.trackImpression();
  widget.queueEvent("click");
  flush(env);

  assert.equal(env.calls.fetch.length, 0);
  assert.equal(env.calls.beacon.length, 0);
});

test("no request id from the server means no events", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);
  widget.requestId = null;

  widget.trackImpression();
  flush(env);

  assert.equal(env.calls.fetch.length, 0);
});

test("a click is reported without touching the destination link", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);
  let handler;
  widget.container.addEventListener = (name, fn) => {
    if (name === "click") handler = fn;
  };
  widget.setupClickTracking();

  let prevented = false;
  handler({
    target: { closest: (sel) => (sel === "a[data-track-type]" ? {} : null) },
    preventDefault: () => (prevented = true),
  });
  flush(env);

  assert.equal(prevented, false);
  const { events } = await sentBody(env.calls.fetch[0]);
  assert.equal(events[0].event_type, "click");
});

test("an element marked data-ad-dismiss reports a dismiss", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);
  let handler;
  widget.container.addEventListener = (name, fn) => {
    if (name === "click") handler = fn;
  };
  widget.setupDismissTracking();

  handler({ target: { closest: (sel) => (sel === "[data-ad-dismiss]" ? {} : null) } });
  flush(env);

  const { events } = await sentBody(env.calls.fetch[0]);
  assert.equal(events[0].event_type, "dismiss");
});

// Fire the most recent not-yet-fired retry timer (anything of 1 s or more).
async function fireNextRetry(env, fired) {
  const next = env.timers.find((t) => t.ms >= 1000 && !fired.has(t));
  if (!next) return null;
  fired.add(next);
  next.fn();
  await new Promise((resolve) => setImmediate(resolve));
  return next;
}

test("a 503 is retried after Retry-After with the same event ids", async () => {
  const env = makeEnv({
    fetchImpl: async () => ({ ok: false, status: 503, headers: { get: () => "7" } }),
  });
  makeWidget(env).trackImpression();
  flush(env);
  await new Promise((resolve) => setImmediate(resolve));

  const fired = new Set();
  const retry = await fireNextRetry(env, fired);

  assert.equal(retry.ms, 7000);
  assert.equal(env.calls.fetch.length, 2);
  const [first, second] = await Promise.all(env.calls.fetch.map(sentBody));
  assert.equal(first.events[0].event_id, second.events[0].event_id);
});

test("retries stop after three and back off without Retry-After, capped at 30 s", async () => {
  const env = makeEnv({
    fetchImpl: async () => ({ ok: false, status: 503, headers: { get: () => null } }),
  });
  makeWidget(env).trackImpression();
  flush(env);
  await new Promise((resolve) => setImmediate(resolve));

  const fired = new Set();
  const delays = [];
  for (let i = 0; i < 6; i += 1) {
    const retry = await fireNextRetry(env, fired);
    if (!retry) break;
    delays.push(retry.ms);
  }

  assert.deepEqual(delays, [5000, 10000, 20000]);
  assert.equal(env.calls.fetch.length, 4); // the first attempt plus three retries
});

test("a huge Retry-After is capped at 30 s", async () => {
  const env = makeEnv({
    fetchImpl: async () => ({ ok: false, status: 503, headers: { get: () => "3600" } }),
  });
  makeWidget(env).trackImpression();
  flush(env);
  await new Promise((resolve) => setImmediate(resolve));

  assert.ok(env.timers.some((t) => t.ms === 30000));
});

test("a 429 without Retry-After uses the default delay", async () => {
  const env = makeEnv({
    fetchImpl: async () => ({ ok: false, status: 429, headers: { get: () => null } }),
  });
  makeWidget(env).trackImpression();
  flush(env);
  await new Promise((resolve) => setImmediate(resolve));

  assert.ok(env.timers.some((t) => t.ms === 5000));
});

test("a 202 is not retried", async () => {
  const env = makeEnv();
  makeWidget(env).trackImpression();
  flush(env);
  await new Promise((resolve) => setImmediate(resolve));

  assert.ok(!env.timers.some((t) => t.ms >= 1000 && t.ms !== 500));
  assert.equal(env.calls.fetch.length, 1);
});

test("on pagehide the queue is flushed with sendBeacon", async () => {
  const env = makeEnv({ beaconImpl: () => true });
  makeWidget(env).trackImpression();

  env.listeners.window.pagehide();

  assert.equal(env.calls.beacon.length, 1);
  assert.equal(env.calls.beacon[0].url, "https://ads.example.com/events");
  assert.equal(env.calls.beacon[0].blob.type, "application/json");
  assert.equal(env.calls.fetch.length, 0);
  const body = JSON.parse(await env.calls.beacon[0].blob.text());
  assert.equal(body.events[0].request_id, REQUEST_ID);
});

test("if sendBeacon refuses the payload the unload flush falls back to fetch keepalive", async () => {
  const env = makeEnv({ beaconImpl: () => false });
  makeWidget(env).trackImpression();

  env.listeners.window.pagehide();

  assert.equal(env.calls.fetch.length, 1);
  assert.equal(env.calls.fetch[0].init.keepalive, true);
});

test("the debounce timer flushes the queue", async () => {
  const env = makeEnv();
  makeWidget(env).trackImpression();

  const debounce = env.timers.find((t) => t.ms === 500);
  assert.ok(debounce);
  debounce.fn();

  assert.equal(env.calls.fetch.length, 1);
});

// --------------------------------------------------------------------------
// Dismiss control and dwell time
// --------------------------------------------------------------------------

test("the widget adds a visible dismiss control and clicking it sends a dismiss, then hides the ad", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);
  let handler;
  widget.container.addEventListener = (name, fn) => {
    if (name === "click") handler = fn;
  };

  widget.setupDismissTracking();

  const [button] = widget.container.children;
  assert.ok(button, "a control was added to the ad");
  assert.equal(button.className, "leo-ad-dismiss");
  assert.equal(button.type, "button");
  assert.ok("data-ad-dismiss" in button.attrs);
  assert.ok(button.attrs["aria-label"]);

  handler({ target: { closest: (sel) => (sel === "[data-ad-dismiss]" ? button : null) } });
  flush(env);

  const { events } = await sentBody(env.calls.fetch[0]);
  assert.deepEqual(events.map((e) => e.event_type), ["dismiss"]);
  assert.ok("hidden" in widget.container.attrs);
});

test("a view carrying dwell_ms is sent when the ad leaves view", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);

  widget.inView = true;
  widget.startDwell();
  env.clock.now += 2500;
  widget.inView = false;
  widget.pauseDwell();
  flush(env);

  const { events } = await sentBody(env.calls.fetch[0]);
  assert.deepEqual(events.map((e) => e.event_type), ["view"]);
  assert.equal(events[0].dwell_ms, 2500);
});

test("dwell below the 1000 ms server minimum sends no view", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);

  widget.inView = true;
  widget.startDwell();
  env.clock.now += 999;
  widget.pauseDwell();
  flush(env);

  assert.equal(env.calls.fetch.length, 0);
});

test("dwell accumulates across separate visits and one view is sent per ad", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);

  widget.inView = true;
  widget.startDwell();
  env.clock.now += 600;
  widget.pauseDwell();
  env.clock.now += 5000; // out of view: not counted
  widget.startDwell();
  env.clock.now += 700;
  widget.pauseDwell();
  widget.startDwell();
  env.clock.now += 5000;
  widget.pauseDwell();
  flush(env);

  const { events } = await sentBody(env.calls.fetch[0]);
  assert.equal(events.length, 1);
  assert.equal(events[0].dwell_ms, 1300);
});

test("time with the tab hidden is not counted", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);
  env.document.visibilityState = "hidden";

  widget.inView = true;
  widget.startDwell();
  env.clock.now += 5000;
  widget.pauseDwell();
  flush(env);

  assert.equal(env.calls.fetch.length, 0);
});

test("hiding the tab sends the view with its dwell time and flushes", async () => {
  const env = makeEnv({ beaconImpl: () => true });
  const widget = makeWidget(env);
  env.internals.widgets.add(widget);

  widget.inView = true;
  widget.startDwell();
  env.clock.now += 1800;
  env.document.visibilityState = "hidden";
  env.listeners.document.visibilitychange();

  assert.equal(env.calls.beacon.length, 1);
  const body = JSON.parse(await env.calls.beacon[0].blob.text());
  assert.equal(body.events[0].event_type, "view");
  assert.equal(body.events[0].dwell_ms, 1800);
});

test("pagehide sends the view with its dwell time and flushes", async () => {
  const env = makeEnv({ beaconImpl: () => true });
  const widget = makeWidget(env);
  env.internals.widgets.add(widget);

  widget.inView = true;
  widget.startDwell();
  env.clock.now += 1200;
  env.listeners.window.pagehide();

  const body = JSON.parse(await env.calls.beacon[0].blob.text());
  assert.equal(body.events[0].event_type, "view");
  assert.equal(body.events[0].dwell_ms, 1200);
});

test("returning to the tab restarts the dwell clock for ads still in view", async () => {
  const env = makeEnv();
  const widget = makeWidget(env);
  env.internals.widgets.add(widget);
  widget.inView = true;

  env.document.visibilityState = "visible";
  env.listeners.document.visibilitychange();
  assert.notEqual(widget.visibleSince, null);
});
