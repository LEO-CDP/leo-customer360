/* Customer 360 Admin -- Attribute Catalog view (cdp_profile_attributes).
 *
 * Third real consumer of the shared C360.DataTableView component (see
 * static/js/data-table-view.js, and list-view.js/segments-view.js for the
 * other two) -- proves the component is genuinely reusable across
 * differently-shaped entities, not just profiles/segments. The generic
 * CRUD router behind /profile-attributes/ (customer360-api) only supports
 * skip/limit/tenant_id query params, so this view fetches one page
 * client-side and lets the component filter/search it in the browser
 * (`clientSide: true`) rather than round-tripping to the API per keystroke. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  function yesNoBadgeClass(v) { return v ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500"; }
  function statusBadgeClass(v) { return v === "ACTIVE" ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500"; }

  // Conversion-related attributes have no dedicated boolean column -- the
  // LEAD_SCORING group (lead_conversion_probability/lead_grade) is what
  // identifies them today.
  function isConversionAttribute(a) { return a.attribute_group === "LEAD_SCORING"; }

  function rowVm(a) {
    return $.extend({}, a, {
      groupIcon: (fmt.CATEGORY_ICONS && fmt.CATEGORY_ICONS[a.attribute_group]) || "🔑",
      groupLabel: fmt.titleCase(a.attribute_group),
      domainLabel: fmt.domainLabel(a.domain_scope),
      dataTypeLabel: fmt.titleCase(a.data_type),
      piiLabel: a.is_pii ? "PII" : "Not PII",
      piiBadgeClass: yesNoBadgeClass(a.is_pii),
      segmentableLabel: a.is_segmentable ? "Segmentable" : "Not segmentable",
      segmentableBadgeClass: yesNoBadgeClass(a.is_segmentable),
      cirLabel: a.is_identity_resolution ? "CIR" : "—",
      cirBadgeClass: yesNoBadgeClass(a.is_identity_resolution),
      conversionLabel: isConversionAttribute(a) ? "Conversion" : "—",
      conversionBadgeClass: yesNoBadgeClass(isConversionAttribute(a)),
      statusLabel: fmt.titleCase(a.status),
      statusBadgeClass: statusBadgeClass(a.status)
    });
  }

  var dtv = C360.DataTableView.create({
    columns: [
      {
        label: "Attribute", type: "identity", nameField: "name", subField: "attribute_internal_code", subStyle: "tag",
        avatarField: "groupIcon", avatarBg: "bg-slate-100", avatarColor: "text-slate-600", avatarTextClass: "text-base"
      },
      { label: "Group", field: "groupLabel" },
      { label: "Domain", field: "domainLabel" },
      { label: "Data Type", field: "dataTypeLabel" },
      { label: "PII", type: "badge", field: "piiLabel", classField: "piiBadgeClass" },
      { label: "Segmentable", type: "badge", field: "segmentableLabel", classField: "segmentableBadgeClass" },
      { label: "CIR", type: "badge", field: "cirLabel", classField: "cirBadgeClass" },
      { label: "Conversion", type: "badge", field: "conversionLabel", classField: "conversionBadgeClass" },
      { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" }
    ],
    rowVm: rowVm,
    rowClickable: false, // no attribute detail/editor page yet -- read-only catalog
    resourceLabel: "attribute",
    clientSide: true,
    clientSideLimit: 500,
    fetch: function (params) { return api("/profile-attributes/", params); },
    clientFilters: {
      q: function (vm, value) {
        var needle = value.toLowerCase();
        return (vm.name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.attribute_internal_code || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.description || "").toLowerCase().indexOf(needle) !== -1;
      },
      group: function (vm, value) { return vm.attribute_group === value; },
      domain: function (vm, value) { return vm.domain_scope === value; }
    },
    onFetched: populateFilterOptions,
    onError: function (xhr) { showApiError("loading attributes", xhr); },
    el: {
      thead: "#attributes-thead",
      tbody: "#attributes-tbody",
      loading: "#attributes-loading",
      empty: "#attributes-empty",
      countLabel: "#attributes-count-label"
    }
  });

  // Populates the "group" filter <select> from whatever attribute_group
  // values actually came back, instead of hardcoding the list -- another
  // small example of the shared component not needing to know the shape
  // of the data it's rendering ahead of time.
  function populateFilterOptions(items) {
    var $select = $("#attributes-group-filter");
    if ($select.children().length > 1) return; // already populated
    var seen = {};
    var groups = [];
    items.forEach(function (a) {
      if (a.attribute_group && !seen[a.attribute_group]) { seen[a.attribute_group] = true; groups.push(a.attribute_group); }
    });
    groups.sort();
    groups.forEach(function (g) {
      $select.append($("<option>").val(g).text(fmt.titleCase(g)));
    });
  }

  function load() { return dtv.load(false); }

  function bindEvents() {
    dtv.bindSearch("#attributes-search-input", "q", 300);
    dtv.bindSelect("#attributes-group-filter", "group");
    dtv.bindSelect("#attributes-domain-filter", "domain");
  }

  // Owns the "/attributes" tab/route (see router.js).
  C360.router.define("/attributes", {
    section: "view-attributes",
    tab: "attributes",
    mount: function () { load(); }
  });

  C360.attributesView = { load: load, bindEvents: bindEvents };
})(window.C360);
