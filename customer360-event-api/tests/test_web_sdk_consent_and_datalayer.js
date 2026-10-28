"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const proxySource = fs.readFileSync(
    path.join(__dirname, "../static/c360-web-sdk/observer/leo.proxy.js"),
    "utf8"
);
const observerSource = fs.readFileSync(
    path.join(__dirname, "../static/c360-web-sdk/observer/leo.observer.js"),
    "utf8"
);
const iframeHtml = fs.readFileSync(
    path.join(__dirname, "../static/c360-web-sdk/html/cdp-event-proxy.html"),
    "utf8"
);

function loadPageProxy(consentGranted) {
    const listeners = {};
    const timers = [];
    const iframeMessages = [];
    let iframe = null;
    let removedFrames = 0;
    const window = {
        leoTrackingConsent: consentGranted,
        location: {
            origin: "https://shop.example",
            protocol: "https:",
            host: "shop.example",
            href: "https://shop.example/",
            search: "",
            hash: ""
        },
        crypto: { randomUUID: () => "event-id" },
        screen: { width: 1280, height: 800 },
        console,
        URL,
        CustomEvent: class CustomEvent {
            constructor(type, init) {
                this.type = type;
                Object.assign(this, init);
            }
        },
        addEventListener(name, callback) {
            (listeners[name] ||= []).push(callback);
        },
        dispatchEvent(event) {
            (listeners[event.type] || []).forEach((callback) => callback(event));
        }
    };
    const document = {
        currentScript: { src: "https://cdn.example/leo.proxy.js" },
        title: "test",
        referrer: "",
        location: window.location,
        getElementsByTagName(name) {
            if (name === "script") return [this.currentScript];
            if (name === "body") return [this.body];
            return [];
        },
        getElementById(id) {
            return iframe && iframe.id === id ? iframe : null;
        },
        createElement() {
            return {
                setAttribute() {},
                addEventListener() {},
                contentWindow: {
                    postMessage(message) {
                        iframeMessages.push(message);
                        if (message.call === "setConsent" && message.granted === false) {
                            window.LeoObserverProxy.messageHandler({
                                event: "LeoConsentRevoked",
                                requestId: message.requestId,
                                storageCleared: true
                            });
                        }
                    }
                },
                remove() {
                    removedFrames += 1;
                    iframe = null;
                }
            };
        },
        body: {
            appendChild(node) {
                iframe = node;
            }
        },
        documentElement: {
            appendChild(node) {
                iframe = node;
            }
        }
    };
    window.document = document;
    const context = vm.createContext({
        window,
        document,
        URL,
        CustomEvent: window.CustomEvent,
        console,
        setTimeout(callback) {
            timers.push(callback);
            return timers.length;
        },
        clearTimeout() {},
        encodeURIComponent,
        decodeURIComponent,
        Date,
        Math,
        Array,
        Object,
        String,
        Number,
        JSON,
        RegExp
    });

    vm.runInContext(proxySource, context);
    return {
        window,
        timers,
        iframeMessages,
        get iframe() {
            return iframe;
        },
        get removedFrames() {
            return removedFrames;
        }
    };
}

function loadObserver(consentGranted) {
    const storedValues = new Map();
    const localStorage = {
        get length() {
            return storedValues.size;
        },
        getItem(key) {
            return storedValues.has(key) ? storedValues.get(key) : null;
        },
        setItem(key, value) {
            storedValues.set(key, String(value));
        },
        removeItem(key) {
            storedValues.delete(key);
        },
        key(index) {
            return Array.from(storedValues.keys())[index] || null;
        }
    };
    const metrics = { beacons: 0, xhrSends: 0, xhrAborts: 0 };
    class FakeXHR {
        open() {
            this.readyState = 1;
        }

        setRequestHeader() {}

        send() {
            metrics.xhrSends += 1;
        }

        abort() {
            metrics.xhrAborts += 1;
            this.readyState = 4;
            if (this.onreadystatechange) this.onreadystatechange();
        }
    }

    const location = {
        href: "https://shop.example/",
        protocol: "https:",
        host: "shop.example",
        hostname: "shop.example"
    };
    const document = {
        location,
        referrer: "",
        title: "shop",
        cookie: "",
        addEventListener() {},
        getElementsByTagName() {
            return [];
        },
        documentElement: { appendChild() {} }
    };
    const window = {
        LEO_SESSION_NAMESPACE_UUID: "11111111-1111-4111-8111-111111111111",
        LEO_TRACKING_CONSENT: consentGranted,
        crypto: webcrypto,
        localStorage,
        document,
        navigator: {
            sendBeacon() {
                metrics.beacons += 1;
                return true;
            }
        },
        XMLHttpRequest: FakeXHR,
        URL,
        Blob,
        TextEncoder,
        Uint8Array,
        console,
        addEventListener() {},
        setInterval() {
            return 1;
        },
        clearInterval() {}
    };
    const context = vm.createContext({
        window,
        document,
        localStorage,
        navigator: window.navigator,
        XMLHttpRequest: FakeXHR,
        URL,
        Blob,
        TextEncoder,
        Uint8Array,
        crypto: webcrypto,
        console,
        setInterval: window.setInterval,
        clearInterval: window.clearInterval,
        setTimeout,
        clearTimeout,
        Date,
        Math,
        Array,
        Object,
        String,
        Number,
        JSON,
        RegExp,
        Error,
        TypeError,
        unescape,
        encodeURIComponent,
        decodeURIComponent,
        isFinite,
        parseInt,
        Uint32Array
    });

    vm.runInContext(observerSource, context);
    return { window, localStorage, storedValues, metrics };
}

