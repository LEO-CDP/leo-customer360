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

  // Row VM for a SHARED persona archetype (customer360.cdp_persona_archetypes),
  // not a raw per-profile match row -- matched_profile_count is a real
  // COUNT(DISTINCT master_profile_id) maintained by a DB trigger, so no
  // client-side aggregation/hack is needed here.
  function personaRowVm(p) {
    var displayName = p.persona_name || p.persona_code || ("Persona " + fmt.shortId(p.persona_archetype_id));
    return $.extend({}, p, {
      displayName: displayName,
      subLabel: p.persona_code || fmt.shortId(p.persona_archetype_id),
      initials: fmt.initials(displayName),
      domainLabel: fmt.domainLabel(p.domain),
      categoryLabel: p.persona_category || "—",
      activeLabel: p.is_active ? "Active" : "Inactive",
      activeBadgeClass: p.is_active ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-600",
      matchedProfileCountLabel: fmt.int(p.matched_profile_count || 0),
      updatedAtLabel: fmt.dateTime(p.updated_at)
    });
  }

  var dtv = C360.DataTableView.create({
    columns: [
      { label: "Persona Archetype", type: "identity", nameField: "displayName", subField: "subLabel", avatarField: "initials" },
      { label: "Domain", field: "domainLabel" },
      { label: "Category", field: "categoryLabel" },
      { label: "Total Matched Profiles", type: "link", field: "matchedProfileCountLabel", valueField: "persona_archetype_id" },
      { label: "Status", type: "badge", field: "activeLabel", classField: "activeBadgeClass" },
      { label: "Updated At", field: "updatedAtLabel", muted: true }
    ],
    rowVm: personaRowVm,
    rowId: function (vm) { return vm.persona_archetype_id; },
    rowClickable: true,
    rowSelectorClass: "persona-row",
    resourceLabel: "persona archetype",
    fetch: function (params) {
      return api("/persona/archetypes", params);
    },
    onRowClick: function (personaArchetypeId) {
      C360.router.navigate("/personas/" + personaArchetypeId + "/matched-profiles");
    },
    onCellLink: function (personaArchetypeId) {
      if (personaArchetypeId) C360.router.navigate("/personas/" + personaArchetypeId + "/matched-profiles");
    },
    onError: function (xhr) { showApiError("loading persona archetypes", xhr); },
    el: {
      thead: "#personas-thead",
      tbody: "#personas-tbody",
      loading: "#personas-loading",
      empty: "#personas-empty",
      countLabel: "#personas-count-label",
      loadMoreBtn: "#btn-personas-load-more"
    }
  });

  // Matched-profiles drill-down (every master profile currently matched to
  // one archetype) -- reuses list-view.js's profile columns/rowVm, same
  // pattern as segments-view.js's matched-profiles sub-table.
  var currentArchetypeId = null;
  var categoryDtv = C360.DataTableView.create({
    columns: C360.listView.columns,
    pagination: true,
    rowVm: C360.listView.rowVm,
    rowId: function (vm) { return vm.master_profile_id; },
    rowSelectorClass: "profile-row",
    resourceLabel: "master profile",
    fetch: function (params) {
      return api("/persona/archetypes/" + currentArchetypeId + "/master-profiles", params);
    },
    onRowClick: function (id) { C360.router.navigate("/profiles/" + id); },
    onError: function (xhr) { showApiError("loading master profiles matched to this persona archetype", xhr); },
    el: {
      thead: "#persona-category-thead",
      tbody: "#persona-category-tbody",
      loading: "#persona-category-loading",
      empty: "#persona-category-empty",
      countLabel: "#persona-category-count-label",
      prevBtn: "#btn-persona-category-page-prev",
      nextBtn: "#btn-persona-category-page-next",
      pageLabel: "#persona-category-page-label"
    }
  });

  function showPersonaList() {
    $("#persona-view-category-detail").addClass("hidden");
    $("#persona-view-list").removeClass("hidden");
  }

  function showArchetypeMatchedProfiles(personaArchetypeId) {
    currentArchetypeId = personaArchetypeId;
    $("#persona-view-list").addClass("hidden");
    $("#persona-view-category-detail").removeClass("hidden");
    api("/persona/archetypes/" + personaArchetypeId)
      .done(function (archetype) {
        $("#persona-category-detail-title").text((archetype && archetype.persona_name) || personaArchetypeId);
      })
      .fail(function () {
        $("#persona-category-detail-title").text(personaArchetypeId);
      });
    categoryDtv.load(false);
  }

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
    // Persona Management must show shared ARCHETYPES, not raw per-profile
    // match rows -- "Total Personas" reflects total_archetypes.
    $("#persona-kpi-total").text(fmt.int(summary.total_archetypes || 0));
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

  function loadAnalytics(refreshTable) {
    return api("/persona/analytics/summary", analyticsParams())
      .done(function (summary) {
        summary = summary || {};
        updateKpis(summary);
        renderCategoryChart(summary);
        renderRiskChart(summary);
        if (refreshTable) dtv.load(false);
      })
      .fail(function (xhr) {
        showApiError("loading persona analytics", xhr);
      });
  }

  function load() {
    showPersonaList();
    loadAnalytics(true);
  }

  function bindEvents() {
    dtv.bindRowClick();
    dtv.bindLoadMore();
    categoryDtv.bindRowClick();
    categoryDtv.bindPagination();

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

    $("#btn-back-to-personas").on("click", function () { C360.router.navigate("/personas"); });
  }

  C360.router.define("/personas", {
    section: "view-personas",
    tab: "personas",
    mount: function () { load(); }
  });

  C360.router.define("/personas/:archetypeId/matched-profiles", {
    section: "view-personas",
    tab: "personas",
    mount: function (params) { showArchetypeMatchedProfiles(decodeURIComponent(params.archetypeId)); }
  });

  // Backward compatibility for old deep links.
  C360.router.redirect("/journeys", "/personas");

  C360.personaManagementView = { load: load, bindEvents: bindEvents };
})(window.C360);
