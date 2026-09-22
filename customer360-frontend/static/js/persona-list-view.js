/* Customer 360 Admin -- Persona Management + Analytics view. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  function populatePersonaCategoryOptions(selectedValue) {
    var $selects = $("#persona-add-category, #persona-edit-category");
    var options = C360.config.personaCategoryOptions || [];
    $selects.each(function () {
      var $select = $(this);
      var value = selectedValue || $select.val() || "";
      $select.find("option:not(:first)").remove();
      options.forEach(function (option) {
        $select.append($("<option>").val(option.value).text(option.label));
      });
      if (value && !options.some(function (option) { return option.value === value; })) {
        $select.append($("<option>").val(value).text(value));
      }
      $select.val(value);
    });
  }

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
    columns: C360.profileListView.columns,
    pagination: true,
    rowVm: C360.profileListView.rowVm,
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

  // Last-loaded archetype backing the metadata card + edit modal (its full
  // PersonaArchetypeRead payload, keyed by nothing since only one is ever
  // shown at a time on this drill-down route).
  var currentArchetype = null;

  var CENTROID_FIELDS = [
    { key: "centroid_behavior_score", label: "Behavior" },
    { key: "centroid_engagement_score", label: "Engagement" },
    { key: "centroid_financial_score", label: "Financial" },
    { key: "centroid_loyalty_score", label: "Loyalty" },
    { key: "centroid_relationship_score", label: "Relationship" },
    { key: "centroid_risk_score", label: "Risk" }
  ];

  function renderArchetypeMetadata(archetype) {
    currentArchetype = archetype;
    var displayName = (archetype && archetype.persona_name) || currentArchetypeId;
    $("#persona-category-detail-title").text(displayName);
    $("#persona-category-detail-title-2").text(displayName);
    $("#persona-detail-code").text((archetype && archetype.persona_code) || "—");
    $("#persona-detail-summary").text((archetype && archetype.persona_summary) || "No summary available.");
    $("#persona-detail-domain").text(fmt.domainLabel(archetype && archetype.domain));
    $("#persona-detail-category").text((archetype && archetype.persona_category) || "—");
    $("#persona-detail-matched-count").text(fmt.int(archetype && archetype.matched_profile_count));
    $("#persona-detail-updated-at").text(fmt.dateTime(archetype && archetype.updated_at));
    $("#persona-detail-llm-provider").text((archetype && archetype.llm_provider) || "—");
    $("#persona-detail-llm-model").text((archetype && archetype.llm_model) || "—");

    var isActive = !!(archetype && archetype.is_active);
    $("#persona-detail-status-badge")
      .text(isActive ? "Active" : "Inactive")
      .attr("class", "text-xs font-semibold px-2.5 py-1 rounded-full " + (isActive ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-600"));

    var $centroids = $("#persona-detail-centroids").empty();
    CENTROID_FIELDS.forEach(function (f) {
      var value = archetype ? archetype[f.key] : null;
      $centroids.append(
        $("<div>").addClass("rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2 text-center").append(
          $("<div>").addClass("text-[10px] uppercase tracking-wide text-slate-400 font-semibold").text(f.label),
          $("<div>").addClass("text-sm font-bold text-slate-800 mt-0.5").text(fmt.score(value))
        )
      );
    });

    // Editing a shared archetype affects how EVERY current/future profile in
    // its domain gets lookalike-matched -- gate the control behind the
    // 'admin' role client-side (UX only; the API re-checks server-side).
    $("#btn-persona-edit").toggleClass("hidden", !C360.config.isAdmin());
  }

  function showArchetypeMatchedProfiles(personaArchetypeId) {
    currentArchetypeId = personaArchetypeId;
    $("#persona-view-list").addClass("hidden");
    $("#persona-view-category-detail").removeClass("hidden");
    api("/persona/archetypes/" + personaArchetypeId)
      .done(function (archetype) { renderArchetypeMetadata(archetype); })
      .fail(function () { renderArchetypeMetadata(null); });
    categoryDtv.load(false);
  }

  function openPersonaEditModal() {
    if (!currentArchetype) return;
    $("#persona-edit-error").addClass("hidden").text("");
    populatePersonaCategoryOptions(currentArchetype.persona_category || "");
    $("#persona-edit-name").val(currentArchetype.persona_name || "");
    $("#persona-edit-category").val(currentArchetype.persona_category || "");
    $("#persona-edit-summary").val(currentArchetype.persona_summary || "");
    $("#persona-edit-llm-provider").val(currentArchetype.llm_provider || "");
    $("#persona-edit-llm-model").val(currentArchetype.llm_model || "");
    $("#persona-edit-active").prop("checked", !!currentArchetype.is_active);
    $("#persona-edit-modal").removeClass("hidden");
  }

  function closePersonaEditModal() {
    $("#persona-edit-modal").addClass("hidden");
  }

  function submitPersonaEditForm() {
    var $error = $("#persona-edit-error");
    $error.addClass("hidden").text("");

    var name = $.trim($("#persona-edit-name").val());
    if (!name) {
      $error.removeClass("hidden").text("Persona Name is required.");
      return;
    }

    var payload = {
      persona_name: name,
      persona_category: $.trim($("#persona-edit-category").val()) || null,
      persona_summary: $.trim($("#persona-edit-summary").val()) || null,
      llm_provider: $.trim($("#persona-edit-llm-provider").val()) || null,
      llm_model: $.trim($("#persona-edit-llm-model").val()) || null,
      is_active: $("#persona-edit-active").is(":checked")
    };

    api("/persona/archetypes/" + currentArchetypeId, payload, "PATCH")
      .done(function (archetype) {
        closePersonaEditModal();
        renderArchetypeMetadata(archetype);
        dtv.load(false);
        notifyCentroidRecompute(archetype);
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        $error.removeClass("hidden").text(detail || "Could not save changes to this persona archetype.");
      });
  }

  // "Add New Persona" -- creates a brand-new shared archetype (POST
  // /persona/archetypes). Admin-only, mirrors openPersonaEditModal's shape
  // but resets all fields since there's no existing row to seed from.
  function openPersonaAddModal() {
    $("#persona-add-error").addClass("hidden").text("");
    populatePersonaCategoryOptions("");
    $("#persona-add-domain").val("");
    $("#persona-add-code").val("");
    $("#persona-add-name").val("");
    $("#persona-add-category").val("");
    $("#persona-add-summary").val("");
    $("#persona-add-llm-provider").val("");
    $("#persona-add-llm-model").val("");
    $("#persona-add-active").prop("checked", true);
    $("#persona-add-modal").removeClass("hidden");
  }

  function closePersonaAddModal() {
    $("#persona-add-modal").addClass("hidden");
  }

  function submitPersonaAddForm() {
    var $error = $("#persona-add-error");
    $error.addClass("hidden").text("");

    var domain = $.trim($("#persona-add-domain").val());
    var code = $.trim($("#persona-add-code").val());
    var name = $.trim($("#persona-add-name").val());
    if (!domain || !code || !name) {
      $error.removeClass("hidden").text("Domain, Persona Code, and Persona Name are required.");
      return;
    }

    var payload = {
      tenant_id: C360.config.current.tenantId,
      domain: domain,
      persona_code: code,
      persona_name: name,
      persona_category: $.trim($("#persona-add-category").val()) || null,
      persona_summary: $.trim($("#persona-add-summary").val()) || null,
      llm_provider: $.trim($("#persona-add-llm-provider").val()) || null,
      llm_model: $.trim($("#persona-add-llm-model").val()) || null,
      is_active: $("#persona-add-active").is(":checked")
    };

    api("/persona/archetypes", payload, "POST")
      .done(function (archetype) {
        closePersonaAddModal();
        dtv.load(false);
        loadAnalytics(false);
        notifyCentroidRecompute(archetype);
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : "Could not create this persona archetype.");
      });
  }

  // Surfaces whether saving this archetype successfully triggered
  // identity_resolution_job to (re)compute its centroid_*_score (see
  // core/routers/persona_api.py's _trigger_persona_centroid_recompute) --
  // a soft, best-effort signal only: a missing run_id (Dagster webserver
  // unreachable) never blocks the create/update itself, so just inform the
  // admin the centroid may stay stale until the next scheduled CIR run.
  function notifyCentroidRecompute(archetype) {
    if (archetype && archetype.centroid_recompute_run_id) {
      showToast(
        "Persona Resolution Engine recompute submitted (run " + archetype.centroid_recompute_run_id + ")…",
        "info"
      );
    } else {
      showToast(
        "Saved, but could not trigger the centroid recompute job -- scores may be stale until the next scheduled run.",
        "warning"
      );
    }
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
    // Creating a shared archetype affects future lookalike-matching for every
    // profile in its domain -- gate client-side like btn-persona-edit (the
    // API's require_admin dependency is the real, fail-closed enforcement).
    $("#btn-persona-add").toggleClass("hidden", !C360.config.isAdmin());
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

    $("#btn-back-to-personas").on("click", function () { C360.router.navigate("/personas"); });

    $("#btn-persona-edit").on("click", openPersonaEditModal);
    $("#btn-persona-edit-cancel").on("click", closePersonaEditModal);
    $("#btn-persona-edit-save").on("click", submitPersonaEditForm);

    $("#btn-persona-add").on("click", openPersonaAddModal);
    $("#btn-persona-add-cancel").on("click", closePersonaAddModal);
    $("#btn-persona-add-save").on("click", submitPersonaAddForm);
    $("#persona-add-modal").on("click", function (e) {
      if (e.target === this) closePersonaAddModal();
    });
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
