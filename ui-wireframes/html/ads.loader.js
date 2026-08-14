/**
 * Ad Banner Rendering & Tracking Engine
 * Implements a lightweight OOP-style loader with light/dark theme support.
 */
(() => {
  const STYLE_ID = "leo-ad-widget-styles";

  class LeoAdWidget {
    constructor(container) {
      this.container = container;
      this.adData = {};
      this.theme = this.resolveTheme();
      this.container.dataset.adTheme = this.theme;
      this.container.classList.add(`leo-ad-theme-${this.theme}`);

      this.setContainerStyles();
      this.injectStyles();
      this.init();
    }

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

    injectStyles() {
      if (document.getElementById(STYLE_ID)) {
        return;
      }

      const adsCDN = 'http://localhost:5500/ui-wireframes/html/';
      const cssUrl = this.container.getAttribute("data-ad-css-url") || `${adsCDN}leo.ads.css`;

      fetch(cssUrl, { cache: "no-store" })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`Failed to load ad stylesheet: ${response.status}`);
          }

          return response.text();
        })
        .then((cssText) => {
          if (document.getElementById(STYLE_ID)) {
            return;
          }

          const style = document.createElement("style");
          style.type = "text/css";
          style.id = STYLE_ID;
          style.textContent = cssText;
          document.head.appendChild(style);
        })
        .catch((error) => {
          console.warn("Unable to load ad stylesheet", error);
        });
    }

    async init() {
      const placementId = this.container.getAttribute("data-ad-placement");
      if (!placementId) {
        return;
      }

      await this.loadAdData(placementId);
    }

    async getAdDataUrl(placementId) {
      const dataAdFormat = this.container.getAttribute("data-ad-format") || "";
      const dataAdTheme =
        this.container.getAttribute("data-ad-theme") || this.theme;

      const adsCDN = 'http://localhost:5500/ui-wireframes/html/';
      const customEndpoint =
        this.container.getAttribute("data-ad-data-url") || `${adsCDN}ads.data.json`;

      const url = new URL(customEndpoint, window.location.href);
      const params = url.searchParams;

      if (!placementId) {
        throw new Error(
          "Ad placement ID is required to build the ad data URL.",
        );
      }

      params.set("leopmid", placementId);
      if (dataAdFormat) params.set("format", dataAdFormat);
      if (dataAdTheme) params.set("theme", dataAdTheme);

      const screenWidth = window.screen?.width ?? 0;
      const screenHeight = window.screen?.height ?? 0;
      if (screenWidth || screenHeight) {
        params.set("screen", `${screenWidth}x${screenHeight}`);
      }

      params.set("leovid", this.getOrCreateVisitorId());
      params.set("url", window.location.href);
      params.set("referrer", document.referrer || "");
      params.set("timestamp", new Date().toISOString());
      params.set("leotpid", await this.getTouchpointId());

      return url.toString();
    }

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
      if (crypto.randomUUID) {
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

    async loadAdData(placementId) {
      if (!this.container) return;

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
        const response = await fetch(adDataUrl, { cache: "no-store" });

        if (!response.ok) {
          throw new Error(`Failed to fetch ad data: ${response.status}`);
        }

        const payload = await response.json();
        this.adData = this.selectAdForPlacement(payload, placementId);

        if (!this.adData) {
          throw new Error(`No ad data found for placement: ${placementId}`);
        }

        this.applyBannerDimensions(this.adData.placement);
        this.renderAd();
        this.setupImpressionTracking();
        this.setupClickTracking();
      } catch (error) {
        console.error("Unable to load ad data", error);
        this.container.innerHTML = `
          <div class="ad-widget-wrapper">
            <div class="ad-widget-header">
              <span>Ad</span> Unable to load ad data
            </div>
          </div>
        `;
      }
    }

    selectAdForPlacement(payload, placementId) {
      const ads = Array.isArray(payload?.ads) ? payload.ads : [payload];
      return (
        ads.find((ad) => String(ad?.adPlacementId) === String(placementId)) ||
        ads[0] ||
        null
      );
    }

    applyBannerDimensions(placement) {
      const width = Number(placement?.width) || 3;
      const height = Number(placement?.height) || 2;

      this.container.style.setProperty("--leo-ad-banner-w", width);
      this.container.style.setProperty("--leo-ad-banner-h", height || width);
    }

    renderSkeleton() {
      const placeholders = Array.from(
        { length: 4 },
        () => '<div class="ad-widget-skeleton"></div>',
      ).join("");

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">
          <div class="ad-widget-header">
            <span>Ad</span> Loading\u2026
          </div>
          <div class="ad-widget-grid" style="--leo-ad-columns: 4;">
            ${placeholders}
          </div>
        </div>
      `;
    }

    renderAd() {
      const data = this.adData;

      switch (data.adFormat) {
        case "native":
          this.renderNativeAd(data);
          break;
        case "single_banner":
          this.renderSingleBannerAd(data);
          break;
        case "product_carousel":
        default:
          this.renderProductCarouselAd(data);
          break;
      }

      this.bindImageFallbacks();
    }

    renderAdHeader(label) {
      return `
        <div class="ad-widget-header">
          <span>Ad</span> ${label || "Sponsored Content"}
        </div>
      `;
    }

    renderAdvertiserFooter(advertiser, adId) {
      return `
        <a
          href="${advertiser.landingPageUrl}"
          target="_blank"
          rel="noopener noreferrer"
          class="ad-widget-footer"
          data-track-type="advertiser_click"
          data-item-id="${adId}"
        >
          <img src="${advertiser.logoUrl}" alt="Logo" class="ad-widget-logo" />
          <div>
            <h3 class="ad-widget-brand-title">${advertiser.title}</h3>
            <p class="ad-widget-brand-subtitle">${advertiser.name}</p>
          </div>
        </a>
      `;
    }

    renderProductCarouselAd(data) {
      const adItems = data.adItems || [];
      const productsHtml = adItems
        .map((product) => {
          const isHighlighted = Boolean(product.highlightText);
          const badgeHtml = product.discount
            ? `<div class="ad-widget-badge">${product.discount}</div>`
            : "";
          const highlightHtml = isHighlighted
            ? `<div class="highlight-banner">${product.highlightText}</div>`
            : "";
          const priceHtml = product.price
            ? `<p class="ad-carousel-item-price">${product.price}</p>`
            : "";
          const infoHtml = `
              <div class="ad-carousel-item-info">
                <p class="ad-carousel-item-name">${product.name || ""}</p>
                ${priceHtml}
              </div>
            `;

          return `
            <a href="${product.landingPageUrl}" target="_blank" rel="noopener noreferrer"
              class="ad-widget-item ${isHighlighted ? "highlighted" : ""}"
              data-track-type="product_click" data-item-id="${product.id}">
              ${badgeHtml}
              <img src="${product.imageUrl}" alt="${product.name || "Product image"}" loading="lazy" />
              ${highlightHtml}
              ${infoHtml}
            </a>
          `;
        })
        .join("");

      const adCount = Math.min(adItems.length || 1, 4);

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">
          ${this.renderAdHeader(data.content?.headline || data.advertiser.description)}
          <div class="ad-widget-grid" style="--leo-ad-columns: ${adCount};">
            ${productsHtml}
          </div>
          ${this.renderAdvertiserFooter(data.advertiser, data.adId)}
        </div>
      `;
    }

    renderSingleBannerAd(data) {
      const creative = data.creative || {};
      const badgeHtml = creative.badge?.text
        ? `<div class="ad-widget-badge">${creative.badge.text}</div>`
        : "";

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">
          ${this.renderAdHeader(data.advertiser.description)}
          <a href="${creative.landingPageUrl}" target="_blank" rel="noopener noreferrer"
            class="ad-single-banner" data-track-type="banner_click" data-item-id="${data.adId}">
            <div class="ad-single-banner-media">
              ${badgeHtml}
              <img src="${creative.imageUrl}" alt="${creative.headline || "Ad banner"}" loading="lazy" />
            </div>
            <div class="ad-single-banner-copy">
              <h3 class="ad-single-banner-headline">${creative.headline || ""}</h3>
              <p class="ad-single-banner-subheadline">${creative.subheadline || ""}</p>
              ${creative.cta ? `<span class="ad-single-banner-cta">${creative.cta}</span>` : ""}
            </div>
          </a>
          ${this.renderAdvertiserFooter(data.advertiser, data.adId)}
        </div>
      `;
    }

    renderNativeAd(data) {
      const content = data.content || {};

      this.container.innerHTML = `
        <div class="ad-widget-wrapper">
          ${this.renderAdHeader(content.label || "Sponsored")}
          <a href="${content.landingPageUrl}" target="_blank" rel="noopener noreferrer"
            class="ad-native-card" data-track-type="native_click" data-item-id="${data.adId}">
            <div class="ad-native-media">
              <img src="${content.imageUrl}" alt="${content.headline || "Sponsored content"}" loading="lazy" />
            </div>
            <div class="ad-native-body">
              <h3 class="ad-native-headline">${content.headline || ""}</h3>
              <p class="ad-native-text">${content.body || ""}</p>
              ${content.cta ? `<span class="ad-native-cta">${content.cta} \u2192</span>` : ""}
            </div>
          </a>
          ${this.renderAdvertiserFooter(data.advertiser, data.adId)}
        </div>
      `;
    }


    bindImageFallbacks() {
      const images = this.container.querySelectorAll(".ad-widget-item img");
      images.forEach((img) => {
        img.addEventListener(
          "error",
          () => {
            img.closest(".ad-widget-item")?.classList.add("is-broken");
          },
          { once: true },
        );
      });
    }

    setupImpressionTracking() {
      let viewTimer = null;
      let hasFiredImpression = false;

      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (hasFiredImpression) return;

            if (entry.isIntersecting) {
              viewTimer = setTimeout(() => {
                this.trackEvent(this.adData.tracking.impressionUrl, {
                  adId: this.adData.adId,
                  event: "impression",
                  timestamp: new Date().toISOString(),
                });
                hasFiredImpression = true;
                observer.disconnect();
              }, 1000);
            } else if (viewTimer) {
              clearTimeout(viewTimer);
            }
          });
        },
        { threshold: 0.5 },
      );

      observer.observe(this.container);
    }

    setupClickTracking() {
      if (this.container.dataset.clickBound === "true") {
        return;
      }

      this.container.addEventListener("click", (event) => {
        const targetElement = event.target.closest("a[data-track-type]");
        if (!targetElement) {
          return;
        }

        const trackType = targetElement.getAttribute("data-track-type");
        const itemId = targetElement.getAttribute("data-item-id");
        const destinationUrl = targetElement.getAttribute("href");

        this.trackEvent(this.adData.tracking.clickUrl, {
          adId: this.adData.adId,
          event: trackType,
          itemId,
          destination: destinationUrl,
          timestamp: new Date().toISOString(),
        });
      });

      this.container.dataset.clickBound = "true";
    }

    trackEvent(endpoint, payload) {
      if (!endpoint) {
        return;
      }

      const blob = new Blob([JSON.stringify(payload)], {
        type: "application/json",
      });

      if (navigator.sendBeacon) {
        navigator.sendBeacon(endpoint, blob);
      } else {
        fetch(endpoint, {
          method: "POST",
          body: blob,
          keepalive: true,
        }).catch(() => {});
      }
    }
  }

  const initializeAdWidgets = () => {
    const nodes = document.getElementsByClassName("leo-ad-container");

    Array.from(nodes).forEach((node) => {
      if (node.dataset.adInitialized === "true") {
        return;
      }

      node.dataset.adInitialized = "true";
      new LeoAdWidget(node);
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeAdWidgets);
  } else {
    initializeAdWidgets();
  }
})();