function loadIframeProxy(search) {
    const listeners = {};
    const parentMessages = [];
    const timers = [];
    const scripts = [];
    const storage = new Map();
    const parent = {
        postMessage(message, targetOrigin) {
            parentMessages.push({ message, targetOrigin });
        }
    };
    const window = {
        location: {
            search,
            hash: "#beta.leocdp.com_https%3A%2F%2Fshop.example_11111111-1111-4111-8111-111111111111",
            protocol: "https:"
        },
        parent,
        localStorage: {
            removeItem(key) {
                storage.delete(key);
            }
        },
        console,
        addEventListener(name, callback) {
            (listeners[name] ||= []).push(callback);
        }
    };
    const container = {
        appendChild(script) {
            scripts.push(script);
        }
    };
    const document = {
        getElementById(id) {
            return id === "leoproxy" ? container : null;
        },
        createElement() {
            return { setAttribute() {} };
        }
    };
    const scriptBody = iframeHtml.match(/<script>\s*([\s\S]*?)\s*<\/script>/i);
    assert.ok(scriptBody, "iframe should contain its inline proxy script");
    const context = vm.createContext({
        window,
        document,
        location: window.location,
        URL,
        URLSearchParams,
        console,
        setTimeout(callback) {
            timers.push(callback);
            return timers.length;
        },
        clearTimeout() {},
        encodeURIComponent,
        decodeURIComponent,
        Date,
        Math,
        Array,
        Object,
        String,
        Number,
        JSON,
        RegExp
    });

    vm.runInContext(scriptBody[1], context);
    timers.splice(0).forEach((callback) => callback());
    return {
        window,
        parent,
        parentMessages,
        listeners,
        scripts,
        storage
    };
}

test("proxy blocks denied consent and maps nested Data Layer conversions", () => {
    const page = loadPageProxy(false);
    page.timers.forEach((callback) => callback());
    assert.equal(page.iframe, null);
    assert.equal(page.window.LeoObserverProxy.recordActionEvent("click", {}), false);

    page.window.dataLayer = [];
    page.window.scopeDataLayer = [];
    const queuedEvents = [];
    page.window.addEventListener("leo_data_layer_event_queued", (event) => {
        queuedEvents.push(event.detail);
    });
    const eventMap = {
        leo_purchase: {
            metricName: "purchase",
            type: "conversion",
            dataPath: "ecommerce",
            transactionIdPath: "ecommerce.transaction_id",
            valuePath: "ecommerce.value",
            currencyPath: "ecommerce.currency",
            itemsPath: "ecommerce.items"
        }
    };
    const watcher = page.window.LeoObserverProxy.watchDataLayer({
        dataLayerName: "dataLayer",
        eventMap
    });
    const scopeWatcher = page.window.LeoObserverProxy.watchDataLayer({
        dataLayerName: "scopeDataLayer",
        eventMap
    });

    page.window.dataLayer.push({
        event: "leo_purchase",
        ecommerce: {
            transaction_id: "order-1",
            value: 10,
            currency: "USD",
            items: [{ item_id: "sku-1", details: { tags: ["a", "b"] } }]
        }
    });
    assert.equal(queuedEvents.length, 0);
    assert.equal(watcher.isWatching(), false);

    page.window.LeoObserverProxy.setConsent(true);
    assert.ok(page.iframe);
    assert.match(page.iframe.src, /leo_tracking=disabled/);
    page.window.LeoObserverProxy.messageHandler({ event: "LeoConsentBridgeReady" });
    assert.equal(page.iframeMessages.at(-1).call, "setConsent");
    assert.equal(page.iframeMessages.at(-1).granted, true);
    assert.equal(watcher.isWatching(), true);
    assert.equal(scopeWatcher.isWatching(), true);

    page.window.dataLayer.push({
        event: "leo_purchase",
        ecommerce: {
            transaction_id: "order-2",
            value: 20,
            currency: "USD",
            items: [{ item_id: "sku-2", details: { tags: ["nested", "array"] } }]
        }
    });
    assert.equal(queuedEvents.length, 1);
    assert.deepEqual(
        Array.from(queuedEvents[0].eventData.items[0].details.tags),
        ["nested", "array"]
    );
    page.window.scopeDataLayer.push({
        event: "leo_purchase",
        ecommerce: {
            transaction_id: "order-3",
            value: 30,
            currency: "USD",
            items: [{ item_id: "sku-3", details: { tags: ["scope", "layer"] } }]
        }
    });
    assert.equal(queuedEvents.length, 2);
    assert.equal(queuedEvents[1].dataLayerName, "scopeDataLayer");

    page.window.LeoObserverProxy.setConsent(false);
    assert.equal(page.iframe, null);
    assert.equal(page.removedFrames, 1);
    assert.equal(page.window.LeoObserverProxy.recordActionEvent("click", {}), false);
    page.window.dataLayer.push({
        event: "leo_purchase",
        ecommerce: { items: [] }
    });
    page.window.scopeDataLayer.push({
        event: "leo_purchase",
        ecommerce: { items: [] }
    });
    assert.equal(queuedEvents.length, 2);
});

