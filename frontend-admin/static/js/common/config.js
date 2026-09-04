/* Customer 360 Admin -- configuration + API client.
 * All profile/business data is fetched live from customer360-api (FastAPI),
 * which reads PostgreSQL. Nothing here is hardcoded demo data.
 *
 * This file is the single source of truth for the API client and helpers.
 * renders C360_SERVER_CONFIG with environment-injected values. This file reads
 * that global if available, otherwise falls back to defaults. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var STORAGE_KEYS = {
    apiBase: "c360.apiBase",
    tenantId: "c360.tenantId",
    accessToken: "c360.accessToken",
    theme: "c360.theme",
    multiTenantEnabled: "c360.multiTenantEnabled",
    tenantOptions: "c360.tenantOptions",
    userId: "c360.userId",
    idToken: "c360.idToken",
    devUser: "c360.devUser"
  };

  var DEFAULTS = {
    apiBase: "http://localhost:8008/api/v1",
    tenantId: "11111111-1111-1111-1111-111111111111",
    accessToken: "",
    theme: "light",
    multiTenantEnabled: false,
    tenantOptions: [],
    userId: "",
    idToken: "",
    devUser: null,
    leoObserverLogDomain: "beta.leocdp.com",
    leoObserverTrackingUri: "/data/api/v1/tracking/logs",
    leoObserverTrackingEndpoint: "https://beta.leocdp.com/data/api/v1/tracking/logs",
    leoObserverCdnDomain: "gcore.jsdelivr.net/gh/LEO-CDP/leo-customer360@main"
  };

  var PERSONA_CATEGORY_OPTIONS = [
    { value: "Champion", label: "Champion" },
    { value: "High Value", label: "High Value" },
    { value: "Growth Potential", label: "Growth Potential" },
    { value: "Standard", label: "Standard" },
    { value: "Emerging Customer", label: "Emerging Customer" },
    { value: "New Customer", label: "New Customer" },
    { value: "Loyal Customer", label: "Loyal Customer" },
    { value: "Engaged Customer", label: "Engaged Customer" },
    { value: "Digital First", label: "Digital First" },
    { value: "Value Conscious", label: "Value Conscious" },
    { value: "At Risk", label: "At Risk" },
    { value: "Critical Risk", label: "Critical Risk" },
    { value: "Retention Priority", label: "Retention Priority" },
    { value: "Dormant Customer", label: "Dormant Customer" },
    { value: "Prospective Customer", label: "Prospective Customer" }
  ];

  function readBool(value, fallback) {
    if (value === null || typeof value === "undefined") return fallback;
    return value === "true";
  }

  function parseTenantOptions(raw) {
    var parts = [];
    if (Array.isArray(raw)) {
      parts = raw;
    } else if (typeof raw === "string") {
      parts = raw.split(/[\n,]/g);
    }
    var unique = {};
    return parts
      .map(function (item) { return String(item || "").trim(); })
      .filter(function (item) {
        if (!item || unique[item]) return false;
        unique[item] = true;
        return true;
      });
  }

  function decodeJwtPayload(token) {
    if (!token || token.split(".").length < 2) return null;
    try {
      var base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      var json = decodeURIComponent(atob(base64).split("").map(function (ch) {
        return "%" + ("00" + ch.charCodeAt(0).toString(16)).slice(-2);
      }).join(""));
      var payload = JSON.parse(json);
      return payload && typeof payload === "object" ? payload : null;
    } catch (e) {
      return null;
    }
  }

  function getConfig() {
    // First check if Jinja rendered C360_SERVER_CONFIG (FastAPI app.py)
    var serverConfig = window.C360_SERVER_CONFIG || {};
    var storedTenantOptions = [];
    try {
      storedTenantOptions = parseTenantOptions(JSON.parse(localStorage.getItem(STORAGE_KEYS.tenantOptions) || "[]"));
    } catch (e) {
      storedTenantOptions = [];
    }
    var tenantId = localStorage.getItem(STORAGE_KEYS.tenantId) || serverConfig.tenantId || DEFAULTS.tenantId;
    var tenantOptions = storedTenantOptions.length ? storedTenantOptions : [tenantId];
    var storedDevUser = null;
    try {
      storedDevUser = JSON.parse(localStorage.getItem(STORAGE_KEYS.devUser) || "null");
    } catch (e) {
      storedDevUser = null;
    }
    return {
      apiBase: localStorage.getItem(STORAGE_KEYS.apiBase) || serverConfig.apiBase || DEFAULTS.apiBase,
      tenantId: tenantId,
      accessToken: localStorage.getItem(STORAGE_KEYS.accessToken) || DEFAULTS.accessToken,
      idToken: localStorage.getItem(STORAGE_KEYS.idToken) || DEFAULTS.idToken,
      userId: localStorage.getItem(STORAGE_KEYS.userId) || DEFAULTS.userId,
      devUser: storedDevUser,
      theme: localStorage.getItem(STORAGE_KEYS.theme) || DEFAULTS.theme,
      multiTenantEnabled: readBool(localStorage.getItem(STORAGE_KEYS.multiTenantEnabled), DEFAULTS.multiTenantEnabled),
      tenantOptions: tenantOptions,
      leoObserverLogDomain: serverConfig.leoObserverLogDomain || DEFAULTS.leoObserverLogDomain,
      leoObserverTrackingUri: serverConfig.leoObserverTrackingUri || DEFAULTS.leoObserverTrackingUri,
      leoObserverTrackingEndpoint: serverConfig.leoObserverTrackingEndpoint || DEFAULTS.leoObserverTrackingEndpoint,
      leoObserverCdnDomain: serverConfig.leoObserverCdnDomain || DEFAULTS.leoObserverCdnDomain
    };
  }

  var CONFIG = getConfig();
  var DOMAINS_CACHE_KEY = "c360.domains";
  var mediaDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var isThemeListenerBound = false;
  var CHART_THEME = {
    dark: {
      text: "#f8fafc",
      grid: "rgba(148, 163, 184, 0.28)",
      border: "rgba(148, 163, 184, 0.38)",
      tooltipBg: "rgba(15, 23, 42, 0.95)",
      tooltipText: "#f8fafc"
    },
    light: {
      text: "#334155",
      grid: "rgba(100, 116, 139, 0.22)",
      border: "rgba(100, 116, 139, 0.32)",
      tooltipBg: "rgba(255, 255, 255, 0.98)",
      tooltipText: "#0f172a"
    }
  };
  var THEME_CLASSES = ["c360-theme-dark", "c360-theme-light", "c360-theme-system"];
  var PROFILE_CARD_IDS = [
    "identity-details-card",
    "communication-preferences-card",
    "working-details-card",
    "address-details-card",
    "other-attributes-card",
    "next-best-action-card"
  ];

  function apiRootFromBase(apiBase) {
    return String(apiBase || "").replace(/\/api\/v1\/?$/, "");
  }

  function ensureObject(parent, key) {
    if (!parent[key] || typeof parent[key] !== "object") {
      parent[key] = {};
    }
    return parent[key];
  }

  function applyChartTheme(shouldUseDark) {
    if (typeof Chart === "undefined") return;

    var palette = shouldUseDark ? CHART_THEME.dark : CHART_THEME.light;

    Chart.defaults.color = palette.text;
    Chart.defaults.borderColor = palette.grid;
    Chart.defaults.plugins = Chart.defaults.plugins || {};
    Chart.defaults.plugins.legend = Chart.defaults.plugins.legend || {};
    Chart.defaults.plugins.legend.labels = Chart.defaults.plugins.legend.labels || {};
    Chart.defaults.plugins.legend.labels.color = palette.text;
    Chart.defaults.plugins.title = Chart.defaults.plugins.title || {};
    Chart.defaults.plugins.title.color = palette.text;
    Chart.defaults.plugins.tooltip = Chart.defaults.plugins.tooltip || {};
    Chart.defaults.plugins.tooltip.backgroundColor = palette.tooltipBg;
    Chart.defaults.plugins.tooltip.titleColor = palette.tooltipText;
    Chart.defaults.plugins.tooltip.bodyColor = palette.tooltipText;
    Chart.defaults.plugins.tooltip.footerColor = palette.tooltipText;

    var instances = Chart.instances || {};
    Object.keys(instances).forEach(function (key) {
      var chart = instances[key];
      if (!chart || !chart.options) return;

      var plugins = ensureObject(chart.options, "plugins");
      var legend = ensureObject(plugins, "legend");
      var legendLabels = ensureObject(legend, "labels");
      legendLabels.color = palette.text;

      var title = ensureObject(plugins, "title");
      title.color = palette.text;

      var tooltip = ensureObject(plugins, "tooltip");
      tooltip.backgroundColor = palette.tooltipBg;
      tooltip.titleColor = palette.tooltipText;
      tooltip.bodyColor = palette.tooltipText;
      tooltip.footerColor = palette.tooltipText;

      var scales = chart.options.scales || {};
      Object.keys(scales).forEach(function (axisKey) {
        var axis = scales[axisKey];
        if (!axis || typeof axis !== "object") return;
        var ticks = ensureObject(axis, "ticks");
        ticks.color = palette.text;
        var grid = ensureObject(axis, "grid");
        grid.color = palette.grid;
        var border = ensureObject(axis, "border");
        border.color = palette.border;
      });

      chart.update("none");
    });
  }

  function applyTheme(theme, persist) {
    var selected = theme || "system";
    var prefersDark = mediaDark ? mediaDark.matches : false;
    var shouldUseDark = selected === "dark" || (selected === "system" && prefersDark);
    var root = document.documentElement;

    THEME_CLASSES.forEach(function (className) {
      root.classList.remove(className);
    });

    root.classList.add("c360-theme-" + selected);
    document.documentElement.classList.toggle("dark", shouldUseDark);
    document.documentElement.setAttribute("data-c360-theme", selected);
    applyChartTheme(shouldUseDark);

    PROFILE_CARD_IDS.forEach(function (id) {
      var element = document.getElementById(id);
      if (!element) return;
      THEME_CLASSES.forEach(function (className) {
        element.classList.remove(className);
      });
      element.classList.add("c360-theme-" + selected);
    });

    if (persist !== false) {
      localStorage.setItem(STORAGE_KEYS.theme, selected);
      CONFIG.theme = selected;
    }
  }

  function onSystemThemeChanged() {
    if ((CONFIG.theme || "system") === "system") {
      applyTheme("system", false);
    }
  }

  function bindSystemThemeListener() {
    if (!mediaDark || isThemeListenerBound) return;
    if (typeof mediaDark.addEventListener === "function") {
      mediaDark.addEventListener("change", onSystemThemeChanged);
      isThemeListenerBound = true;
      return;
    }
    if (typeof mediaDark.addListener === "function") {
      mediaDark.addListener(onSystemThemeChanged);
      isThemeListenerBound = true;
    }
  }

  function unbindSystemThemeListener() {
    if (!mediaDark || !isThemeListenerBound) return;
    if (typeof mediaDark.removeEventListener === "function") {
      mediaDark.removeEventListener("change", onSystemThemeChanged);
      isThemeListenerBound = false;
      return;
    }
    if (typeof mediaDark.removeListener === "function") {
      mediaDark.removeListener(onSystemThemeChanged);
      isThemeListenerBound = false;
    }
  }

  function themeLoader(theme, persist) {
    var selected = theme || CONFIG.theme || "system";
    applyTheme(selected, persist);
    if (selected === "system") {
      bindSystemThemeListener();
    } else {
      unbindSystemThemeListener();
    }
    return selected;
  }

  function currentUserFromConfig() {
    if (CONFIG.devUser) {
      var du = CONFIG.devUser;
      return {
        username: du.username || "Developer",
        email: du.email || "-",
        fullName: du.full_name || "",
        roles: du.roles && du.roles.length ? du.roles : ["user"],
        authMode: du.is_root ? "Dev Root Credentials" : "Dev Credentials",
        isRoot: !!du.is_root,
        userId: du.user_id || null
      };
    }

    var payload = decodeJwtPayload(CONFIG.accessToken);
    var roles = [];
    if (payload && payload.realm_access && Array.isArray(payload.realm_access.roles)) {
      roles = roles.concat(payload.realm_access.roles);
    }
    if (payload && payload.resource_access && typeof payload.resource_access === "object") {
      Object.keys(payload.resource_access).forEach(function (clientKey) {
        var client = payload.resource_access[clientKey];
        if (client && Array.isArray(client.roles)) {
          roles = roles.concat(client.roles);
        }
      });
    }
    roles = parseTenantOptions(roles);
    return {
      username: payload ? (payload.preferred_username || payload.name || payload.email || payload.sub || "Authenticated User") : "Developer",
      email: payload ? (payload.email || "-") : "-",
      fullName: payload ? (payload.name || "") : "",
      roles: roles.length ? roles : [CONFIG.accessToken ? "user" : "developer"],
      authMode: CONFIG.accessToken ? "SSO Token" : "Dev Header",
      isRoot: false,
      userId: CONFIG.userId || null
    };
  }

  function api(path, params, method) {
    var httpMethod = method || "GET";
    var headers = { "X-Tenant-Id": CONFIG.tenantId };
    if (CONFIG.accessToken) {
      headers.Authorization = "Bearer " + CONFIG.accessToken;
    }
    if (CONFIG.userId) {
      headers["X-User-Id"] = CONFIG.userId;
    }
    var options = {
      url: CONFIG.apiBase + path,
      method: httpMethod,
      dataType: "json",
      headers: headers
    };
    // GET/DELETE: serialize params as a query string (unchanged behavior).
    // POST/PATCH/PUT: send as a JSON request body so FastAPI Pydantic
    // "payload" body params can actually be parsed -- previously these were
    // sent as application/x-www-form-urlencoded, which only happened to
    // work for POST calls that pass no params at all (e.g. recompute-all).
    if (httpMethod === "GET" || httpMethod === "DELETE") {
      options.data = params || {};
    } else {
      options.contentType = "application/json";
      options.data = JSON.stringify(params || {});
    }

    var request = $.ajax(options);
    request.fail(function (xhr) {
      if (xhr && xhr.status === 401 && C360.config && typeof C360.config.logout === "function") {
        C360.config.logout(function () {
          window.location.reload();
        });
      }
    });
    return request;
  }

  function showApiError(context, xhr) {
    // status 0 = no HTTP response reached the browser (network/DNS/TLS, or a blocked
    // CORS preflight). Any other status means the request DID reach the API, so blaming
    // "CORS / unreachable" would be misleading — report what actually came back.
    var status = xhr && typeof xhr.status === "number" ? xhr.status : 0;
    var detail = "";
    if (xhr && xhr.responseJSON && xhr.responseJSON.detail) {
      detail = typeof xhr.responseJSON.detail === "string"
        ? xhr.responseJSON.detail
        : JSON.stringify(xhr.responseJSON.detail);
    } else if (xhr && xhr.responseText) {
      detail = String(xhr.responseText).slice(0, 200);
    }
    var at = " at " + CONFIG.apiBase + " (" + context + ")";
    var msg;
    if (status === 0) {
      msg = "Could not reach the Customer 360 API" + at + ". Check that customer360-api is "
        + "running and reachable, and that CORS is enabled.";
    } else if (status >= 500) {
      msg = "The Customer 360 API returned a server error (HTTP " + status + ")" + at
        + " — a backend fault; check the customer360-api logs." + (detail ? " Detail: " + detail : "");
    } else if (status === 401 || status === 403) {
      msg = "Not authorized (HTTP " + status + ")" + at
        + ". Your session may have expired — try signing in again.";
    } else if (status >= 400) {
      msg = "The Customer 360 API rejected the request (HTTP " + status + ")" + at + "."
        + (detail ? " Detail: " + detail : "");
    } else {
      msg = "Unexpected response (HTTP " + status + ")" + at + " from the Customer 360 API.";
    }
    $("#alert-banner").removeClass("hidden").text(msg);
    $("#api-status-dot").removeClass("bg-green-500 bg-slate-300").addClass("bg-red-500");
  }

  function pingHealth() {
    $.ajax({ url: apiRootFromBase(CONFIG.apiBase) + "/health", method: "GET", dataType: "json", timeout: 4000 })
      .done(function () {
        $("#api-status-dot").removeClass("bg-red-500 bg-slate-300").addClass("bg-green-500");
        $("#alert-banner").addClass("hidden");
      })
      .fail(function (xhr) { showApiError("health check", xhr); });
  }

  function loadSystemMetadata() {
    console.log("Loading system metadata from " + apiRootFromBase(CONFIG.apiBase) + "/api/v1/metadata");
    return $.ajax({
      url: apiRootFromBase(CONFIG.apiBase) + "/api/v1/metadata",
      method: "GET",
      dataType: "json"
    });
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

  function saveConfig(apiBaseOrConfig, maybeTenantId) {
    var next = (typeof apiBaseOrConfig === "object" && apiBaseOrConfig !== null)
      ? apiBaseOrConfig
      : {
          apiBase: apiBaseOrConfig,
          tenantId: maybeTenantId
        };

    var apiBase = String(next.apiBase || "").trim();
    var tenantId = String(next.tenantId || "").trim();
    var accessToken = String(next.accessToken || "").trim();
    var theme = String(next.theme || "system").trim() || "system";
    var multiTenantEnabled = !!next.multiTenantEnabled;
    var tenantOptions = parseTenantOptions(next.tenantOptions || [tenantId]);
    if (tenantId && tenantOptions.indexOf(tenantId) === -1) tenantOptions.unshift(tenantId);

    localStorage.setItem(STORAGE_KEYS.apiBase, apiBase || DEFAULTS.apiBase);
    localStorage.setItem(STORAGE_KEYS.tenantId, tenantId || DEFAULTS.tenantId);
    localStorage.setItem(STORAGE_KEYS.accessToken, accessToken);
    localStorage.setItem(STORAGE_KEYS.theme, theme);
    localStorage.setItem(STORAGE_KEYS.multiTenantEnabled, String(multiTenantEnabled));
    localStorage.setItem(STORAGE_KEYS.tenantOptions, JSON.stringify(tenantOptions));

    CONFIG = getConfig();
    C360.config.current = CONFIG;
  }

  function switchTenant(tenantId) {
    var selected = String(tenantId || "").trim();
    if (!selected) return;
    saveConfig({
      apiBase: CONFIG.apiBase,
      tenantId: selected,
      accessToken: CONFIG.accessToken,
      theme: CONFIG.theme,
      multiTenantEnabled: CONFIG.multiTenantEnabled,
      tenantOptions: parseTenantOptions((CONFIG.tenantOptions || []).concat([selected]))
    });
  }

  function logout(callback) {
    localStorage.clear();
    sessionStorage.clear();
    CONFIG.accessToken = "";
    CONFIG.idToken = "";
    CONFIG.userId = "";
    CONFIG.devUser = null;
    C360.config.current = CONFIG;
    if (typeof callback === "function") callback();
  }

  // True once a session exists: an SSO access token, or a resolved dev-mode
  // login (root or a real sys_user row) -- see auth-view.js.
  function isAuthenticated() {
    return !!(CONFIG.accessToken || CONFIG.devUser);
  }

  // Persists the profile + dev JWT returned by POST /auth/login (dev mode,
  // SSO_LOGIN=false). The token is stored the same way an SSO access token
  // is, so api() automatically sends it as `Authorization: Bearer <token>`
  // -- X-Tenant-Id/X-User-Id are still set as a defense-in-depth fallback.
  function setDevSession(loginResponse) {
    localStorage.setItem(STORAGE_KEYS.devUser, JSON.stringify(loginResponse));
    localStorage.setItem(STORAGE_KEYS.userId, loginResponse.user_id || "");
    localStorage.setItem(STORAGE_KEYS.tenantId, loginResponse.tenant_id);
    localStorage.setItem(STORAGE_KEYS.accessToken, loginResponse.access_token || "");
    CONFIG = getConfig();
    C360.config.current = CONFIG;
  }

  // Persists tokens returned by POST /auth/callback (SSO_LOGIN=true).
  function setSsoSession(tokenResponse) {
    localStorage.setItem(STORAGE_KEYS.accessToken, tokenResponse.access_token || "");
    localStorage.setItem(STORAGE_KEYS.idToken, tokenResponse.id_token || "");
    CONFIG = getConfig();
    C360.config.current = CONFIG;
  }

  // POST /auth/login -- dev-mode credential login (SSO_LOGIN=false).
  function login(username, password) {
    return api("/auth/login", { username: username, password: password, tenant_id: CONFIG.tenantId }, "POST")
      .done(function (resp) { setDevSession(resp); });
  }

  // POST /auth/callback -- exchanges a Keycloak authorization code for tokens.
  function exchangeSsoCode(code, redirectUri) {
    return api("/auth/callback", { code: code, redirect_uri: redirectUri }, "POST")
      .done(function (resp) { setSsoSession(resp); });
  }

  // Builds the Keycloak Authorization Code redirect URL from the non-secret
  // sso_config published by GET /metadata (see metadata_repository.py).
  function buildSsoAuthorizeUrl(ssoConfig, redirectUri, state) {
    var base = String(ssoConfig.login_url || "").replace(/\/$/, "");
    var params = {
      client_id: ssoConfig.client_id,
      redirect_uri: redirectUri,
      response_type: "code",
      scope: "openid",
      state: state
    };
    return base + "/realms/" + encodeURIComponent(ssoConfig.realm) + "/protocol/openid-connect/auth?" + $.param(params);
  }

  // The Period selector is now a per-view component (Overview: #overview-period-select,
  // Analytics: #analytics-period-select) rather than one shared header control -- pass
  // the relevant selector; views without their own period control get the 90-day default.
  function getDataPeriodDays(selector) {
    var value = parseInt($(selector || "#data-period-select").val(), 10);
    return isNaN(value) ? 90 : value;
  }

  // Frontend-only UX gate (show/hide edit controls) -- the API independently
  // re-checks the 'admin' role server-side on the actual mutation (fail-closed;
  // see customer360-api/core/auth.py::require_admin), so this is never the
  // only line of defense.
  function isAdmin() {
    var roles = currentUserFromConfig().roles || [];
    return roles.some(function (r) { return String(r).toLowerCase() === "admin"; });
  }

  C360.config = {
    get: getConfig,
    current: CONFIG,
    api: api,
    showApiError: showApiError,
    pingHealth: pingHealth,
    loadSystemMetadata: loadSystemMetadata,
    loadDomains: loadDomains,
    save: saveConfig,
    switchTenant: switchTenant,
    logout: logout,
    isAuthenticated: isAuthenticated,
    login: login,
    exchangeSsoCode: exchangeSsoCode,
    buildSsoAuthorizeUrl: buildSsoAuthorizeUrl,
    parseTenantOptions: parseTenantOptions,
    currentUser: currentUserFromConfig,
    decodeJwtPayload: decodeJwtPayload,
    applyTheme: applyTheme,
    themeLoader: themeLoader,
    getDataPeriodDays: getDataPeriodDays,
    isAdmin: isAdmin,
    personaCategoryOptions: PERSONA_CATEGORY_OPTIONS
  };

  C360.themeLoader = themeLoader;

})(window.C360);
