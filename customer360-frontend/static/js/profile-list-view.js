/* Customer 360 Admin -- Master Profiles list view.
 *
 * This view (and segments-view.js/attributes-view.js) is a thin config
 * layer on top of the shared C360.DataTableView component
 * (static/js/data-table-view.js): it only supplies *what* a profile row
 * looks like (columns, row-vm formatting, API path, DOM hooks) -- paging,
 * filter wiring, loading/empty states and "load more" are all owned by
 * that shared component. `columns`/`rowVm` are exported so
 * segments-view.js's "matched profiles" sub-table can render identical
 * profile rows without duplicating this config. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  function rowVm(p) {
    // Real (plaintext) name wins when available; hashed domains (banking)
    // fall back to the AI-computed persona name, then a generic label.
    var displayName = fmt.realName(p) || p.persona_name || ("Profile " + fmt.shortId(p.master_profile_id));
    return $.extend({}, p, {
      displayName: displayName,
      initials: fmt.initials(displayName),
      shortId: fmt.shortId(p.master_profile_id),
      domainLabel: fmt.domainLabel(p.domain),
      domainIcon: fmt.domainIcon(p.domain),
      domainIconBg: fmt.domainIconBg(p.domain),
      tierLabel: fmt.titleCase(p.membership_tier || p.clv_segment) || "—",
      tierIcon: "🏷️",
      lastActivityIcon: "🕒",
      lifecycleLabel: fmt.titleCase(p.lifecycle_stage) || "—",
      lifecycleBadgeClass: fmt.lifecycleBadgeClass(p.lifecycle_stage),
      churnBadgeClass: fmt.churnBadgeClass(p.churn_risk_tier),
      linkedRawProfileCountLabel: fmt.int(p.linked_raw_profile_count || 0),
      trackedEventsLabel: p.total_tracked_events === null || p.total_tracked_events === undefined
        ? "—"
        : fmt.int(p.total_tracked_events),
      clvLabel: (p.predictive_clv !== null && p.predictive_clv !== undefined) ? fmt.money(p.predictive_clv, "") : "—",
      engagementLabel: (p.engagement_score !== null && p.engagement_score !== undefined) ? fmt.score(p.engagement_score) : "—",
      lastActivityLabel: p.last_activity_at ? fmt.dateTime(p.last_activity_at) : "—"
    });
  }

  // The declarative "flexible fields" column set for a profile row -- reused
  // as-is by segments-view.js for the segment-detail "Matched Profiles" table.
  var COLUMNS = [
    { label: "Profile", type: "identity", nameField: "displayName", subField: "shortId", avatarField: "initials" },
    { label: "Domain", type: "identity", nameField: "domainLabel", avatarField: "domainIcon", avatarBgField: "domainIconBg", avatarColor: "text-slate-600", compact: true },
    { label: "Tier", type: "identity", nameField: "tierLabel", avatarField: "tierIcon", avatarBg: "bg-amber-100", avatarColor: "text-amber-700", compact: true },
    { label: "Lifecycle", type: "badge", field: "lifecycleLabel", classField: "lifecycleBadgeClass" },
    { label: "Churn Risk", type: "badge", field: "churn_risk_tier", classField: "churnBadgeClass" },
    { label: "Linked Profiles", field: "linkedRawProfileCountLabel" },
    { label: "Total Events", field: "trackedEventsLabel" },
    { label: "Last Activity", type: "identity", nameField: "lastActivityLabel", avatarField: "lastActivityIcon", avatarBg: "bg-slate-100", avatarColor: "text-slate-500", compact: true }
  ];

  function buildListParams(params) {
    var query = params || {};
    return query;
  }

  var dataSourcesLoadedForTenant = null;

  function loadDataSources() {
    var $select = $("#data-source-filter");
    var tenantId = C360.config.current && C360.config.current.tenantId;
    if (!$select.length || !tenantId || dataSourcesLoadedForTenant === tenantId) {
      return $.Deferred().resolve().promise();
    }

    $select.prop("disabled", true);
    return api("/metadata/data-sources", {
      tenant_id: tenantId,
      status: 1,
      skip: 0,
      limit: 1000
    }).done(function (sources) {
      var current = $select.val();
      var items = Array.isArray(sources) ? sources.slice() : [];
      items.sort(function (left, right) {
        return String(left.name || left.slug || "").localeCompare(String(right.name || right.slug || ""));
      });
      $select.find("option:not(:first)").remove();
      items.forEach(function (source) {
        var label = source.name || source.slug || source.data_source_id;
        if (source.slug && source.name && source.slug !== source.name) {
          label += " (" + source.slug + ")";
        }
        $select.append($("<option></option>").attr("value", source.data_source_id).text(label));
      });
      if (current && $select.find("option[value='" + current + "']").length) {
        $select.val(current);
      } else {
        $select.val("");
      }
      dataSourcesLoadedForTenant = tenantId;
    }).fail(function (xhr) {
      showApiError("loading data sources", xhr);
    }).always(function () {
      $select.prop("disabled", false);
    });
  }

  var dtv = C360.DataTableView.create({
    columns: COLUMNS,
    pagination: true,
    rowVm: rowVm,
    rowId: function (vm) { return vm.master_profile_id; },
    rowSelectorClass: "profile-row",
    resourceLabel: "profile",
    fetch: function (params) { return api("/master-profiles/", buildListParams(params)); },
    onRowClick: function (id) { C360.router.navigate("/profiles/" + id); },
    onError: function (xhr) { showApiError("loading profiles", xhr); },
    el: {
      thead: "#profiles-thead",
      tbody: "#profiles-tbody",
      loading: "#list-loading",
      empty: "#list-empty",
      countLabel: "#list-count-label",
      loadMoreBtn: "#btn-load-more",
      prevBtn: "#btn-page-prev",
      nextBtn: "#btn-page-next",
      pageLabel: "#list-page-label"
    }
  });

  function load(append) { return dtv.load(append); }

  function bindEvents() {
    dtv.bindRowClick();
    dtv.bindSearch("#search-input", "q", 350);
    dtv.bindSelect("#domain-filter", "domain");
    dtv.bindSelect("#data-source-filter", "data_source_id");
    dtv.bindSelect("#lifecycle-filter", "lifecycle_stage");
    dtv.bindSelect("#tier-filter", "clv_segment");
    dtv.bindSelect("#churn-risk-filter", "churn_risk_tier");
    $("#linked-raw-profile-count-input").on("input change", function () {
      var raw = $(this).val();
      if (raw === "" || raw === null || raw === undefined) {
        dtv.setFilter("linked_raw_profile_count_min", "");
        return;
      }
      var n = parseInt(raw, 10);
      dtv.setFilter("linked_raw_profile_count_min", isNaN(n) ? "" : String(Math.max(0, n)));
    });
    dtv.bindPagination();
  }

  // Owns the "/profiles" listing route (see router.js). The row-click
  // handler above navigates to "/profiles/:id", which profile-detail-view.js
  // owns.
  C360.router.define("/profiles", {
    section: "view-list",
    tab: "profiles",
    mount: function () {
      loadDataSources();
      load(false);
    }
  });

  C360.profileListView = {
    load: load,
    loadDataSources: loadDataSources,
    bindEvents: bindEvents,
    rowVm: rowVm,
    columns: COLUMNS
  };
})(window.C360);
