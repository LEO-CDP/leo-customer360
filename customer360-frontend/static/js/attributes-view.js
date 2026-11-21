/* Customer 360 Admin -- unified profile and behavioral attribute catalog. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var activeSection = "profile";
  var editingId = null;
  var pendingDelete = null;
  var profileById = {};
  var behaviorById = {};
  var sourceLabels = {
    cdp_domain_profiles: "Domain Profile",
    cdp_raw_profiles_stage: "Raw Profile",
    cdp_master_profiles: "Master Profile"
  };

  function canManageAttributes() {
    var user = C360.config.currentUser();
    var allowedRoles = ["root", "platform_admin", "super_admin", "admin"];
    if (user && user.isRoot) return true;
    return !!user && (user.roles || []).some(function (role) {
      return allowedRoles.indexOf(String(role).trim().toLowerCase()) !== -1;
    });
  }

  function statusClass(value) {
    return value === "ACTIVE" ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500";
  }

  function dateLabel(value) {
    return value ? String(value).slice(0, 10) : "—";
  }

  function profileRow(a) {
    var conversion = a.attribute_group === "LEAD_SCORING";
    return $.extend({}, a, {
      groupIcon: (fmt.CATEGORY_ICONS && fmt.CATEGORY_ICONS[a.attribute_group]) || "🔑",
      attributeIcon: "bi-person-vcard",
      codeLabel: a.attribute_internal_code,
      groupLabel: fmt.titleCase(a.attribute_group),
      domainLabel: fmt.domainLabel(a.domain_scope),
      sourceLabel: sourceLabels[a.source_table] || "Master Profile",
      piiLabel: a.is_pii ? "PII" : "Not PII",
      piiClass: a.is_pii ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500",
      segmentableLabel: a.is_segmentable ? "Yes" : "No",
      segmentableClass: a.is_segmentable ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500",
      priorityLabel: a.is_identity_resolution ? "Rank " + (a.priority_rank || 99) : "—",
      conversionLabel: conversion ? "Conversion" : "—",
      conversionClass: conversion ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-500",
      statusLabel: fmt.titleCase(a.status),
      statusClass: statusClass(a.status),
      updatedLabel: dateLabel(a.updated_at)
    });
  }

  function behaviorRow(event) {
    return $.extend({}, event, {
      eventIcon: event.is_conversion_default ? "⚡" : "⌁",
      behaviorIcon: event.is_conversion_default ? "bi-lightning-charge" : "bi-activity",
      categoryLabel: fmt.titleCase(event.event_category),
      domainLabel: fmt.domainLabel(event.domain_scope),
      eventTypeLabel: event.is_conversion_default ? "Conversion" : "Activity",
      eventTypeClass: event.is_conversion_default ? "bg-violet-100 text-violet-700" : "bg-sky-100 text-sky-700",
      statusLabel: fmt.titleCase(event.status),
      statusClass: statusClass(event.status),
      valueFieldLabel: event.value_field || "—",
      updatedLabel: dateLabel(event.updated_at)
    });
  }

  function baseTableOptions() {
    return {
      rowClickable: false,
      editLabel: "Edit",
      secondaryActionLabel: "Delete",
      secondaryActionIcon: "bi-trash3",
      secondaryActionClass: "text-red-500 hover:text-red-700",
      secondaryActionButtonClass: "border-red-100 bg-red-50 text-red-600 hover:border-red-200 hover:bg-red-100 hover:text-red-700 focus:ring-red-400",
      actionsVisible: function () { return canManageAttributes(); },
      canEdit: function () { return canManageAttributes(); },
      canSecondaryAction: function () { return canManageAttributes(); },
      clientSide: true,
      clientSideLimit: 1000,
      compactTable: true,
      compactActions: true,
      el: {
        thead: "#attributes-thead",
        tbody: "#attributes-tbody",
        loading: "#attributes-loading",
        empty: "#attributes-empty",
        countLabel: "#attributes-count-label"
      },
      onError: function (xhr) {
        showApiError("loading " + activeSection + " attributes", xhr);
      }
    };
  }

  var profileTable = C360.DataTableView.create($.extend(baseTableOptions(), {
    columns: [
      { label: "Attribute", icon: "bi-person-vcard", type: "identity", nameField: "name", subField: "codeLabel", subStyle: "tag", descriptionField: "description", avatarIconField: "attributeIcon", avatarBg: "bg-indigo-50", avatarColor: "text-indigo-600", avatarTextClass: "text-base" },
      { label: "Group", icon: "bi-collection", field: "groupLabel" },
      { label: "Domain", icon: "bi-globe2", field: "domainLabel" },
      { label: "Data Type", icon: "bi-braces", field: "data_type" },
      { label: "PII", icon: "bi-shield-lock", type: "badge", field: "piiLabel", classField: "piiClass" },
      { label: "Segmentable", icon: "bi-funnel", type: "badge", field: "segmentableLabel", classField: "segmentableClass" },
      { label: "Priority", icon: "bi-sort-numeric-down", field: "priorityLabel" },
      { label: "Status", icon: "bi-circle-half", type: "badge", field: "statusLabel", classField: "statusClass" },
      { label: "Updated", icon: "bi-clock", field: "updatedLabel", muted: true }
    ],
    rowVm: profileRow,
    resourceLabel: "profile attribute",
    onEdit: function (id) { openEditModal("profile", id); },
    onSecondaryAction: function (id) { deleteProfileAttribute(id); },
    fetch: function (params) {
      return api("/profile-attributes/", params).then(function (items) {
        return items.slice().sort(function (a, b) {
          return (b.is_identity_resolution ? 1 : 0) - (a.is_identity_resolution ? 1 : 0) ||
            (a.priority_rank || 99) - (b.priority_rank || 99) ||
            (a.display_order || 0) - (b.display_order || 0);
        });
      });
    },
    clientFilters: {
      q: function (vm, value) {
        var needle = value.toLowerCase();
        return [vm.name, vm.attribute_internal_code, vm.description, vm.attribute_group]
          .some(function (item) { return String(item || "").toLowerCase().indexOf(needle) !== -1; });
      },
      group: function (vm, value) { return vm.attribute_group === value; },
      domain: function (vm, value) { return vm.domain_scope === value; },
      source: function (vm, value) { return vm.source_table === value; }
    },
    onFetched: function (items) {
      profileById = {};
      items.forEach(function (item) { profileById[item.id] = item; });
      $("#attributes-profile-total").text(items.length);
      populateGroupOptions(items);
    }
  }));

  var behaviorTable = C360.DataTableView.create($.extend(baseTableOptions(), {
    columns: [
      { label: "Behavior Event", icon: "bi-activity", type: "identity", nameField: "event_name", subField: "description", avatarIconField: "behaviorIcon", avatarBg: "bg-sky-50", avatarColor: "text-sky-600", avatarTextClass: "text-base" },
      { label: "Category", icon: "bi-tags", field: "categoryLabel" },
      { label: "Domain", icon: "bi-globe2", field: "domainLabel" },
      { label: "Event Type", icon: "bi-signpost-split", type: "badge", field: "eventTypeLabel", classField: "eventTypeClass" },
      { label: "Value Field", icon: "bi-input-cursor-text", field: "valueFieldLabel", muted: true },
      { label: "Status", icon: "bi-circle-half", type: "badge", field: "statusLabel", classField: "statusClass" },
      { label: "Updated", icon: "bi-clock", field: "updatedLabel", muted: true }
    ],
    rowVm: behaviorRow,
    resourceLabel: "behavior event",
    onEdit: function (id) { openEditModal("behavior", id); },
    onSecondaryAction: function (id) { deleteBehaviorEvent(id); },
    fetch: function (params) {
      return api("/event-catalog/", params).then(function (items) {
        return items.slice().sort(function (a, b) {
          return (a.display_order || 0) - (b.display_order || 0) ||
            String(a.event_name).localeCompare(String(b.event_name));
        });
      });
    },
    clientFilters: {
      q: function (vm, value) {
        var needle = value.toLowerCase();
        return [vm.event_name, vm.description, vm.event_category, vm.value_field]
          .some(function (item) { return String(item || "").toLowerCase().indexOf(needle) !== -1; });
      },
      category: function (vm, value) { return vm.event_category === value; },
      domain: function (vm, value) { return vm.domain_scope === value; },
      conversion: function (vm, value) { return String(!!vm.is_conversion_default) === value; }
    },
    onFetched: function (items) {
      behaviorById = {};
      items.forEach(function (item) { behaviorById[item.id] = item; });
      $("#attributes-behavior-total").text(items.length);
    }
  }));

  function populateGroupOptions(items) {
    var $select = $("#attributes-group-filter");
    if ($select.children().length > 1) return;
    var seen = {};
    items.forEach(function (item) {
      if (item.attribute_group && !seen[item.attribute_group]) {
        seen[item.attribute_group] = true;
        $select.append($("<option>").val(item.attribute_group).text(fmt.titleCase(item.attribute_group)));
      }
    });
  }

  function activeTable() {
    return activeSection === "behavior" ? behaviorTable : profileTable;
  }

  function activateSection(section) {
    activeSection = section === "behavior" ? "behavior" : "profile";
    var behavior = activeSection === "behavior";
    $(".attributes-section-tab")
      .attr("aria-selected", "false")
      .removeClass("border-indigo-200 shadow-sm ring-1 ring-indigo-100")
      .addClass("border-slate-200");
    var $activeTab = behavior ? $("#attributes-tab-behavior") : $("#attributes-tab-profile");
    $activeTab.attr("aria-selected", "true")
      .removeClass("border-slate-200")
      .addClass("border-indigo-200 shadow-sm ring-1 ring-indigo-100");

    $("#attributes-profile-filters").toggleClass("hidden", behavior).toggleClass("flex", !behavior);
    $("#attributes-behavior-filters").toggleClass("hidden", !behavior).toggleClass("flex", behavior);
    $("#attributes-section-title").text(behavior ? "Customer Behavior Attributes" : "Customer Profile Attributes");
    $("#attributes-section-subtitle").text(
      behavior ? "Behavioral event vocabulary stored in cdp_event_catalog." : "Attributes stored in cdp_profile_attributes."
    );
    $("#attributes-add-label").text(behavior ? "Add Behavior Event" : "Add Profile Attribute");
    $("#btn-attributes-add").toggleClass("hidden", !canManageAttributes());
    $("#attributes-search-input").attr("placeholder", behavior ? "Search behavior events…" : "Search profile attributes…");
    activeTable().renderHead();
    activeTable().load(false);
  }

  function loadSummaryCounts() {
    api("/profile-attributes/count")
      .done(function (response) { $("#attributes-profile-total").text(response.count); })
      .fail(function (xhr) { showApiError("loading profile attribute count", xhr); });
    api("/event-catalog/count")
      .done(function (response) { $("#attributes-behavior-total").text(response.count); })
      .fail(function (xhr) { showApiError("loading behavior event count", xhr); });
  }

  function setFormMode(section) {
    var behavior = section === "behavior";
    $("#attribute-form-icon")
      .attr("class", behavior ? "bi bi-activity" : "bi bi-person-vcard");
    $("#attribute-form-mode-badge")
      .text(behavior ? "Behavior" : "Profile")
      .removeClass("bg-indigo-50 text-indigo-600 bg-sky-50 text-sky-600")
      .addClass(behavior ? "bg-sky-50 text-sky-600" : "bg-indigo-50 text-indigo-600");
    $("#attribute-name-field").toggleClass("hidden", behavior);
    $("#behavior-category-field").toggleClass("hidden", !behavior);
    $("#attribute-profile-fields").toggleClass("hidden", behavior).toggleClass("grid", !behavior);
    $("#attribute-profile-checkboxes").toggleClass("hidden", behavior).toggleClass("flex", !behavior);
    $("#attribute-behavior-fields").toggleClass("hidden", !behavior).toggleClass("grid", behavior);
    $("#attribute-code-label").text(behavior ? "Event Name *" : "Attribute Code *");
    $("#attribute-add-code").attr("placeholder", behavior ? "purchase_completed" : "preferred_language");
  }

  function openAddModal() {
    if (!canManageAttributes()) return;
    editingId = null;
    var behavior = activeSection === "behavior";
    setFormMode(activeSection);
    $("#attribute-form-title").text(behavior ? "Add Behavior Event" : "Add Profile Attribute");
    $("#attribute-form-subtitle").text(
      behavior ? "Registers a governed event in cdp_event_catalog" : "Registers a profile field in cdp_profile_attributes"
    );
    $("#attribute-form-save-label").text(behavior ? "Save Event" : "Save Attribute");
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
    $("#behavior-add-category").val("GENERAL");
    $("#behavior-add-domain").val("all");
    $("#behavior-add-value-field").val("");
    $("#behavior-add-conversion").prop("checked", false);
    $("#attribute-add-status").val("ACTIVE");
    $("#attribute-form-modal").removeClass("hidden");
  }

  function openEditModal(section, id) {
    if (!canManageAttributes()) return;
    var behavior = section === "behavior";
    var item = behavior ? behaviorById[id] : profileById[id];
    if (!item) return;
    editingId = id;
    activeSection = section;
    setFormMode(section);
    $("#attribute-form-title").text(behavior ? "Edit Behavior Event" : "Edit Profile Attribute");
    $("#attribute-form-subtitle").text(behavior ? "Updates this cdp_event_catalog row" : "Updates this cdp_profile_attributes row");
    $("#attribute-form-save-label").text("Save Changes");
    $("#attribute-add-error").addClass("hidden").text("");
    $("#attribute-add-code").val(behavior ? item.event_name : item.attribute_internal_code).prop("disabled", true);
    $("#attribute-add-description").val(item.description || "");
    if (behavior) {
      $("#behavior-add-category").val(item.event_category);
      $("#behavior-add-domain").val(item.domain_scope);
      $("#behavior-add-value-field").val(item.value_field || "");
      $("#behavior-add-conversion").prop("checked", !!item.is_conversion_default);
      $("#attribute-add-status").val(item.status);
    } else {
      $("#attribute-add-name").val(item.name);
      $("#attribute-add-group").val(item.attribute_group);
      $("#attribute-add-source").val(item.source_table);
      $("#attribute-add-domain-scope").val(item.domain_scope);
      $("#attribute-add-data-type").val(item.data_type);
      $("#attribute-add-is-pii").prop("checked", !!item.is_pii);
      $("#attribute-add-is-segmentable").prop("checked", !!item.is_segmentable);
    }
    $("#attribute-form-modal").removeClass("hidden");
  }

  function closeModal() {
    $("#attribute-form-modal").addClass("hidden");
  }

  function formError(message) {
    $("#attribute-add-error").removeClass("hidden").text(message);
  }

  function submitForm() {
    if (!canManageAttributes()) {
      formError("Attribute management requires the Root / Super Admin or Admin role.");
      return;
    }
    $("#attribute-add-error").addClass("hidden").text("");
    var behavior = activeSection === "behavior";
    var code = $.trim($("#attribute-add-code").val());
    var name = $.trim($("#attribute-add-name").val());
    if (!code || (!behavior && !name)) {
      formError(behavior ? "Event Name is required." : "Attribute Code and Display Name are required.");
      return;
    }

    var isEdit = editingId !== null;
    var endpoint = behavior ? "/event-catalog/" : "/profile-attributes/";
    var payload;
    if (behavior) {
      payload = {
        event_category: $("#behavior-add-category").val(),
        domain_scope: $("#behavior-add-domain").val(),
        description: $.trim($("#attribute-add-description").val()) || null,
        is_conversion_default: $("#behavior-add-conversion").is(":checked"),
        value_field: $.trim($("#behavior-add-value-field").val()) || null,
        status: $("#attribute-add-status").val()
      };
      if (!isEdit) payload.event_name = code;
    } else {
      payload = {
        name: name,
        description: $.trim($("#attribute-add-description").val()) || null,
        attribute_group: $("#attribute-add-group").val(),
        source_table: $("#attribute-add-source").val(),
        domain_scope: $("#attribute-add-domain-scope").val(),
        data_type: $("#attribute-add-data-type").val(),
        is_pii: $("#attribute-add-is-pii").is(":checked"),
        is_segmentable: $("#attribute-add-is-segmentable").is(":checked")
      };
      if (!isEdit) payload.attribute_internal_code = code;
    }

    var request = isEdit
      ? api(endpoint + editingId, payload, "PATCH")
      : api(endpoint, payload, "POST");
    request.done(function () {
      closeModal();
      activeTable().reload();
    }).fail(function (xhr) {
      var detail = C360.config.formatApiErrorDetail
        ? C360.config.formatApiErrorDetail(xhr.responseJSON && xhr.responseJSON.detail)
        : ((xhr.responseJSON && xhr.responseJSON.detail) || "Unable to save this item.");
      formError(typeof detail === "string" ? detail : JSON.stringify(detail));
    });
  }

  function deleteProfileAttribute(id) {
    if (!canManageAttributes()) return;
    var item = profileById[id];
    if (!item) return;
    openDeleteModal({
      id: id,
      section: "profile",
      name: item.name,
      endpoint: "/profile-attributes/",
      impact: "Removing this profile attribute can affect identity resolution, segmentation rules, and downstream customer-profile metadata."
    });
  }

  function deleteBehaviorEvent(id) {
    if (!canManageAttributes()) return;
    var item = behaviorById[id];
    if (!item) return;
    openDeleteModal({
      id: id,
      section: "behavior",
      name: item.event_name,
      endpoint: "/event-catalog/",
      impact: "Removing this event can stop new matching events from being accepted by event analytics and processing."
    });
  }

  function openDeleteModal(item) {
    pendingDelete = item;
    $("#attribute-delete-title").text("Delete " + item.name + "?");
    $("#attribute-delete-warning").text(
      "You are about to permanently delete this " +
      (item.section === "behavior" ? "behavior event" : "profile attribute") +
      " from the catalog. This action cannot be undone."
    );
    $("#attribute-delete-impact").text(item.impact);
    $("#attribute-delete-confirmation").val("");
    $("#attribute-delete-error").addClass("hidden").text("");
    $("#btn-attribute-delete-confirm").prop("disabled", true);
    $("#attribute-delete-modal").removeClass("hidden");
    setTimeout(function () { $("#attribute-delete-confirmation").trigger("focus"); }, 0);
  }

  function closeDeleteModal() {
    pendingDelete = null;
    $("#attribute-delete-modal").addClass("hidden");
    $("#attribute-delete-confirmation").val("");
  }

  function updateDeleteConfirmation() {
    var confirmed = $.trim($("#attribute-delete-confirmation").val()).toLowerCase() === "yes";
    $("#btn-attribute-delete-confirm").prop("disabled", !confirmed);
    $("#attribute-delete-error").addClass("hidden").text("");
  }

  function confirmDelete() {
    if (!canManageAttributes() || !pendingDelete) return;
    if ($.trim($("#attribute-delete-confirmation").val()).toLowerCase() !== "yes") {
      $("#attribute-delete-error").removeClass("hidden").text('Type "yes" to confirm deletion.');
      return;
    }

    var item = pendingDelete;
    $("#btn-attribute-delete-confirm").prop("disabled", true)
      .html('<i class="bi bi-hourglass-split" aria-hidden="true"></i>Deleting…');
    api(item.endpoint + item.id, {}, "DELETE")
      .done(function () {
        closeDeleteModal();
        loadSummaryCounts();
        (item.section === "behavior" ? behaviorTable : profileTable).reload();
      })
      .fail(function (xhr) {
        var detail = C360.config.formatApiErrorDetail
          ? C360.config.formatApiErrorDetail(xhr.responseJSON && xhr.responseJSON.detail)
          : "Unable to delete this catalog item.";
        $("#attribute-delete-error").removeClass("hidden").text(detail || "Unable to delete this catalog item.");
        $("#btn-attribute-delete-confirm").prop("disabled", false);
      })
      .always(function () {
        $("#btn-attribute-delete-confirm")
          .html('<i class="bi bi-trash3" aria-hidden="true"></i>Delete permanently');
      });
  }

  function bindEvents() {
    profileTable.bindSearch("#attributes-search-input", "q", 250);
    behaviorTable.bindSearch("#attributes-search-input", "q", 250);
    profileTable.bindSelect("#attributes-group-filter", "group");
    profileTable.bindSelect("#attributes-domain-filter", "domain");
    profileTable.bindSelect("#attributes-source-filter", "source");
    behaviorTable.bindSelect("#behavior-category-filter", "category");
    behaviorTable.bindSelect("#behavior-domain-filter", "domain");
    behaviorTable.bindSelect("#behavior-conversion-filter", "conversion");
    profileTable.bindRowEdit();
    behaviorTable.bindRowEdit();

    $(document).on("click", "[data-attributes-tab]", function () { activateSection($(this).data("attributes-tab")); });
    $(document).on("click", "#btn-attributes-add", openAddModal);
    $(document).on("click", "#btn-attribute-add-cancel", closeModal);
    $(document).on("click", "#btn-attribute-add-save", submitForm);
    $(document).on("input", "#attribute-delete-confirmation", updateDeleteConfirmation);
    $(document).on("keydown", "#attribute-delete-confirmation", function (event) {
      if (event.key === "Enter" && !$("#btn-attribute-delete-confirm").prop("disabled")) {
        confirmDelete();
      }
    });
    $(document).on("click", "#btn-attribute-delete-cancel", closeDeleteModal);
    $(document).on("click", "#btn-attribute-delete-confirm", confirmDelete);
    $(document).on("click", "#attribute-form-modal", function (event) {
      if (event.target === this) closeModal();
    });
    $(document).on("click", "#attribute-delete-modal", function (event) {
      if (event.target === this) closeDeleteModal();
    });
  }

  C360.router.define("/attributes", {
    section: "view-attributes",
    tab: "attributes",
    mount: function () {
      loadSummaryCounts();
      activateSection(activeSection);
    }
  });

  C360.attributesView = {
    load: function () { return activeTable().reload(); },
    bindEvents: bindEvents
  };
})(window.C360);
