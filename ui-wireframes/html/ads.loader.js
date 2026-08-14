/**
 * Ad Banner Rendering & Tracking Engine
 * Implements IIFE, MRC standard viewability, and beacon click tracking.
 */
(function () {
  // 1. Scoped CSS Injection
  const injectStyles = () => {
    const style = document.createElement("style");
    style.type = "text/css";
    style.id = "ad-widget-styles";
    style.innerHTML = `
            .ad-widget-wrapper { font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 12px; background: #fff; box-sizing: border-box; }
            .ad-widget-header { text-align: center; font-size: 12px; color: #999; margin-bottom: 10px; }
            .ad-widget-header span { border: 1px solid #f2c94c; padding: 1px 4px; border-radius: 2px; color: #f2c94c; font-size: 10px; margin-right: 4px;}
            .ad-widget-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
            .ad-widget-item { position: relative; display: block; text-decoration: none; border-radius: 8px; overflow: hidden; background: #f5f5f5; border: 1px solid #eaeaea; transition: transform 0.2s; cursor: pointer; }
            .ad-widget-item:hover { transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
            .ad-widget-item img { width: 100%; height: auto; display: block; object-fit: cover; aspect-ratio: 3/4; }
            .ad-widget-item.highlighted { border: 2px solid #5fa8d3; background: #fff; }
            .ad-widget-item.highlighted .highlight-banner { position: absolute; bottom: 0; left: 0; right: 0; background: #cae9f5; color: #1565c0; font-size: 11px; font-weight: bold; text-align: center; padding: 8px 4px; }
            .ad-widget-badge { position: absolute; top: 6px; left: 6px; background: #fff; color: #1565c0; border: 1px solid #1565c0; font-size: 11px; font-weight: bold; padding: 2px 6px; border-radius: 4px; }
            .ad-widget-footer { display: flex; align-items: center; margin-top: 15px; text-decoration: none; cursor: pointer; }
            .ad-widget-logo { width: 44px; height: 44px; border-radius: 4px; margin-right: 12px; }
            .ad-widget-brand-title { font-size: 18px; color: #222; margin: 0; font-weight: 500; }
            .ad-widget-brand-subtitle { font-size: 14px; color: #888; margin: 2px 0 0 0; }
            @media (max-width: 768px) { .ad-widget-grid { grid-template-columns: repeat(3, 1fr); } }
            @media (max-width: 480px) { .ad-widget-grid { grid-template-columns: repeat(2, 1fr); } }
        `;
    document.head.appendChild(style);
  };
  if (document.getElementById("ad-widget-styles") == null) {
    injectStyles();
  }

  let adData = {};

  async function getAdDataUrl(placementId, container) {
    const dataAdFormat = container?.getAttribute("data-ad-format") || "";
    const dataAdTheme = container?.getAttribute("data-ad-theme") || "";
    const customEndpoint =
      container?.getAttribute("data-ad-data-url") || "ads.data.json";

    const url = new URL(customEndpoint, window.location.href);
    const params = url.searchParams;

    if (!placementId) {
      throw new Error("Ad placement ID is required to build the ad data URL.");
    }

    params.set("leopmid", placementId);
    if (dataAdFormat) params.set("format", dataAdFormat);
    if (dataAdTheme) params.set("theme", dataAdTheme);

    const screenWidth = window.screen?.width ?? 0;
    const screenHeight = window.screen?.height ?? 0;
    if (screenWidth || screenHeight) {
      params.set("screen", `${screenWidth}x${screenHeight}`);
    }

    params.set("leovid", getOrCreateVisitorId());
    params.set("url", window.location.href);
    params.set("referrer", document.referrer || "");
    params.set("timestamp", new Date().toISOString());

    const leotpid = await getTouchpointId();
    params.set("leotpid", leotpid);

    return url.toString();
  }

  async function getTouchpointId() {
    const url = new URL(window.location.href);

    // Remove parameters that should NOT create a different touchpoint.
    // Adjust this list according to your tracking model.
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

    // Normalize URL
    const normalizedUrl = url.toString();

    return await sha256(normalizedUrl);
  }

  async function sha256(value) {
    const data = new TextEncoder().encode(value);

    const hashBuffer = await crypto.subtle.digest("SHA-256", data);

    const hashArray = new Uint8Array(hashBuffer);

    return Array.from(hashArray)
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  function getOrCreateVisitorId() {
    const key = "leovid";

    let visitorId = localStorage.getItem(key);

    // Reuse existing valid UUID
    if (visitorId && isValidUUID(visitorId)) {
      return visitorId;
    }

    // Generate a new UUID v4
    visitorId = generateUUID();

    localStorage.setItem(key, visitorId);

    return visitorId;
  }

  function generateUUID() {
    // Modern browsers
    if (crypto.randomUUID) {
      return crypto.randomUUID();
    }

    // Fallback for older browsers
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);

    // UUID v4
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(bytes, (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");

    return [
      hex.substring(0, 8),
      hex.substring(8, 12),
      hex.substring(12, 16),
      hex.substring(16, 20),
      hex.substring(20, 32),
    ].join("-");
  }

  function isValidUUID(value) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    );
  }

  const loadAdData = async (placementId, container) => {
    if (!container) return;
    var dataAdLoading = container.getAttribute("data-ad-loading");
    if (dataAdLoading === "true") {
      console.warn("Ad data is already loading for placement: " + placementId);
      return;
    }
    // Mark the container as loading to prevent duplicate fetches
    container.setAttribute("data-ad-loading", "true");

    // Construct the ad data URL based on placementId and optional attributes
    let adDataUrl = await getAdDataUrl(placementId, container); // Default URL for ad data
    try {
      const response = await fetch(adDataUrl, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch ad data: ${response.status}`);
      }

      adData = await response.json();
      renderAd(container, adData);
      setupImpressionTracking(container);
      setupClickTracking(container);
    } catch (error) {
      console.error("Unable to load ad data", error);
      container.innerHTML = `
        <div class="ad-widget-wrapper">
          <div class="ad-widget-header">
            <span>Ad</span> Unable to load ad data
          </div>
        </div>
      `;
    }
  };

  // 2. Tracking Utilities
  const trackEvent = (endpoint, payload) => {
    if (!endpoint) return;

    // Prepare data as Blob for sendBeacon
    const blob = new Blob([JSON.stringify(payload)], {
      type: "application/json",
    });

    // Use sendBeacon for non-blocking requests (crucial for click-aways), fallback to fetch
    if (navigator.sendBeacon) {
      navigator.sendBeacon(endpoint, blob);
    } else {
      fetch(endpoint, { method: "POST", body: blob, keepalive: true }).catch(
        () => {},
      );
    }
  };

  // 3. Viewability / TrueView Impression Tracking (MRC Standard: 50% in view for 1 second)
  const setupImpressionTracking = (container) => {
    let viewTimer = null;
    let hasFiredImpression = false;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (hasFiredImpression) return;

          if (entry.isIntersecting) {
            // Ad is at least 50% visible, start the 1-second timer
            viewTimer = setTimeout(() => {
              trackEvent(adData.tracking.impressionUrl, {
                adId: adData.adId,
                event: "impression",
                timestamp: new Date().toISOString(),
              });
              hasFiredImpression = true;
              observer.disconnect(); // Stop observing once tracked
            }, 1000);
          } else {
            // Ad left the viewport before 1 second elapsed, cancel timer
            if (viewTimer) clearTimeout(viewTimer);
          }
        });
      },
      {
        threshold: 0.5, // Trigger when 50% of the ad is visible
      },
    );

    observer.observe(container);
  };

  // 5. Render HTML with Data Attributes for Tracking
  const renderAd = (container, data) => {
    const productsHtml = data.products
      .map((product) => {
        const isHighlighted = product.isHighlighted;
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

    const widgetHtml = `
            <div class="ad-widget-wrapper">
                <div class="ad-widget-header">
                    <span>Ad</span> Quảng cáo
                </div>
                <div class="ad-widget-grid">
                    ${productsHtml}
                </div>
                <a href="${data.advertiser.landingPageUrl}" target="_blank" rel="noopener noreferrer" 
                   class="ad-widget-footer" data-track-type="advertiser_click" data-item-id="${data.adId}">
                    <img src="${data.advertiser.logoUrl}" alt="Logo" class="ad-widget-logo" />
                    <div>
                        <h3 class="ad-widget-brand-title">${data.advertiser.title}</h3>
                        <p class="ad-widget-brand-subtitle">${data.advertiser.name}</p>
                    </div>
                </a>
            </div>
        `;

    container.innerHTML = widgetHtml;
  };

  // 6. Event Delegation for Click Tracking
  const setupClickTracking = (container) => {
    container.addEventListener("click", (event) => {
      // Find the closest anchor tag that was clicked
      const targetElement = event.target.closest("a[data-track-type]");

      if (targetElement) {
        const trackType = targetElement.getAttribute("data-track-type");
        const itemId = targetElement.getAttribute("data-item-id");
        const destinationUrl = targetElement.getAttribute("href");

        // Fire async beacon (browser handles navigation naturally)
        trackEvent(adData.tracking.clickUrl, {
          adId: adData.adId,
          event: trackType,
          itemId: itemId,
          destination: destinationUrl,
          timestamp: new Date().toISOString(),
        });
      }
    });
  };

  const nodes = document.getElementsByClassName("leo-ad-container");

  Array.from(nodes).forEach((node) => {
    const placementId = node.getAttribute("data-ad-placement");
    if (
      typeof placementId !== "undefined" &&
      placementId !== null &&
      placementId !== ""
    ) {
      loadAdData(placementId, node);
    }
  });
})();
