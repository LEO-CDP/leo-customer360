/* Customer 360 Admin -- Persona Management + Analytics view. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var charts = {};
  var PALETTE = ["#4f46e5", "#0ea5e9", "#22c55e", "#f59e0b", "#ef4444", "#a855f7"];

  function renderChart(id, config) {
    if (charts[id]) {
      charts[id].destroy();
      delete charts[id];
    }
    var el = document.getElementById(id);
    if (!el) return;
    charts[id] = new Chart(el.getContext("2d"), config);
  }

  function personaRowVm(p) {
    var displayName = p.persona_name || p.persona_code || ("Persona " + fmt.shortId(p.persona_id));
    return $.extend({}, p, {
      displayName: displayName,
      subLabel: p.persona_code || fmt.shortId(p.persona_id),
      initials: fmt.initials(displayName),
      domainLabel: fmt.domainLabel(p.domain),
      categoryLabel: p.persona_category || "—",
      valueTierLabel: fmt.titleCase(p.customer_value_tier) || "—",
      riskLabel: fmt.titleCase(p.risk_level) || "—",
      riskBadgeClass: fmt.churnBadgeClass(p.risk_level),
      activeLabel: p.is_active ? "Active" : "Inactive",
      activeBadgeClass: p.is_active ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-600",
      personaScoreLabel: p.persona_score !== null && p.persona_score !== undefined ? Number(p.persona_score).toFixed(1) : "—",
      confidenceLabel: p.confidence_score !== null && p.confidence_score !== undefined ? Math.round(Number(p.confidence_score) * 100) + "%" : "—",
      computedAtLabel: fmt.dateTime(p.computed_at)
    });
  }

  var dtv = C360.DataTableView.create({
    columns: [
      { label: "Persona", type: "identity", nameField: "displayName", subField: "subLabel", avatarField: "initials" },
      { label: "Domain", field: "domainLabel" },
      { label: "Category", field: "categoryLabel" },
      { label: "Value Tier", field: "valueTierLabel" },
      { label: "Risk", type: "badge", field: "riskLabel", classField: "riskBadgeClass" },
      { label: "Status", type: "badge", field: "activeLabel", classField: "activeBadgeClass" },
      { label: "Persona Score", field: "personaScoreLabel" },
      { label: "Confidence", field: "confidenceLabel" },
      { label: "Computed At", field: "computedAtLabel", muted: true }
    ],
    rowVm: personaRowVm,
    rowId: function (vm) { return vm.persona_id; },
    rowClickable: false,
    resourceLabel: "persona",
    fetch: function (params) {
      return api("/persona/list", params);
    },
    onError: function (xhr) { showApiError("loading personas", xhr); },
    el: {
      thead: "#personas-thead",
      tbody: "#personas-tbody",
      loading: "#personas-loading",
      empty: "#personas-empty",
      countLabel: "#personas-count-label",
      loadMoreBtn: "#btn-personas-load-more"
    }
  });

  function analyticsParams() {
    var params = { days: C360.config.getDataPeriodDays() };
    var domain = $("#persona-domain-filter").val();
    var status = $("#persona-status-filter").val();
    if (domain) params.domain = domain;
    if (status === "true") params.is_active = true;
    if (status === "false") params.is_active = false;
    return params;
  }

  function updateKpis(summary) {
    $("#persona-kpi-total").text(fmt.int(summary.total_personas || 0));
    $("#persona-kpi-active-inactive").text(
      fmt.int(summary.active_personas || 0) + " / " + fmt.int(summary.inactive_personas || 0)
    );
    $("#persona-kpi-confidence").text(Math.round(Number(summary.avg_confidence_score || 0) * 100) + "%");
  }

  function renderCategoryChart(summary) {
    var rows = (summary.by_category || []).slice(0, 8);
    renderChart("chart-persona-category", {
      type: "bar",
      data: {
        labels: rows.map(function (r) { return fmt.titleCase(r.value); }),
        datasets: [{ data: rows.map(function (r) { return r.count; }), backgroundColor: PALETTE }]
      },
      options: {
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  function renderRiskChart(summary) {
    var rows = summary.by_risk_level || [];
    renderChart("chart-persona-risk", {
      type: "doughnut",
      data: {
        labels: rows.map(function (r) { return fmt.titleCase(r.value); }),
        datasets: [{ data: rows.map(function (r) { return r.count; }), backgroundColor: PALETTE }]
      },
      options: {
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } }
      }
    });
  }

  function loadAnalytics() {
    return api("/persona/analytics/summary", analyticsParams())
      .done(function (summary) {
        updateKpis(summary || {});
        renderCategoryChart(summary || {});
        renderRiskChart(summary || {});
      })
      .fail(function (xhr) {
        showApiError("loading persona analytics", xhr);
      });
  }

  function load() {
    dtv.load(false);
    loadAnalytics();
  }

  function bindEvents() {
    dtv.bindLoadMore();

    $("#persona-domain-filter").on("change", function () {
      dtv.setFilter("domain", $(this).val() || "");
      loadAnalytics();
    });

    $("#persona-status-filter").on("change", function () {
      var status = $(this).val();
      dtv.setFilter("is_active", status === "" ? "" : String(status));
      loadAnalytics();
    });

    $("#data-period-select").on("change", function () {
      loadAnalytics();
    });
  }

  C360.router.define("/personas", {
    section: "view-personas",
    tab: "personas",
    mount: function () { load(); }
  });

  // Backward compatibility for old deep links.
  C360.router.redirect("/journeys", "/personas");

  C360.personaManagementView = { load: load, bindEvents: bindEvents };
})(window.C360);
