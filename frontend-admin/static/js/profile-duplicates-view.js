/* Customer 360 Admin -- "Duplicate Profiles" tab on the Master Profiles list
 * view (static/templates/profile/profiles-list.html).
 *
 * Moved here from the Overview dashboard (previously rendered as a static
 * Handlebars table -- see overview-view.js/overview-dashboard.html) so
 * duplicate (merged) master profiles get the same searchable/filterable
 * listing UX as the Master Profiles tab, without leaving the /profiles route.
 *
 * customer360-api's /reporting/master-profiles/duplicates endpoint returns a
 * plain list (skip/limit only, no total/pagination metadata), so this fetch
 * combines it with /reporting/summary's duplicate_master_profile_count for
 * the real total and builds the {items, pagination} shape C360.DataTableView
 * expects in server-paginated mode. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  function rowVm(p) {
    var displayName = fmt.realName(p) || p.persona_name || ("Profile " + fmt.shortId(p.master_profile_id));
    return $.extend({}, p, {
      displayName: displayName,
      initials: fmt.initials(displayName),
      shortId: fmt.shortId(p.master_profile_id),
      linkedRawProfileCountLabel: fmt.int(p.linked_raw_profile_count || 0),
      sourceSystemsLabel: (p.source_systems || []).join(", ") || "—"
    });
  }

  var COLUMNS = [
    { label: "Profile", type: "identity", nameField: "displayName", subField: "shortId", avatarField: "initials" },
    { label: "Domain", field: "domain", capitalize: true },
    { label: "Linked Raw Profiles", field: "linkedRawProfileCountLabel" },
    { label: "Source Systems", field: "sourceSystemsLabel", muted: true }
  ];

  var totalByDays = {}; // cached duplicate_master_profile_count per data-period, avoids refetching summary every page

  function unwrapAjaxResponse(response) {
    if (Array.isArray(response)) {
      return response[0] !== undefined ? response[0] : null;
    }
    return response || null;
  }

  function fetchTotal() {
    if (Object.prototype.hasOwnProperty.call(totalByDays, "all")) return $.Deferred().resolve(totalByDays["all"]).promise();
    return api("/reporting/summary").then(function (summaryRes) {
      var summary = unwrapAjaxResponse(summaryRes) || {};
      totalByDays["all"] = Number(summary.duplicate_master_profile_count || 0);
      return totalByDays["all"];
    });
  }

  var dtv = C360.DataTableView.create({
    columns: COLUMNS,
    pagination: true,
    limit: 20,
    rowVm: rowVm,
    rowId: function (vm) { return vm.master_profile_id; },
    rowSelectorClass: "duplicate-profile-row",
    resourceLabel: "duplicate profile",
    fetch: function (params) {
      var page = params.page || 1;
      var limit = params.page_size || 20;
      var skip = (page - 1) * limit;
      return $.when(
        api("/reporting/master-profiles/duplicates", { skip: skip, limit: limit }),
        fetchTotal()
      ).then(function (itemsRes, total) {
        var items = unwrapAjaxResponse(itemsRes) || [];
        var shown = skip + items.length;
        return {
          items: items,
          pagination: {
            page: page,
            page_size: limit,
            total: Number(total || 0),
            has_prev: page > 1,
            has_next: shown < Number(total || 0)
          }
        };
      });
    },
    onRowClick: function (id) { C360.router.navigate("/profiles/" + id); },
    onError: function (xhr) { showApiError("loading duplicate profiles", xhr); },
    el: {
      thead: "#duplicates-thead",
      tbody: "#duplicates-tbody",
      loading: "#duplicates-loading",
      empty: "#duplicates-empty",
      countLabel: "#duplicates-count-label",
      prevBtn: "#duplicates-btn-page-prev",
      nextBtn: "#duplicates-btn-page-next",
      pageLabel: "#duplicates-page-label"
    }
  });

  var loaded = false;
  function load(force) {
    if (loaded && !force) return;
    loaded = true;
    dtv.load(false);
  }

  function bindEvents() {
    dtv.bindRowClick();
    dtv.bindSearch("#duplicate-search-input", "q", 350);
    dtv.bindSelect("#duplicate-domain-filter", "domain");
    dtv.bindPagination();
  }

  // Left-tab switcher shared with profile-list-view.js's Master Profiles
  // tab -- both live inside the same /profiles route/card, so this is a
  // plain show/hide toggle rather than a router-owned route change.
  function bindTabs() {
    $(".profile-tab-btn").on("click", function () {
      var tab = $(this).data("profile-tab");
      $(".profile-tab-btn").removeClass("active");
      $(this).addClass("active");
      $("#tab-panel-master").toggleClass("hidden", tab !== "master");
      $("#tab-panel-duplicates").toggleClass("hidden", tab !== "duplicates");
      if (tab === "duplicates") load(false);
    });
  }

  C360.duplicateProfilesView = { load: load, bindEvents: bindEvents, bindTabs: bindTabs };
})(window.C360);
