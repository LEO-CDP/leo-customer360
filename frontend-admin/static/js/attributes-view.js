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

  // cdp_domain_profiles-sourced attributes (JSONB keys in domain_attributes,
  // e.g. risk_segment/membership_tier) are now segmentable via the LATERAL
  // join added to every cdp_segments query (see core/crud/segmentation.py's
  // DOMAIN_ATTRIBUTES_JOIN_SQL) -- shown here as a short "Domain Profile"
  // label so it's obvious a given row isn't a plain cdp_master_profiles column.
  function sourceLabel(v) { return v === "cdp_domain_profiles" ? "Domain Profile" : "Master Profile"; }
  function sourceBadgeClass(v) { return v === "cdp_domain_profiles" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500"; }

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
      sourceLabel: sourceLabel(a.source_table),
      sourceBadgeClass: sourceBadgeClass(a.source_table),
      piiLabel: a.is_pii ? "PII" : "Not PII",
      piiBadgeClass: yesNoBadgeClass(a.is_pii),
      segmentableLabel: a.is_segmentable ? "Segmentable" : "Not segmentable",
      segmentableBadgeClass: yesNoBadgeClass(a.is_segmentable),
      cirLabel: a.is_identity_resolution ? "CIR" : "—",
      cirBadgeClass: yesNoBadgeClass(a.is_identity_resolution),
      // Priority rank only matters for active CIR matching keys (see database-schema.sql
      // cdp_profile_attributes.priority_rank); non-CIR attributes show a dash.
      priorityLabel: a.is_identity_resolution ? ("Rank " + a.priority_rank) : "—",
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
      { label: "Source", type: "badge", field: "sourceLabel", classField: "sourceBadgeClass" },
      { label: "Data Type", field: "dataTypeLabel" },
      { label: "PII", type: "badge", field: "piiLabel", classField: "piiBadgeClass" },
      { label: "Segmentable", type: "badge", field: "segmentableLabel", classField: "segmentableBadgeClass" },
      { label: "CIR", type: "badge", field: "cirLabel", classField: "cirBadgeClass" },
      { label: "Priority", field: "priorityLabel" },
      { label: "Conversion", type: "badge", field: "conversionLabel", classField: "conversionBadgeClass" },
      { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" }
    ],
    rowVm: rowVm,
    rowClickable: false, // no attribute detail/editor page yet -- read-only catalog
    onEdit: function (id) { openEditAttributeModal(id); },
    editLabel: "Edit",
    resourceLabel: "attribute",
    clientSide: true,
    clientSideLimit: 500,
    // Sort priority: CIR first (by priority_rank), then PII, then Conversion;
    // stable sort keeps everything else in its original (display_order) sequence.
    fetch: function (params) {
      return api("/profile-attributes/", params).then(function (items) {
        return items.slice().sort(function (a, b) {
          return (b.is_identity_resolution ? 1 : 0) - (a.is_identity_resolution ? 1 : 0) ||
            (a.priority_rank || 99) - (b.priority_rank || 99) ||
            (b.is_pii ? 1 : 0) - (a.is_pii ? 1 : 0) ||
            (isConversionAttribute(b) ? 1 : 0) - (isConversionAttribute(a) ? 1 : 0);
        });
      });
    },
    clientFilters: {
      q: function (vm, value) {
        var needle = value.toLowerCase();
        return (vm.name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.attribute_internal_code || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.description || "").toLowerCase().indexOf(needle) !== -1;
      },
      group: function (vm, value) { return vm.attribute_group === value; },
      domain: function (vm, value) { return vm.domain_scope === value; },
      source: function (vm, value) { return vm.source_table === value; }
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
  var attributesById = {}; // last-fetched rows, keyed by id -- backs the Edit modal

  function populateFilterOptions(items) {
    attributesById = {};
    items.forEach(function (a) { attributesById[a.id] = a; });

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

  // Non-null while the modal is editing an existing row (its `id`, the
  // generic CRUD router's primary key) -- null means "creating a new row".
  var editingAttributeId = null;

  function openAddAttributeModal() {
    editingAttributeId = null;
    $("#attribute-form-title").text("Add Attribute");
    $("#attribute-form-subtitle").text("Registers a new row in the cdp_profile_attributes catalog");
    $("#attribute-form-save-label").text("Save Attribute");
    $("#attribute-add-error").addClass("hidden").text("");
    $("#attribute-add-code").val("").prop("disabled", false);
    $("#attribute-add-name").val("");
    $("#attribute-add-description").val("");
    $("#attribute-add-group").val("GENERAL");
    $("#attribute-add-source").val("cdp_domain_profiles");
    $("#attribute-add-domain-scope").val("all");
    $("#attribute-add-data-type").val("TEXT");
    $("#attribute-add-is-pii").prop("checked", false);
    $("#attribute-add-is-segmentable").prop("checked", true);
    $("#attribute-form-modal").removeClass("hidden");
  }

  function openEditAttributeModal(id) {
    var a = attributesById[id];
    if (!a) return;
    editingAttributeId = id;
    $("#attribute-form-title").text("Edit Attribute");
    $("#attribute-form-subtitle").text("Updates this cdp_profile_attributes catalog row");
    $("#attribute-form-save-label").text("Save Changes");
    $("#attribute-add-error").addClass("hidden").text("");
    // attribute_internal_code is the matching-rule identity key and isn't
    // part of ProfileAttributeUpdate -- shown for context but not editable.
    $("#attribute-add-code").val(a.attribute_internal_code).prop("disabled", true);
    $("#attribute-add-name").val(a.name);
    $("#attribute-add-description").val(a.description || "");
    $("#attribute-add-group").val(a.attribute_group);
    $("#attribute-add-source").val(a.source_table);
    $("#attribute-add-domain-scope").val(a.domain_scope);
    $("#attribute-add-data-type").val(a.data_type);
    $("#attribute-add-is-pii").prop("checked", !!a.is_pii);
    $("#attribute-add-is-segmentable").prop("checked", !!a.is_segmentable);
    $("#attribute-form-modal").removeClass("hidden");
  }

  function closeAddAttributeModal() {
    $("#attribute-form-modal").addClass("hidden");
  }

  function submitAddAttributeForm() {
    var $error = $("#attribute-add-error");
    $error.addClass("hidden").text("");

    var code = $.trim($("#attribute-add-code").val());
    var name = $.trim($("#attribute-add-name").val());
    if (!code || !name) {
      $error.removeClass("hidden").text("Attribute Code and Display Name are required.");
      return;
    }

    var isEdit = editingAttributeId !== null;
    var payload = {
      name: name,
      description: $.trim($("#attribute-add-description").val()) || null,
      attribute_group: $("#attribute-add-group").val(),
      source_table: $("#attribute-add-source").val(),
      domain_scope: $("#attribute-add-domain-scope").val(),
      data_type: $("#attribute-add-data-type").val(),
      is_pii: $("#attribute-add-is-pii").is(":checked"),
      is_segmentable: $("#attribute-add-is-segmentable").is(":checked")
    };
    // attribute_internal_code is only settable on create (ProfileAttributeUpdate
    // has no such field -- it's the immutable matching-rule identity key).
    if (!isEdit) payload.attribute_internal_code = code;

    var request = isEdit
      ? api("/profile-attributes/" + editingAttributeId, payload, "PATCH")
      : api("/profile-attributes/", payload, "POST");

    request
      .done(function () {
        closeAddAttributeModal();
        load();
      })
      .fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || ("Could not " + (isEdit ? "update" : "create") + " attribute.");
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : JSON.stringify(detail));
      });
  }

  function bindEvents() {
    dtv.bindSearch("#attributes-search-input", "q", 300);
    dtv.bindSelect("#attributes-group-filter", "group");
    dtv.bindSelect("#attributes-domain-filter", "domain");
    dtv.bindSelect("#attributes-source-filter", "source");
    dtv.bindRowEdit();

    $(document).on("click", "#btn-attributes-add", openAddAttributeModal);
    $(document).on("click", "#btn-attribute-add-cancel", closeAddAttributeModal);
    $(document).on("click", "#btn-attribute-add-save", submitAddAttributeForm);
    $(document).on("click", "#attribute-form-modal", function (e) {
      if (e.target === this) closeAddAttributeModal();
    });
  }

  // Owns the "/attributes" tab/route (see router.js).
  C360.router.define("/attributes", {
    section: "view-attributes",
    tab: "attributes",
    mount: function () { load(); }
  });

  C360.attributesView = { load: load, bindEvents: bindEvents };
})(window.C360);
