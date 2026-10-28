/**
 * Ad Banner Rendering & Tracking Engine
 * Supports:
 * - Local native/banner/carousel ads
 * - Google Ads / Google Ad Manager JS tags
 * - Affiliate JS-tag ads
 * - Native JSON ads
 * - Impression, view, click and dismiss tracking (batched to POST /events)
 * - Responsive placements
 * - Light/dark theme support
 */
(() => {
  "use strict";

  const VERSION = "1.0.0";
  const jsdoc = window.document;

  const STYLE_ID = "leo-ad-widget-styles";
  const GITHUB_CDN = "https://gcore.jsdelivr.net/gh/LEO-CDP/leo-customer360@latest/customer360-promotions/static/";

  const DEFAULT_CDN = jsdoc.currentScript?.getAttribute("data-base-cdn") || GITHUB_CDN;

  const rawApiBase =
    jsdoc.currentScript?.getAttribute("data-ads-api-base") || DEFAULT_CDN;

  const DEFAULT_API_BASE = rawApiBase.endsWith("/")
    ? rawApiBase
    : `${rawApiBase}/`;

  console.log("Leo Ad Widget Loader initialized. Using CDN:", DEFAULT_CDN);
  console.log("Leo Ad Widget Loader initialized. Using API base:", DEFAULT_API_BASE);

  /**
   * ------------------------------------------------------------
   * EVENT TRANSPORT (POST {ad server}/events)
   * ------------------------------------------------------------
   *
   * Options (script tag or ad container attributes; the container wins):
   *   data-ads-events-url  full URL of the events endpoint (override)
   *   data-ads-tenant      tenant key (default "demo")
   *   data-ads-site        opaque site key (default: page hostname)
   *   data-ads-consent     granted | denied | unknown (default unknown)
   *
   * Without an events-url override the endpoint is `events` under
   * data-ads-api-base when that is set, otherwise `/events` on the origin this
   * loader was served from.
   *
   * Events are queued, sent as one batch after a short debounce (at most 50 per
   * call) and flushed with sendBeacon when the page is hidden or unloaded. A
   * 429 or 503 answer is retried up to 3 times: after its Retry-After delay,
   * or a doubling back-off from 5 s when it has none, never more than 30 s.
   * The events keep their event_id, so a retry is never counted twice. Nothing is sent
   * while consent is "denied". Only the existing `leovid` is stored in the
   * browser; the session id lives in memory for this page load.
   */
  const EVENT_BATCH_MAX = 50;
  const EVENT_FLUSH_DELAY_MS = 500;
  const RETRY_AFTER_DEFAULT_S = 5;
  const RETRY_AFTER_MAX_S = 30;
  const MAX_RETRIES = 3;
  const VIEW_MIN_DWELL_MS = 1000; // the server rejects a view below this

  const scriptOption = (name) => jsdoc.currentScript?.getAttribute(name) || "";

  const resolveEventsUrl = () => {
    const override = scriptOption("data-ads-events-url");

    if (override) {
      return override;
    }

    if (scriptOption("data-ads-api-base")) {
      return `${DEFAULT_API_BASE}events`;
    }

    try {
      return new URL(
        "/events",
        jsdoc.currentScript?.src || window.location.href,
      ).toString();
    } catch (error) {
      return `${DEFAULT_API_BASE}events`;
    }
  };

  class EventTransport {
    constructor(getUrl) {
      this.getUrl = getUrl;
      this.queue = [];
      this.timer = null;
    }

    add(entry) {
      this.queue.push(entry);

      if (!this.timer) {
        this.timer = setTimeout(() => {
          this.timer = null;
          this.flush();
        }, EVENT_FLUSH_DELAY_MS);
      }
    }

    flush({ unloading = false } = {}) {
      if (this.timer) {
        clearTimeout(this.timer);
        this.timer = null;
      }

      const entries = this.queue.splice(0);
      const groups = new Map();

      entries.forEach((entry) => {
        const key = JSON.stringify([
          entry.tenant,
          entry.site,
          entry.leovid,
          entry.sessionId,
        ]);

        if (!groups.has(key)) {
          groups.set(key, { head: entry, events: [] });
        }

        groups.get(key).events.push(entry.event);
      });

      groups.forEach(({ head, events }) => {
        for (let i = 0; i < events.length; i += EVENT_BATCH_MAX) {
          this.send(
            {
              tenant: head.tenant,
              leovid: head.leovid,
              session_id: head.sessionId,
              site: head.site,
              events: events.slice(i, i + EVENT_BATCH_MAX),
            },
            unloading,
            false,
            0,
          );
        }
      });
    }

    send(body, unloading, attempt) {
      const url = this.getUrl();
      const blob = new Blob([JSON.stringify(body)], {
        type: "application/json",
      });

      // sendBeacon survives navigation but cannot report a status, so it is
      // only used when the page is going away.
      if (unloading && typeof navigator.sendBeacon === "function") {
        try {
          if (navigator.sendBeacon(url, blob)) {
            return;
          }
        } catch (error) {
          console.warn("sendBeacon failed", error);
        }
      }

      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: blob,
        keepalive: true,
        credentials: "omit",
      })
        .then((response) => {
          if (
            (response.status === 429 || response.status === 503) &&
            attempt < MAX_RETRIES
          ) {
            const header = Number(response.headers?.get("Retry-After"));
            const seconds = Math.min(
              Math.max(
                header > 0 ? header : RETRY_AFTER_DEFAULT_S * 2 ** attempt,
                1,
              ),
              RETRY_AFTER_MAX_S,
            );

            setTimeout(
              () => this.send(body, false, attempt + 1),
              seconds * 1000,
            );
          }
        })
        .catch(() => {});
    }
  }

  const transport = new EventTransport(resolveEventsUrl);
  let sessionId = null;

  // Widgets on this page, so a page hide can close their dwell measurement
  // before the queue is flushed.
  const widgets = new Set();

  window.addEventListener("pagehide", () => {
    widgets.forEach((widget) => widget.pauseDwell());
    transport.flush({ unloading: true });
  });
  jsdoc.addEventListener("visibilitychange", () => {
    if (jsdoc.visibilityState === "hidden") {
      widgets.forEach((widget) => widget.pauseDwell());
      transport.flush({ unloading: true });
    } else {
      widgets.forEach((widget) => widget.startDwell());
    }
  });

  class LeoAdWidget {
    constructor(container) {
      this.container = container;
      this.adData = {};
      this.impressionObserver = null;
      this.impressionTimer = null;
      this.hasFiredImpression = false;
      this.requestId = null;
      this.inView = false;
      this.visibleSince = null;
      this.dwellMs = 0;
      this.hasSentView = false;
      widgets.add(this);

      this.theme = this.resolveTheme();

      this.container.dataset.adTheme = this.theme;
      this.container.classList.add(`leo-ad-theme-${this.theme}`);

      this.setContainerStyles();
      this.init();
    }

    /**
     * ------------------------------------------------------------
     * CONTAINER / THEME
     * ------------------------------------------------------------
     */

    setContainerStyles() {
      const maxWidth =
        this.container.getAttribute("data-ad-max-width") || "100%";

      const minWidth =
        this.container.getAttribute("data-ad-min-width") || "280px";

      const width = this.container.getAttribute("data-ad-width") || "100%";

      this.container.style.setProperty("--leo-ad-max-width", maxWidth);

      this.container.style.setProperty("--leo-ad-min-width", minWidth);

      this.container.style.setProperty("--leo-ad-width", width);
    }

    resolveTheme() {
      const explicitTheme = (this.container.getAttribute("data-ad-theme") || "")
        .trim()
        .toLowerCase();

      if (explicitTheme === "dark" || explicitTheme === "light") {
        return explicitTheme;
      }

      const prefersDark =
        window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches;

      const parentDark = this.container.closest(".dark, [data-theme='dark']");

      return parentDark || prefersDark ? "dark" : "light";
    }

    /**
     * ------------------------------------------------------------
     * INITIALIZATION
     * ------------------------------------------------------------
     */

    async init() {
      const placementId = this.container.getAttribute("data-ad-placement");

      if (!placementId) {
        console.warn("LeoAdWidget: missing data-ad-placement");

        return;
      }

      await this.loadAdData(placementId);
    }

    /**
     * ------------------------------------------------------------
     * AD DATA URL
     * ------------------------------------------------------------
     */

    async getAdDataUrl(placementId) {
      const dataAdFormat = this.container.getAttribute("data-ad-format") || "";

      const dataAdTheme =
        this.container.getAttribute("data-ad-theme") || this.theme;

      const customEndpoint =
        this.container.getAttribute("data-ad-data-url") ||
        `${DEFAULT_API_BASE}serve/${encodeURIComponent(placementId)}`;

      const url = new URL(customEndpoint, window.location.href);

      const params = url.searchParams;

      if (!placementId) {
        throw new Error(
          "Ad placement ID is required to build the ad data URL.",
        );
      }

      params.set("leopmid", placementId);

      if (dataAdFormat) {
        params.set("format", dataAdFormat);
      }

      if (dataAdTheme) {
        params.set("theme", dataAdTheme);
      }

      const screenWidth = window.screen?.width ?? 0;

      const screenHeight = window.screen?.height ?? 0;

      if (screenWidth || screenHeight) {
        params.set("screen", `${screenWidth}x${screenHeight}`);
      }

      params.set("leovid", this.getOrCreateVisitorId());

      // Personalisation inputs (all optional on the server).
      params.set("tenant", this.getOption("tenant", "demo"));

      params.set("session", this.getSessionId());

      params.set("site", this.getOption("site", jsdoc.location?.hostname || ""));

      params.set("consent", this.getConsent());

      params.set("url", jsdoc.location.href);

      params.set("referrer", jsdoc.referrer || "");

      params.set("timestamp", new Date().toISOString());

      params.set("leotpid", await this.getTouchpointId());

      return url.toString();
    }

    /**
     * ------------------------------------------------------------
     * TOUCHPOINT
     * ------------------------------------------------------------
     */

    async getTouchpointId() {
      const url = new URL(window.location.href);

      const ignoredParams = [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
      ];

      ignoredParams.forEach((param) => {
        url.searchParams.delete(param);
      });

      return await this.sha256(url.toString());
    }

    async sha256(value) {
      const bytes = new TextEncoder().encode(value);

      const hashBuffer = await crypto.subtle.digest("SHA-256", bytes);

      const hashArray = Array.from(new Uint8Array(hashBuffer));

      return hashArray
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
    }

    /**
     * ------------------------------------------------------------
     * VISITOR ID
     * ------------------------------------------------------------
     */

    getSessionId() {
      // In memory only: never written to storage.
      if (!sessionId) {
        sessionId = this.generateUUID();
      }

      return sessionId;
    }

    getOption(name, fallback) {
      const attribute = `data-ads-${name}`;

      return (
        this.container.getAttribute(attribute) ||
        scriptOption(attribute) ||
        fallback
      );
    }

    getConsent() {
      const value = String(this.getOption("consent", "unknown")).toLowerCase();

      return value === "granted" || value === "denied" ? value : "unknown";
    }

    getOrCreateVisitorId() {
      const key = "leovid";

      let visitorId = localStorage.getItem(key);

      if (visitorId && this.isValidUUID(visitorId)) {
        return visitorId;
      }

      visitorId = this.generateUUID();

      localStorage.setItem(key, visitorId);

      return visitorId;
    }

    generateUUID() {
      if (typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
      }

      const bytes = new Uint8Array(16);

      crypto.getRandomValues(bytes);

      bytes[6] = (bytes[6] & 0x0f) | 0x40;

      bytes[8] = (bytes[8] & 0x3f) | 0x80;

      const hex = Array.from(bytes, (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join("");

      return [
        hex.slice(0, 8),
        hex.slice(8, 12),
        hex.slice(12, 16),
        hex.slice(16, 20),
        hex.slice(20),
      ].join("-");
    }

    isValidUUID(value) {
      return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        value,
      );
    }

    /**
     * ------------------------------------------------------------
     * LOAD AD DATA
     * ------------------------------------------------------------
     */

    async loadAdData(placementId) {
      if (!this.container) {
        return;
      }

      if (this.container.getAttribute("data-ad-loading") === "true") {
        console.warn(
          `Ad data is already loading for placement: ${placementId}`,
        );

        return;
      }

      this.container.setAttribute("data-ad-loading", "true");

      this.renderSkeleton();

      try {
        const adDataUrl = await this.getAdDataUrl(placementId);

        const response = await fetch(adDataUrl, {
          cache: "no-store",
        });

        if (!response.ok) {
          throw new Error(`Failed to fetch ad data: ${response.status}`);
        }

        const payload = await response.json();

        // Correlates later events with the stored serving decision.
        this.requestId = payload?.request_id || null;

        this.adData = this.selectAdForPlacement(payload, placementId);

        if (!this.adData) {
          throw new Error(`No ad data found for placement: ${placementId}`);
        }

        this.applyBannerDimensions(this.adData.placement);

        this.renderAd();

        this.setupImpressionTracking();

        this.setupClickTracking();

        this.setupDismissTracking();
      } catch (error) {
        console.error("Unable to load ad data", error);

        this.renderError("Unable to load ad data");
      } finally {
        this.container.setAttribute("data-ad-loading", "false");
      }
    }

    selectAdForPlacement(payload, placementId) {
      const ads = Array.isArray(payload?.ads) ? payload.ads : [payload];

      return (
        ads.find((ad) => String(ad?.adPlacementId) === String(placementId)) ||
        null
      );
    }

    /**
     * ------------------------------------------------------------
     * DIMENSIONS
     * ------------------------------------------------------------
     */

    applyBannerDimensions(placement) {
      const width = Number(placement?.width) || 100;

      const height = Number(placement?.height) || 0;

      const unit = placement?.unit || "%";

      this.container.style.setProperty("--leo-ad-banner-w", width);

      this.container.style.setProperty("--leo-ad-banner-h", height);

      this.container.dataset.adUnit = unit;

      if (placement?.responsive === true) {
        this.container.style.width = "100%";
      }
    }

    /**
     * ------------------------------------------------------------
     * SKELETON
     * ------------------------------------------------------------
     */

    renderSkeleton() {
      const placeholders = Array.from(
        { length: 4 },
        () => '<div class="ad-widget-skeleton"></div>',
      ).join("");

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">
          <div class="ad-widget-header">
            <span>Ad</span> Loading...
          </div>

          <div
            class="ad-widget-grid"
            style="--leo-ad-columns: 4;"
          >
            ${placeholders}
          </div>
        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * MAIN RENDERER
     * ------------------------------------------------------------
     */

    renderAd() {
      const data = this.adData;

      if (!data) {
        return;
      }

      /**
       * New architecture:
       *
       * rendering.type:
       *
       * native_json
       * js_tag
       *
       * JS-tag ads are delegated to external
       * provider-specific rendering logic.
       */
      if (data?.rendering?.type === "js_tag") {
        this.renderJsTagAd(data);

        return;
      }

      /**
       * Local / native ads
       */
      switch (data.adFormat) {
        case "native":
        case "native_product":
          this.renderNativeAd(data);
          break;

        case "single_banner":
          this.renderSingleBannerAd(data);
          break;

        case "product_carousel":
          this.renderProductCarouselAd(data);
          break;

        default:
          this.renderError(`Unsupported ad format: ${data.adFormat || "unknown"}`);
          break;
      }

      this.bindImageFallbacks();
    }

    /**
     * ------------------------------------------------------------
     * COMMON HEADER
     * ------------------------------------------------------------
     */

    renderAdHeader(label) {
      return `
        <div class="ad-widget-header">
          <span>Ad</span>
          ${this.escapeHtml(label || "Sponsored Content")}
        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * ADVERTISER FOOTER
     * ------------------------------------------------------------
     */

    renderAdvertiserFooter(advertiser, adId) {
      if (!advertiser) {
        return "";
      }

      const logoUrl = this.escapeAttribute(advertiser.logoUrl || "");

      const title = this.escapeHtml(
        advertiser.title || advertiser.name || "Sponsored",
      );

      const name = this.escapeHtml(advertiser.name || "");

      return `
        <div
          class="ad-widget-footer"
          data-item-id="${this.escapeAttribute(adId || "")}"
        >
          ${
            logoUrl
              ? `
            <img
              src="${logoUrl}"
              alt=""
              class="ad-widget-logo"
              loading="lazy"
            />
          `
              : ""
          }

          <div>
            <h3 class="ad-widget-brand-title">
              ${title}
            </h3>

            <p class="ad-widget-brand-subtitle">
              ${name}
            </p>
          </div>
        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * PRODUCT CAROUSEL
     * ------------------------------------------------------------
     */

    renderProductCarouselAd(data) {
      const adItems = Array.isArray(data.adItems) ? data.adItems : [];

      // Limit to maximum 4 items for carousel display
      const displayItems = adItems.slice(0, 5);

      const productsHtml = displayItems
        .map((product) => {
          const isHighlighted = Boolean(product.highlightText);

          const badgeHtml = product.discount
            ? `
              <div class="ad-widget-badge">
                ${this.escapeHtml(product.discount)}
              </div>
            `
            : "";

          const highlightHtml = isHighlighted
            ? `
              <div class="highlight-banner">
                ${this.escapeHtml(product.highlightText)}
              </div>
            `
            : "";

          const priceHtml = product.price
            ? `
              <p class="ad-carousel-item-price">
                ${this.escapeHtml(product.price)}
              </p>
            `
            : "";

          const productName = this.escapeHtml(product.name || "");

          const productImage = this.escapeAttribute(product.imageUrl || "");

          const destination = this.escapeAttribute(
            product.destination?.url || product.landingPageUrl || "#",
          );

          const itemId = this.escapeAttribute(product.id || "");

          return `
              <a
                href="${destination}"
                target="_blank"
                rel="noopener noreferrer"
                class="ad-widget-item ${isHighlighted ? "highlighted" : ""}"
                data-track-type="product_click"
                data-item-id="${itemId}"
              >
                ${badgeHtml}

                ${
                  productImage
                    ? `
                  <img
                    src="${productImage}"
                    alt="${productName || "Product image"}"
                    loading="lazy"
                  />
                `
                    : ""
                }

                ${highlightHtml}

                <div class="ad-carousel-item-info">
                  <p class="ad-carousel-item-name">
                    ${productName}
                  </p>

                  ${priceHtml}
                </div>
              </a>
            `;
        })
        .join("");

      const adCount = Math.min(displayItems.length || 1, 4);

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">

          ${this.renderAdHeader(
            data.content?.headline ||
              data.advertiser?.description ||
              "Sponsored Content",
          )}

          <div
            class="ad-widget-grid"
            style="--leo-ad-columns: ${adCount};"
          >
            ${productsHtml}
          </div>

          ${this.renderAdvertiserFooter(data.advertiser, data.adId)}

        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * SINGLE BANNER
     * ------------------------------------------------------------
     */

    renderSingleBannerAd(data) {
      const creative = data.creative || {};

      const badgeHtml = creative.badge?.text
        ? `
          <div class="ad-widget-badge">
            ${this.escapeHtml(creative.badge.text)}
          </div>
        `
        : "";

      const destination = this.escapeAttribute(
        data.destination?.url || creative.landingPageUrl || "#",
      );

      const imageUrl = this.escapeAttribute(creative.imageUrl || "");

      const headline = this.escapeHtml(creative.headline || "");

      const subheadline = this.escapeHtml(creative.subheadline || "");

      const cta = this.escapeHtml(creative.cta || "");

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">

          ${this.renderAdHeader(
            data.advertiser?.description || "Sponsored Content",
          )}

          <a
            href="${destination}"
            target="_blank"
            rel="noopener noreferrer"
            class="ad-single-banner"
            data-track-type="banner_click"
            data-item-id="${this.escapeAttribute(data.adId || "")}"
          >

            <div class="ad-single-banner-media">

              ${badgeHtml}

              ${
                imageUrl
                  ? `
                <img
                  src="${imageUrl}"
                  alt="${headline || "Ad banner"}"
                  loading="lazy"
                />
              `
                  : ""
              }

            </div>

            <div class="ad-single-banner-copy">

              <h3 class="ad-single-banner-headline">
                ${headline}
              </h3>

              <p class="ad-single-banner-subheadline">
                ${subheadline}
              </p>

              ${
                cta
                  ? `
                <span class="ad-single-banner-cta">
                  ${cta}
                </span>
              `
                  : ""
              }

            </div>

          </a>

          ${this.renderAdvertiserFooter(data.advertiser, data.adId)}

        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * NATIVE AD
     * ------------------------------------------------------------
     */

    renderNativeAd(data) {
      const content = data.content || {};

      const destination = this.escapeAttribute(
        data.destination?.url || content.landingPageUrl || "#",
      );

      const imageUrl = this.escapeAttribute(content.imageUrl || "");

      const headline = this.escapeHtml(content.headline || "");

      const body = this.escapeHtml(content.body || "");

      const cta = this.escapeHtml(content.cta || "");

      const label = this.escapeHtml(content.label || "Sponsored");

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">

          ${this.renderAdHeader(label)}

          <a
            href="${destination}"
            target="_blank"
            rel="noopener noreferrer"
            class="ad-native-card"
            data-track-type="native_click"
            data-item-id="${this.escapeAttribute(data.adId || "")}"
          >

            ${
              imageUrl
                ? `
              <div class="ad-native-media">
                <img
                  src="${imageUrl}"
                  alt="${headline || "Sponsored content"}"
                  loading="lazy"
                />
              </div>
            `
                : ""
            }

            <div class="ad-native-body">

              <h3 class="ad-native-headline">
                ${headline}
              </h3>

              <p class="ad-native-text">
                ${body}
              </p>

              ${
                cta
                  ? `
                <span class="ad-native-cta">
                  ${cta} →
                </span>
              `
                  : ""
              }

            </div>

          </a>

          ${this.renderAdvertiserFooter(data.advertiser, data.adId)}

        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * JS TAG RENDERER
     * ------------------------------------------------------------
     *
     * Expected JSON:
     *
     * "rendering": {
     *   "type": "js_tag",
     *   "loader": {
     *     "src": "...",
     *     "async": true
     *   },
     *   "config": {
     *      ...
     *   },
     *   "container": {
     *     "id": "...",
     *     "className": "..."
     *   }
     * }
     */

    renderJsTagAd(data) {
      const rendering = data?.rendering || {};

      const loader = rendering.loader || {};

      const config = rendering.config || {};

      const containerConfig = rendering.container || {};

      const containerId =
        containerConfig.id ||
        `leo-ad-slot-${String(data.adId || "unknown").replace(
          /[^a-zA-Z0-9_-]/g,
          "-",
        )}`;

      const className = containerConfig.className || "ad-slot external-ad-slot";

      const provider = data?.source?.provider || "external";

      const network = data?.source?.network || "";

      this.container.innerHTML = `
        <div
          class="ad-widget-wrapper leo-external-ad-wrapper"
        >

          ${this.renderAdHeader(
            provider === "google_ads"
              ? "Google Ad"
              : provider === "affiliate"
                ? "Affiliate"
                : "Advertisement",
          )}

          <div
            id="${this.escapeAttribute(containerId)}"
            class="${this.escapeAttribute(className)}"
            data-ad-provider="${this.escapeAttribute(provider)}"
            data-ad-network="${this.escapeAttribute(network)}"
            data-ad-id="${this.escapeAttribute(data.adId || "")}"
          ></div>

        </div>
      `;

      const slot = jsdoc.getElementById(containerId);

      if (!slot) {
        throw new Error(
          `Unable to create external ad container: ${containerId}`,
        );
      }

      /**
       * Keep the complete ad information available
       * to external rendering adapters.
       */
      slot.__leoAd = {
        data,
        config,
      };

      if (this.container.getAttribute("data-ad-preview") === "true") {
        this.renderExternalPreview(slot, data);

        return;
      }

      if (!loader.src) {
        this.renderExternalFallback(slot, data, "Missing JS loader URL");

        return;
      }

      this.loadExternalScript(loader.src, Boolean(loader.async))
        .then(() => {
          this.initializeExternalAd(slot, data);
        })
        .catch((error) => {
          console.warn("External ad script failed to load", error);

          this.renderExternalFallback(
            slot,
            data,
            "Unable to load advertisement",
          );
        });
    }

    renderExternalPreview(slot, data) {
      const provider = data?.source?.provider || "external";

      const network = data?.source?.network || "unconfigured";

      const sizes = data?.rendering?.config?.sizes;

      const format = Array.isArray(sizes)
        ? sizes.map((size) => size.join("x")).join(", ")
        : data?.rendering?.config?.format || data?.adFormat || "JS tag";

      slot.innerHTML = `
        <div class="leo-ad-preview">
          <strong>${this.escapeHtml(network)}</strong>
          <span>${this.escapeHtml(provider)} · ${this.escapeHtml(format)}</span>
          <small>External script preview</small>
        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * EXTERNAL JS LOADER
     * ------------------------------------------------------------
     */

    loadExternalScript(src, async = true) {
      return new Promise((resolve, reject) => {
        if (!src) {
          reject(new Error("External ad script URL is required"));

          return;
        }

        const existingScripts = jsdoc.querySelectorAll(
          "script[data-leo-ad-script]",
        );

        let existing = null;

        for (const script of existingScripts) {
          if (script.dataset.leoAdScript === src) {
            existing = script;
            break;
          }
        }

        if (existing) {
          if (existing.dataset.loaded === "true") {
            resolve();

            return;
          }

          existing.addEventListener("load", () => resolve(), {
            once: true,
          });

          existing.addEventListener(
            "error",
            () => reject(new Error(`Failed to load ${src}`)),
            {
              once: true,
            },
          );

          return;
        }

        const script = jsdoc.createElement("script");

        script.src = src;
        script.async = async;

        script.dataset.leoAdScript = src;

        script.addEventListener(
          "load",
          () => {
            script.dataset.loaded = "true";

            resolve();
          },
          {
            once: true,
          },
        );

        script.addEventListener(
          "error",
          () => {
            reject(new Error(`Failed to load external ad script: ${src}`));
          },
          {
            once: true,
          },
        );

        jsdoc.head.appendChild(script);
      });
    }

    /**
     * ------------------------------------------------------------
     * EXTERNAL AD INITIALIZATION
     * ------------------------------------------------------------
     */

    initializeExternalAd(slot, data) {
      const provider = String(data?.source?.provider || "").toLowerCase();

      const network = String(data?.source?.network || "").toLowerCase();

      const config = data?.rendering?.config || {};

      /**
       * Google Ad Manager / GPT
       */
      if (
        provider === "google_ads" &&
        (network === "google_ad_manager" || network === "gpt")
      ) {
        this.initializeGooglePublisherTag(slot, data);

        return;
      }

      /**
       * Explicit custom adapter:
       *
       * "adapter": "MyAffiliateRenderer"
       */
      const adapterName = data?.rendering?.adapter;

      if (adapterName && typeof window[adapterName] === "function") {
        window[adapterName](slot, data, config);

        return;
      }

      /**
       * Generic provider adapter:
       *
       * window.LeoAdNetworks.google_ads
       * window.LeoAdNetworks.lazada
       * window.LeoAdNetworks.shopee
       */
      const networkAdapter = window.LeoAdNetworks?.[provider];

      if (networkAdapter && typeof networkAdapter.render === "function") {
        networkAdapter.render(slot, data, config);

        return;
      }

      /**
       * Some external scripts render automatically
       * after being loaded.
       */
      if (slot.childNodes.length === 0) {
        this.renderExternalFallback(
          slot,
          data,
          "External ad loaded; no renderer adapter was registered",
        );
      }
    }

    /**
     * ------------------------------------------------------------
     * GOOGLE PUBLISHER TAG
     * ------------------------------------------------------------
     */

    initializeGooglePublisherTag(slot, data) {
      const config = data?.rendering?.config || {};

      const adUnitPath = config.adUnitPath;

      const sizes = Array.isArray(config.sizes) ? config.sizes : [];

      if (!adUnitPath) {
        this.renderExternalFallback(slot, data, "Missing Google ad unit path");

        return;
      }

      window.googletag = window.googletag || {
        cmd: [],
      };

      window.googletag.cmd.push(() => {
        try {
          /**
           * Remove an existing GPT slot with the same ID
           * so repeated initialization doesn't duplicate it.
           */
          const existingSlot = window.googletag
            .pubads()
            .getSlots()
            .find((item) => item.getSlotElementId() === slot.id);

          if (existingSlot) {
            window.googletag.destroySlots([existingSlot]);
          }

          const gptSlot = window.googletag.defineSlot(
            adUnitPath,
            sizes,
            slot.id,
          );

          if (!gptSlot) {
            this.renderExternalFallback(
              slot,
              data,
              "Google could not define the ad slot",
            );

            return;
          }

          /**
           * Custom targeting
           */
          if (config.targeting && typeof gptSlot.setTargeting === "function") {
            Object.entries(config.targeting).forEach(([key, value]) => {
              if (value !== undefined && value !== null) {
                gptSlot.setTargeting(
                  key,
                  Array.isArray(value) ? value : String(value),
                );
              }
            });
          }

          gptSlot.addService(window.googletag.pubads());

          /**
           * Initialize GPT once globally.
           */
          if (!window.__leoGPTInitialized) {
            window.googletag.pubads().enableSingleRequest();

            window.googletag.enableServices();

            window.__leoGPTInitialized = true;
          }

          window.googletag.display(slot.id);
        } catch (error) {
          console.error("Google Publisher Tag initialization failed", error);

          this.renderExternalFallback(
            slot,
            data,
            "Google ad initialization failed",
          );
        }
      });
    }

    /**
     * ------------------------------------------------------------
     * EXTERNAL FALLBACK
     * ------------------------------------------------------------
     */

    renderExternalFallback(slot, data, message) {
      const fallbackUrl = data?.destination?.url || data?.destination?.finalUrl;

      slot.innerHTML = `
        <div class="leo-ad-fallback">

          <span>
            ${this.escapeHtml(message || "Advertisement unavailable")}
          </span>

          ${
            fallbackUrl
              ? `
            <a
              href="${this.escapeAttribute(fallbackUrl)}"
              target="_blank"
              rel="noopener noreferrer"
              data-track-type="external_fallback_click"
              data-item-id="${this.escapeAttribute(data.adId || "")}"
            >
              View offer
            </a>
          `
              : ""
          }

        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * ERROR
     * ------------------------------------------------------------
     */

    renderError(message) {
      this.container.innerHTML = `
        <div class="ad-widget-wrapper">

          <div class="ad-widget-header">
            <span>Ad</span>
            ${this.escapeHtml(message || "Advertisement unavailable")}
          </div>

        </div>
      `;
    }

    /**
     * ------------------------------------------------------------
     * IMAGE FALLBACKS
     * ------------------------------------------------------------
     */

    bindImageFallbacks() {
      const images = this.container.querySelectorAll("img");

      images.forEach((img) => {
        img.addEventListener(
          "error",
          () => {
            const item = img.closest(
              ".ad-widget-item, .ad-single-banner, .ad-native-card",
            );

            if (item) {
              item.classList.add("is-broken");
            }

            img.style.display = "none";
          },
          {
            once: true,
          },
        );
      });
    }

    /**
     * ------------------------------------------------------------
     * IMPRESSION TRACKING
     * ------------------------------------------------------------
     *
     * Fires when:
     * - ad is >= 50% visible
     * - for at least 1 second
     * - only once
     */

    setupImpressionTracking() {
      if (!("IntersectionObserver" in window)) {
        this.trackImpression();

        return;
      }

      this.hasFiredImpression = false;

      this.impressionObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            this.inView = entry.isIntersecting;

            if (entry.isIntersecting) {
              this.startDwell();

              if (!this.hasFiredImpression && !this.impressionTimer) {
                this.impressionTimer = setTimeout(() => {
                  this.impressionTimer = null;
                  this.hasFiredImpression = true;
                  this.trackImpression();
                }, 1000);
              }
            } else {
              if (this.impressionTimer) {
                clearTimeout(this.impressionTimer);

                this.impressionTimer = null;
              }

              // Leaving view ends the dwell measurement.
              this.pauseDwell();
            }
          });
        },
        {
          threshold: 0.5,
        },
      );

      this.impressionObserver.observe(this.container);
    }

    /**
     * Dwell time is the total time the ad was >= 50% visible while the tab was
     * visible. One `view` is sent per ad, the first time the ad leaves view (or
     * the tab is hidden, or the page is hidden) with at least 1000 ms on screen.
     */
    startDwell() {
      if (
        this.inView &&
        this.visibleSince === null &&
        jsdoc.visibilityState !== "hidden"
      ) {
        this.visibleSince = Date.now();
      }
    }

    pauseDwell() {
      if (this.visibleSince !== null) {
        this.dwellMs += Date.now() - this.visibleSince;

        this.visibleSince = null;
      }

      if (!this.hasSentView && this.dwellMs >= VIEW_MIN_DWELL_MS) {
        this.hasSentView = true;

        this.queueEvent("view", { dwell_ms: Math.round(this.dwellMs) });
      }
    }

    trackImpression() {
      this.queueEvent("impression");
    }

    /**
     * ------------------------------------------------------------
     * CLICK TRACKING
     * ------------------------------------------------------------
     */

    setupClickTracking() {
      if (this.container.dataset.clickBound === "true") {
        return;
      }

      this.container.addEventListener("click", (event) => {
        const targetElement = event.target.closest("a[data-track-type]");

        if (!targetElement) {
          return;
        }

        // The destination link is left alone; this only reports the click.
        this.queueEvent("click", {
          click_id: this.adData?.tracking?.clickId,
        });
      });

      this.container.dataset.clickBound = "true";
    }

    /**
     * ------------------------------------------------------------
     * DISMISS TRACKING
     * ------------------------------------------------------------
     *
     * The widget adds a small close button to the ad (`data-ad-dismiss`). Any
     * element inside the container with that attribute reports a dismiss event
     * when clicked, then the ad is hidden.
     */

    setupDismissTracking() {
      if (this.container.dataset.dismissBound === "true") {
        return;
      }

      this.addDismissControl();

      this.container.addEventListener("click", (event) => {
        if (event.target.closest("[data-ad-dismiss]")) {
          this.dismiss();
        }
      });

      this.container.dataset.dismissBound = "true";
    }

    addDismissControl() {
      if (!jsdoc.createElement || this.container.querySelector?.("[data-ad-dismiss]")) {
        return;
      }

      const button = jsdoc.createElement("button");

      button.type = "button";
      button.className = "leo-ad-dismiss";
      button.setAttribute("data-ad-dismiss", "");
      button.setAttribute("aria-label", "Dismiss this ad");
      button.textContent = "\u00d7";

      this.container.appendChild(button);
    }

    dismiss() {
      this.queueEvent("dismiss");

      this.pauseDwell();

      this.container.setAttribute("hidden", "");
    }

    /**
     * ------------------------------------------------------------
     * EVENT QUEUE
     * ------------------------------------------------------------
     */

    queueEvent(eventType, extra = {}) {
      // Nothing is sent without consent, or when the server gave no request id.
      if (this.getConsent() === "denied") {
        return;
      }

      if (!this.adData?.adId || !this.requestId) {
        return;
      }

      const event = {
        event_id: this.generateUUID(),
        event_type: eventType,
        request_id: this.requestId,
        ad_key: String(this.adData.adId),
        event_time: new Date().toISOString(),
        page_url: jsdoc.location?.href,
      };

      Object.entries(extra).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          event[key] = value;
        }
      });

      transport.add({
        tenant: this.getOption("tenant", "demo"),
        site: this.getOption("site", jsdoc.location?.hostname || ""),
        leovid: this.getOrCreateVisitorId(),
        sessionId: this.getSessionId(),
        event,
      });
    }

    /**
     * ------------------------------------------------------------
     * SECURITY HELPERS
     * ------------------------------------------------------------
     */

    escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    escapeAttribute(value) {
      return this.escapeHtml(value);
    }
  }

  // ------------------------------------------------------------
  // GLOBAL STYLESHEET INJECTION
  // ------------------------------------------------------------
  const injectStyles = (cssUrl) => {
    if (jsdoc.getElementById(STYLE_ID)) {
      return;
    }

    const resolvedCssUrl = cssUrl || `${DEFAULT_CDN}leo.ads.css`;

    fetch(resolvedCssUrl, {
      cache: "no-store",
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Failed to load ad stylesheet: ${response.status}`);
        }

        return response.text();
      })
      .then((cssText) => {
        if (jsdoc.getElementById(STYLE_ID)) {
          return;
        }

        const style = jsdoc.createElement("style");

        style.type = "text/css";
        style.id = STYLE_ID;
        style.textContent = cssText;

        jsdoc.head.appendChild(style);
      })
      .catch((error) => {
        console.warn("Unable to load ad stylesheet", error);
      });
  };

  // Test hook: only populated when a test sets window.__LEO_ADS_TEST__ = {}
  // before loading this file (tests/widget/ads.loader.test.js).
  if (window.__LEO_ADS_TEST__) {
    Object.assign(window.__LEO_ADS_TEST__, { LeoAdWidget, EventTransport, transport, widgets });
  }

  /**
   * ------------------------------------------------------------
   * GLOBAL INITIALIZER
   * ------------------------------------------------------------
   */

  const initializeAdWidgets = () => {
    const nodes = Array.from(jsdoc.getElementsByClassName("leo-ad-container"));

    if (nodes.length > 0) {
      const firstNode = nodes[0];

      injectStyles(
        firstNode.getAttribute("data-ad-css-url") || `${DEFAULT_CDN}leo.ads.css`,
      );
    }

    nodes.forEach((node) => {
      if (node.dataset.adInitialized === "true") {
        return;
      }

      node.dataset.adInitialized = "true";

      new LeoAdWidget(node);
    });
  };

  if (jsdoc.readyState === "loading") {
    jsdoc.addEventListener("DOMContentLoaded", initializeAdWidgets);
  } else {
    initializeAdWidgets();
  }
})();