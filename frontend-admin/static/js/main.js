/* Customer 360 Admin -- app bootstrap: injects static partials, wires up
 * the settings modal, and kicks off the initial load.
 *
 * View routing itself lives in static/js/router.js (a small React-Router
 * style hash router). This file does NOT know about individual views --
 * every listing/detail/editor view (profiles, segments, journeys, scoring
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
    journeys: "/journeys",
    scoring: "/scoring",
    analytics: "/analytics",
    campaigns: "/campaigns",
    datasources: "/datasources",
    admin: "/admin"
  };

  function bindBrowserEvents() {
    $("#btn-back-to-profiles").on("click", function () { C360.router.navigate("/profiles"); });

    $(".tab-btn").on("click", function () {
      C360.router.navigate(TAB_DEFAULT_PATH[$(this).data("tab")] || "/overview");
    });

    $("#btn-export-pdf").on("click", function () { window.print(); });

    $("#btn-settings").on("click", function () {
      var cfg = C360.config.current;
      $("#settings-api-base").val(cfg.apiBase);
      $("#settings-tenant-id").val(cfg.tenantId);
      $("#settings-modal").removeClass("hidden");
    });
    $("#btn-settings-cancel").on("click", function () { $("#settings-modal").addClass("hidden"); });
    $("#btn-settings-save").on("click", function () {
      C360.config.save($("#settings-api-base").val(), $("#settings-tenant-id").val());
      location.reload();
    });
  }

  function populateDomainSelects() {
    var labels = C360.fmt && C360.fmt.DOMAIN_LABELS ? C360.fmt.DOMAIN_LABELS : {};
    var keys = Object.keys(labels).filter(function (k) { return k !== "all"; }).sort();

    $("#domain-filter, #attributes-domain-filter").each(function () {
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
      $("body").append(C360.templates.html("settings-modal"));

      $("#footer-api-base").text(C360.config.current.apiBase);

      bindBrowserEvents();
      C360.listView.bindEvents();
      C360.profileDetailView.bindEvents();
      C360.segmentsView.bindEvents();
      C360.attributesView.bindEvents();

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
