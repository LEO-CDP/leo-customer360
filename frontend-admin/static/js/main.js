/* Customer 360 Admin -- app bootstrap: injects static partials, wires up
 * the settings modal, and kicks off the initial load.
 *
 * View routing itself lives in static/js/router.js (a small React-Router
 * style hash router). This file does NOT know about individual views --
 * every listing/detail/editor view (profiles, segments, personas, scoring
 * models, analytics reports, data source connectors/importers, identity
 * resolution rules, admin users, ...) registers its own route(s) with
 * C360.router from its own file. Adding a new view is therefore a change
 * to that view's file only, never to this bootstrap. */
window.C360 = window.C360 || {};

const TIME_CHECK_API_HEALTH = 60000;
(function (C360) {
  "use strict";

  Handlebars.registerHelper("json", function (v) { return JSON.stringify(v); });

  // Nav tab -> the path it navigates to. Each of these paths (and any
  // sub-routes registered under it, e.g. /segments/:id or
  // /datasources/connectors) is owned by the corresponding view module.
  var TAB_DEFAULT_PATH = {
    overview: "/overview",
    profiles: "/profiles",
    segments: "/segments",
    attributes: "/attributes",
    personas: "/personas",
    scoring: "/scoring",
    analytics: "/analytics",
    campaigns: "/campaigns",
    datasources: "/datasources",
    admin: "/admin"
  };

  function toggleSettingsModal(visible) {
    var $modal = $("#user-settings-modal");
    if (!$modal.length) return;
    $modal.toggleClass("hidden", !visible);
  }

  function fillTenantSwitcher(cfg) {
    var options = cfg.tenantOptions && cfg.tenantOptions.length ? cfg.tenantOptions.slice() : [cfg.tenantId];
    var $select = $("#settings-tenant-select");
    $select.empty();
    options.forEach(function (tenantId) {
      $select.append($("<option></option>").attr("value", tenantId).text(tenantId));
    });
    if (cfg.tenantId) {
      $select.val(cfg.tenantId);
    }
    $select.prop("disabled", !cfg.multiTenantEnabled);
  }

  function populateSettingsModal() {
    var cfg = C360.config.current;
    var user = C360.config.currentUser();
    $("#settings-api-base").val(cfg.apiBase);
    $("#settings-tenant-id").val(cfg.tenantId);
    $("#settings-access-token").val(cfg.accessToken || "");
    $("#settings-theme").val(cfg.theme || "system");
    $("#settings-multi-tenant-enabled").prop("checked", !!cfg.multiTenantEnabled);
    $("#settings-tenant-options").val((cfg.tenantOptions || []).join(", "));
    fillTenantSwitcher(cfg);

    $("#settings-user-username").text(user.username || "-");
    $("#settings-user-email").text(user.email || "-");
    $("#settings-user-roles").text((user.roles || []).join(", ") || "-");
    $("#settings-auth-mode").text(user.authMode || "-");

    C360.config.loadSystemMetadata().done(function (metadata) {
      if (metadata && typeof metadata.sso_login !== "undefined") {
        $("#settings-auth-mode").text(metadata.sso_login ? "SSO (token required)" : "Dev Header (SSO disabled)");
      }
    });
  }

  function bindBrowserEvents() {
    $("#btn-back-to-profiles").on("click", function () { C360.router.navigate("/profiles"); });

    $(".tab-btn").on("click", function () {
      C360.router.navigate(TAB_DEFAULT_PATH[$(this).data("tab")] || "/overview");
    });

    $("#btn-export-pdf").on("click", function () { window.print(); });

    $("#btn-settings").on("click", function () {
      populateSettingsModal();
      toggleSettingsModal(true);
    });

    $("#btn-settings-cancel, #btn-settings-close").on("click", function () { toggleSettingsModal(false); });
    $(document).on("click", "#user-settings-modal", function (e) {
      if (e.target.id === "user-settings-modal") {
        toggleSettingsModal(false);
      }
    });

    $("#settings-tenant-select").on("change", function () {
      var selected = String($(this).val() || "").trim();
      if (selected) $("#settings-tenant-id").val(selected);
    });

    $("#settings-multi-tenant-enabled").on("change", function () {
      var enabled = $(this).is(":checked");
      $("#settings-tenant-select").prop("disabled", !enabled);
      if (enabled) {
        var selected = String($("#settings-tenant-select").val() || "").trim();
        if (selected) $("#settings-tenant-id").val(selected);
      }
    });

    $("#btn-settings-save").on("click", function () {
      var tenantOptions = C360.config.parseTenantOptions($("#settings-tenant-options").val());
      var multiTenantEnabled = $("#settings-multi-tenant-enabled").is(":checked");
      var selectedTenant = String($("#settings-tenant-id").val() || "").trim();

      if (multiTenantEnabled) {
        var fromSelect = String($("#settings-tenant-select").val() || "").trim();
        if (fromSelect) selectedTenant = fromSelect;
      }

      C360.config.save({
        apiBase: $("#settings-api-base").val(),
        tenantId: selectedTenant,
        accessToken: $("#settings-access-token").val(),
        theme: $("#settings-theme").val(),
        multiTenantEnabled: multiTenantEnabled,
        tenantOptions: tenantOptions
      });
      C360.themeLoader($("#settings-theme").val(), false);
      location.reload();
    });

    $("#btn-settings-logout").on("click", function () {
      C360.config.logout();
      toggleSettingsModal(false);
      location.reload();
    });

    $(document).on("keydown", function (e) {
      if (e.key === "Escape") {
        toggleSettingsModal(false);
      }
    });
  }

  function populateDomainSelects() {
    var labels = C360.fmt && C360.fmt.DOMAIN_LABELS ? C360.fmt.DOMAIN_LABELS : {};
    var keys = Object.keys(labels).filter(function (k) { return k !== "all"; }).sort();

    $("#domain-filter, #attributes-domain-filter, #persona-domain-filter, #attribute-add-domain-scope").each(function () {
      var $sel = $(this);
      var current = $sel.val();
      $sel.find("option[value!=''][value!='all']").remove();
      keys.forEach(function (key) {
        $sel.append($("<option></option>").attr("value", key).text(labels[key]));
      });
      if (current && ($sel.find("option[value='" + current + "']").length || current === "" || current === "all")) {
        $sel.val(current);
      }
    });
  }

  $(function () {

    C360.templates.loadAll().done(function () {
      $("#app-header").html(C360.templates.html("tabs"));
      $("#view-list").html(C360.templates.html("profiles-list"));
      $("#view-placeholder").html(C360.templates.html("placeholder"));
      $("#segment-view-list").html(C360.templates.html("segments-list"));
      $("#view-attributes").html(C360.templates.html("attributes-list"));
      $("#view-datasources").html(C360.templates.html("data-source-list"));
      $("#view-scoring").html(C360.templates.html("scoring-model-list"));
      $("#persona-list-content").html(C360.templates.html("persona-list"));
      $("body").append(C360.templates.html("settings-modal"));

      // apply the current theme (light/dark/system) to the page, and re-apply it
      C360.themeLoader(C360.config.current.theme, false);

      $("#footer-api-base").text(C360.config.current.apiBase);

      bindBrowserEvents();
      C360.listView.bindEvents();
      C360.profileDetailView.bindEvents();
      C360.segmentsView.bindEvents();
      C360.personaManagementView.bindEvents();
      C360.attributesView.bindEvents();
      C360.dataSourceView.bindEvents();
      C360.scoringModelView.bindEvents();

      C360.config.pingHealth();
      setInterval(C360.config.pingHealth, TIME_CHECK_API_HEALTH);

      // Load authoritative domain labels from the API and refresh any
      // UI that depends on them (filter selects, chart axes, row labels).
      C360.config.loadDomains().always(function () {
        populateDomainSelects();
        C360.router.start("/overview");
        // Pre-fetch the profiles list in the background even if we didn't
        // land on the Profiles tab, so switching to it feels instant.
        C360.listView.load(false);
      });
    }).fail(function () {
      $("#alert-banner").removeClass("hidden").text(
        "Failed to load UI templates from static/templates/. Serve this folder with a static HTTP server " +
        "(opening index.html via file:// blocks the template/API requests)."
      );
    });
  });
})(window.C360);
