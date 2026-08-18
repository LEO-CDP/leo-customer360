/* Customer 360 Admin -- Reporting Overview dashboard.
 * Fetches core-customer360-api /reporting/* endpoints (see
 * customer360-api/core/routers/reporting_api.py) and renders KPI cards plus
 * Chart.js visualizations: processing funnel, profile counts by domain,
 * raw profiles by source system, and identity graph channel coverage.
 * The "Top Duplicate Master Profiles" table previously rendered here has
 * moved to the Profiles list view's "Duplicate Profiles" tab -- see
 * profile-duplicates-view.js. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var charts = {};
  var PALETTE = ["#6366f1", "#22c55e", "#3b82f6", "#f97316", "#ef4444", "#a855f7", "#14b8a6", "#eab308"];

  function renderChart(id, config) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    var el = document.getElementById(id);
    if (!el) return;
    charts[id] = new Chart(el.getContext("2d"), config);
  }

  function buildVm(summary) {
    return {
      totalRawProfilesLabel: fmt.int(summary.total_raw_profiles),
      totalMasterProfilesLabel: fmt.int(summary.total_master_profiles),
      duplicateCountLabel: fmt.int(summary.duplicate_master_profile_count),
      processedLabel: fmt.int(summary.processed_raw_profiles),
      inProgressLabel: fmt.int(summary.in_progress_raw_profiles),
      pendingLabel: fmt.int(summary.pending_raw_profiles)
    };
  }

  function renderStatusFunnelChart(summary) {
    var rows = summary.raw_profiles_by_status || [];
    renderChart("chart-status-funnel", {
      type: "doughnut",
      data: {
        labels: rows.map(function (r) { return r.label; }),
        datasets: [{ data: rows.map(function (r) { return r.count; }), backgroundColor: PALETTE }]
      },
      options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } }
    });
  }

  function renderDomainChart(summary) {
    var rawByDomain = summary.raw_profiles_by_domain || [];
    var masterByDomain = summary.master_profiles_by_domain || [];
    var domains = [];
    rawByDomain.concat(masterByDomain).forEach(function (r) {
      if (domains.indexOf(r.domain) === -1) domains.push(r.domain);
    });
    var rawMap = {}, masterMap = {};
    rawByDomain.forEach(function (r) { rawMap[r.domain] = r.count; });
    masterByDomain.forEach(function (r) { masterMap[r.domain] = r.count; });

    renderChart("chart-domain-breakdown", {
      type: "bar",
      data: {
        labels: domains.map(function (d) { return fmt.domainLabel(d); }),
        datasets: [
          { label: "Raw Profiles", data: domains.map(function (d) { return rawMap[d] || 0; }), backgroundColor: PALETTE[0] },
          { label: "Master Profiles", data: domains.map(function (d) { return masterMap[d] || 0; }), backgroundColor: PALETTE[1] }
        ]
      },
      options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true } } }
    });
  }

  function renderSourceSystemsChart(summary) {
    var rows = (summary.raw_profiles_by_source_system || []).slice(0, 10);
    renderChart("chart-source-systems", {
      type: "bar",
      data: {
        labels: rows.map(function (r) { return r.source_system + " (" + fmt.titleCase(r.domain) + ")"; }),
        datasets: [{ label: "Raw Profiles", data: rows.map(function (r) { return r.count; }), backgroundColor: PALETTE[2] }]
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } }
      }
    });
  }

  function renderIdentityCoverageChart(coverage) {
    var total = coverage.total_master_profiles || 0;
    var channels = [
      { label: "Email", count: coverage.with_email },
      { label: "Phone", count: coverage.with_phone_number },
      { label: "Device ID", count: coverage.with_device_id },
      { label: "Advertising ID", count: coverage.with_advertising_id },
      { label: "Cookie ID", count: coverage.with_cookie_id },
      { label: "External ID", count: coverage.with_external_id },
      { label: "National ID", count: coverage.with_national_id }
    ].sort(function (a, b) { return b.count - a.count; });

    renderChart("chart-identity-coverage", {
      type: "bar",
      data: {
        labels: channels.map(function (c) { return c.label; }),
        datasets: [{
          label: "Coverage %",
          data: channels.map(function (c) { return total ? Math.round((c.count / total) * 1000) / 10 : 0; }),
          backgroundColor: PALETTE[3]
        }]
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) { return ctx.parsed.x + "%"; } } }
        },
        scales: { x: { beginAtZero: true, max: 100 } }
      }
    });
  }

  function periodParams() {
    var days = C360.config.getDataPeriodDays("#overview-period-select");
    return { days: days };
  }

  function load() {
    var days = periodParams().days;
    $("#overview-loading").removeClass("hidden");
    $("#overview-content").empty();

    api("/reporting/summary", { days: days }).done(function (summary) {
      var vm = buildVm(summary);
      $("#overview-loading").addClass("hidden");
      $("#overview-content").html(C360.templates.render("overview-dashboard", vm));
      // The select is recreated by the render above -- restore the period the
      // user had picked instead of it snapping back to the template default.
      $("#overview-period-select").val(days);

      renderStatusFunnelChart(summary);
      renderDomainChart(summary);
      renderSourceSystemsChart(summary);

      api("/reporting/identity-graph/coverage", { days: days })
        .done(function (coverage) { renderIdentityCoverageChart(coverage); })
        .fail(function (xhr) { showApiError("loading identity graph coverage", xhr); });
    }).fail(function (xhr) {
      $("#overview-loading").addClass("hidden");
      showApiError("loading reporting overview", xhr);
    });
  }

  function bindEvents() {
    $(document).on("change", "#overview-period-select", load);
  }

  bindEvents();

  // Owns the "/overview" route (see router.js).
  C360.router.define("/overview", {
    section: "view-overview",
    tab: "overview",
    mount: load
  });

  C360.overviewView = { load: load };
})(window.C360);
