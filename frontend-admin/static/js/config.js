/* Customer 360 Admin -- configuration + API client.
 * All profile/business data is fetched live from customer360-api (FastAPI),
 * which reads PostgreSQL. Nothing here is hardcoded demo data.
 *
 * This file is the single source of truth for the API client and helpers.
 * When served via frontend-admin/app.py, the Jinja template (jinja/config.js.j2)
 * renders C360_SERVER_CONFIG with environment-injected values. This file reads
 * that global if available, otherwise falls back to defaults. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var DEFAULTS = {
    apiBase: "http://localhost:8008/api/v1",
    tenantId: "11111111-1111-1111-1111-111111111111"
  };

  function getConfig() {
    // First check if Jinja rendered C360_SERVER_CONFIG (FastAPI app.py)
    var serverConfig = window.C360_SERVER_CONFIG || {};
    return {
      apiBase: localStorage.getItem("c360.apiBase") || serverConfig.apiBase || DEFAULTS.apiBase,
      tenantId: localStorage.getItem("c360.tenantId") || serverConfig.tenantId || DEFAULTS.tenantId
    };
  }

  var CONFIG = getConfig();
  var DOMAINS_CACHE_KEY = "c360.domains";

  function api(path, params, method) {
    return $.ajax({
      url: CONFIG.apiBase + path,
      method: method || "GET",
      data: params || {},
      dataType: "json",
      headers: { "X-Tenant-Id": CONFIG.tenantId }
    });
  }

  function showApiError(context, xhr) {
    var msg = "Could not reach the Customer 360 API at " + CONFIG.apiBase + " (" + context + "). " +
      "Make sure customer360-api is running and reachable, and CORS is enabled. " +
      (xhr && xhr.status ? ("HTTP " + xhr.status) : "");
    $("#alert-banner").removeClass("hidden").text(msg);
    $("#api-status-dot").removeClass("bg-green-500 bg-slate-300").addClass("bg-red-500");
  }

  function pingHealth() {
    $.ajax({ url: CONFIG.apiBase.replace(/\/api\/v1$/, "") + "/health", method: "GET", dataType: "json", timeout: 4000 })
      .done(function () {
        $("#api-status-dot").removeClass("bg-red-500 bg-slate-300").addClass("bg-green-500");
        $("#alert-banner").addClass("hidden");
      })
      .fail(function (xhr) { showApiError("health check", xhr); });
  }

  function applyDomainLabels(labels) {
    if (C360.fmt && typeof C360.fmt.setDomainLabels === "function") {
      C360.fmt.setDomainLabels(labels);
    }
  }

  function loadDomains() {
    var cached = null;
    try {
      cached = JSON.parse(localStorage.getItem(DOMAINS_CACHE_KEY));
    } catch (e) { cached = null; }
    if (cached) applyDomainLabels(cached);

    var req = api("/metadata/domains");
    req.done(function (labels) {
      if (labels && typeof labels === "object") {
        applyDomainLabels(labels);
        try { localStorage.setItem(DOMAINS_CACHE_KEY, JSON.stringify(labels)); } catch (e) { /* ignore */ }
      }
    });
    return req;
  }

  function saveConfig(apiBase, tenantId) {
    localStorage.setItem("c360.apiBase", apiBase.trim());
    localStorage.setItem("c360.tenantId", tenantId.trim());
  }

  function getDataPeriodDays() {
    var value = parseInt($("#data-period-select").val(), 10);
    return isNaN(value) ? 90 : value;
  }

  C360.config = {
    get: getConfig,
    current: CONFIG,
    api: api,
    showApiError: showApiError,
    pingHealth: pingHealth,
    loadDomains: loadDomains,
    save: saveConfig,
    getDataPeriodDays: getDataPeriodDays
  };
  C360.config = Object.freeze(C360.config);
  
})(window.C360);