test("unknown consent preserves legacy allow without pre-authorizing iframe assets", () => {
    const page = loadPageProxy(undefined);
    page.timers.forEach((callback) => callback());
    assert.ok(page.iframe);
    assert.equal(page.window.LeoObserverProxy.consentGranted, true);
    assert.match(page.iframe.src, /leo_tracking=disabled/);
});

test("iframe waits for parent consent before requesting observer assets", () => {
    const iframe = loadIframeProxy("?leo_tracking=disabled");
    assert.equal(iframe.window.LeoObserverProxy.getStatus().trackingAllowed, false);
    assert.equal(iframe.scripts.length, 0);
    assert.equal(iframe.parentMessages[0].message.event, "LeoConsentBridgeReady");

    const parentMessageHandler = iframe.listeners.message[0];
    parentMessageHandler({
        source: iframe.parent,
        origin: "https://shop.example",
        data: { call: "setConsent", granted: true }
    });
    assert.equal(iframe.window.LeoObserverProxy.getStatus().trackingAllowed, true);
    assert.equal(iframe.scripts.length, 1);
    assert.match(iframe.scripts[0].src, /fingerprintjs2/);

    parentMessageHandler({
        source: iframe.parent,
        origin: "https://shop.example",
        data: { call: "setConsent", granted: false, requestId: "revoke-1" }
    });
    assert.equal(iframe.window.LeoObserverProxy.getStatus().trackingAllowed, false);
    assert.equal(iframe.parentMessages.at(-1).message.event, "LeoConsentRevoked");
    assert.equal(iframe.parentMessages.at(-1).message.requestId, "revoke-1");
});

test("observer blocks initial denial and aborts XHR on consent withdrawal", () => {
    const observer = loadObserver(false);
    const sdk = observer.window.LeoEventObserver;
    assert.equal(sdk.doTracking("action", { batchsize: 1 }), false);
    assert.equal(observer.metrics.beacons + observer.metrics.xhrSends, 0);
    assert.equal(observer.storedValues.size, 0);

    sdk.setConsent(true);
    sdk.doTracking("action", {
        batchsize: 1,
        custom: { items: [{ item_id: "sku-1" }] }
    });
    assert.equal(observer.metrics.beacons + observer.metrics.xhrSends, 1);

    observer.window.LeoCorsRequest.post("https://tracking.example/logs", "{}");
    sdk.setConsent(false);
    assert.equal(observer.metrics.xhrAborts, 1);
    assert.equal(observer.storedValues.size, 0);
    const sentBeforeDeniedCall = observer.metrics.beacons + observer.metrics.xhrSends;
    assert.equal(sdk.doTracking("action", { batchsize: 1 }), false);
    observer.window.LeoCorsRequest.batchSend(
        "https://tracking.example/logs",
        { event_id: "blocked" },
        1
    );
    assert.equal(observer.metrics.beacons + observer.metrics.xhrSends, sentBeforeDeniedCall);
});
