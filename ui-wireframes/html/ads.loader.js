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

      const style = document.createElement("style");
      style.type = "text/css";
      style.id = STYLE_ID;
      style.textContent = `
        .leo-ad-theme-light,
        .leo-ad-theme-dark {
          --leo-ad-bg: #ffffff;
          --leo-ad-surface: #f5f5f5;
          --leo-ad-text: #222222;
          --leo-ad-subtext: #888888;
          --leo-ad-border: #eaeaea;
          --leo-ad-accent: #1565c0;
          --leo-ad-accent-soft: #cae9f5;
          --leo-ad-highlight: #5fa8d3;
          --leo-ad-label: #f2c94c;
          --leo-ad-badge-text: #1565c0;
          --leo-ad-badge-bg: #ffffff;
          --leo-ad-badge-border: #1565c0;
        }

        .leo-ad-theme-light {
          --leo-ad-bg: #ffffff;
          --leo-ad-surface: #f5f5f5;
          --leo-ad-text: #222222;
          --leo-ad-subtext: #888888;
          --leo-ad-border: #eaeaea;
          --leo-ad-accent: #1565c0;
          --leo-ad-accent-soft: #cae9f5;
          --leo-ad-highlight: #5fa8d3;
          --leo-ad-label: #f2c94c;
          --leo-ad-badge-text: #1565c0;
          --leo-ad-badge-bg: #ffffff;
          --leo-ad-badge-border: #1565c0;
        }

        .leo-ad-theme-dark {
          --leo-ad-bg: #111827;
          --leo-ad-surface: #1f2937;
          --leo-ad-text: #f3f4f6;
          --leo-ad-subtext: #cbd5e1;
          --leo-ad-border: #374151;
          --leo-ad-accent: #7dd3fc;
          --leo-ad-accent-soft: rgba(125, 211, 252, 0.14);
          --leo-ad-highlight: #67e8f9;
          --leo-ad-label: #fbbf24;
          --leo-ad-badge-text: #dbeafe;
          --leo-ad-badge-bg: rgba(17, 24, 39, 0.9);
          --leo-ad-badge-border: #7dd3fc;
        }

        .ad-widget-wrapper {
          font-family: Arial, sans-serif;
          width: var(--leo-ad-width, 100%);
          max-width: var(--leo-ad-max-width, 100%);
          min-width: var(--leo-ad-min-width, 280px);
          margin: 0 auto;
          padding: clamp(10px, 2vw, 16px);
          background: var(--leo-ad-bg);
          color: var(--leo-ad-text);
          box-sizing: border-box;
          border-radius: 14px;
          border: 1px solid var(--leo-ad-border);
          box-shadow: 0 10px 25px rgba(15, 23, 42, 0.06);
        }

        .ad-widget-header {
          text-align: center;
          font-size: clamp(11px, 1.5vw, 12px);
          color: var(--leo-ad-subtext);
          margin-bottom: 12px;
          letter-spacing: 0.02em;
        }

        .ad-widget-header span {
          border: 1px solid var(--leo-ad-label);
          padding: 1px 5px;
          border-radius: 3px;
          color: var(--leo-ad-label);
          font-size: 10px;
          margin-right: 4px;
          display: inline-block;
          font-weight: 700;
        }

        .ad-widget-grid {
            display: grid;
            grid-template-columns: repeat(
                var(--leo-ad-columns, 4),
                minmax(0, 1fr)
            );
            gap: clamp(8px, 1.5vw, 12px);
            width: 100%;
            max-width: 1320px;
            margin-inline: auto;
        }

        .ad-widget-item {
          position: relative;
          display: block;
          text-decoration: none;
          border-radius: 10px;
          overflow: hidden;
          background: var(--leo-ad-surface);
          border: 1px solid var(--leo-ad-border);
          transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
          cursor: pointer;
          min-height: 0;
        }

        .ad-widget-item:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 18px rgba(0, 0, 0, 0.12);
        }

        .ad-widget-item img {
          width: 100%;
          height: 100%;
          display: block;
          object-fit: cover;
          aspect-ratio: 3 / 4;
        }

        .ad-widget-item.highlighted {
          border: 2px solid var(--leo-ad-highlight);
          background: var(--leo-ad-bg);
        }

        .ad-widget-item.highlighted .highlight-banner {
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          background: var(--leo-ad-accent-soft);
          color: var(--leo-ad-accent);
          font-size: clamp(9px, 1.4vw, 11px);
          line-height: 1.2;
          font-weight: bold;
          text-align: center;
          padding: 8px 4px;
        }

        .ad-widget-badge {
          position: absolute;
          top: 6px;
          left: 6px;
          background: var(--leo-ad-badge-bg);
          color: var(--leo-ad-badge-text);
          border: 1px solid var(--leo-ad-badge-border);
          font-size: clamp(9px, 1.3vw, 11px);
          font-weight: bold;
          padding: 2px 6px;
          border-radius: 4px;
          z-index: 1;
        }

        .ad-widget-footer {
          display: flex;
          align-items: center;
          margin-top: 14px;
          text-decoration: none;
          cursor: pointer;
          color: var(--leo-ad-text);
          gap: 12px;
        }

        .ad-widget-logo {
          width: clamp(36px, 5vw, 44px);
          height: clamp(36px, 5vw, 44px);
          border-radius: 6px;
          flex-shrink: 0;
          object-fit: cover;
        }

        .ad-widget-brand-title {
          font-size: clamp(15px, 2vw, 18px);
          color: var(--leo-ad-text);
          margin: 0;
          font-weight: 600;
          line-height: 1.2;
        }

        .ad-widget-brand-subtitle {
          font-size: clamp(12px, 1.6vw, 14px);
          color: var(--leo-ad-subtext);
          margin: 2px 0 0;
          line-height: 1.35;
        }

        @media (max-width: 1200px) {
            .ad-widget-grid {
                grid-template-columns: repeat(4, minmax(0, 1fr));
            }
        }

        @media (max-width: 900px) {
            .ad-widget-grid {
                grid-template-columns: repeat(4, minmax(0, 1fr));
            }
        }

        @media (max-width: 700px) {
            .ad-widget-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        @media (max-width: 480px) {
          .ad-widget-wrapper {
            padding: 10px;
            border-radius: 10px;
          }

          .ad-widget-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
          }

          .ad-widget-footer {
            align-items: flex-start;
          }

          .ad-widget-brand-title {
            font-size: 15px;
          }

          .ad-widget-brand-subtitle {
            font-size: 12px;
          }
        }
      `;

      document.head.appendChild(style);
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
      const customEndpoint =
        this.container.getAttribute("data-ad-data-url") || "ads.data.json";

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

      try {
        const adDataUrl = await this.getAdDataUrl(placementId);
        const response = await fetch(adDataUrl, { cache: "no-store" });

        if (!response.ok) {
          throw new Error(`Failed to fetch ad data: ${response.status}`);
        }

        this.adData = await response.json();
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

    renderAd() {
      const data = this.adData;
      const adItems = data.adItems || [];
      const productsHtml = adItems
        .map((product) => {
          const isHighlighted = Boolean(product.isHighlighted);
          const badgeHtml =
            product.discount && !isHighlighted
              ? `<div class="ad-widget-badge">${product.discount}</div>`
              : "";
          const highlightHtml =
            isHighlighted && product.highlightText
              ? `<div class="highlight-banner">${product.highlightText}</div>`
              : "";

          return `
            <a href="${product.landingPageUrl}" target="_blank" rel="noopener noreferrer"
              class="ad-widget-item ${isHighlighted ? "highlighted" : ""}"
              data-track-type="product_click" data-item-id="${product.id}">
              ${badgeHtml}
              <img src="${product.imageUrl}" alt="Product image" loading="lazy" />
              ${highlightHtml}
            </a>
          `;
        })
        .join("");

      const adCount = Math.min(adItems.length, 4);

        this.container.innerHTML = `
        <div class="ad-widget-wrapper">
            <div class="ad-widget-header">
            <span>Ad</span> ${data.advertiser.description || "Sponsored Content"}
            </div>

            <div
            class="ad-widget-grid"
            style="--leo-ad-columns: ${adCount};"
            >
            ${productsHtml}
            </div>

            <a
            href="${data.advertiser.landingPageUrl}"
            target="_blank"
            rel="noopener noreferrer"
            class="ad-widget-footer"
            data-track-type="advertiser_click"
            data-item-id="${data.adId}"
            >
            <img
                src="${data.advertiser.logoUrl}"
                alt="Logo"
                class="ad-widget-logo"
            />

            <div>
                <h3 class="ad-widget-brand-title">${data.advertiser.title}</h3>
                <p class="ad-widget-brand-subtitle">${data.advertiser.name}</p>
            </div>
            </a>
        </div>
        `;

      this.bindImageFallbacks();
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
