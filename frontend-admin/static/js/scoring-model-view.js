/* Customer 360 Admin -- Scoring Models Registry view (cdp_scoring_models).
 *
 * Modeled directly on attributes-view.js: a real consumer of the shared
 * C360.DataTableView component (static/js/data-table-view.js), client-side
 * filtering (the generic CRUD router behind /metadata/scoring-models only
 * supports skip/limit/status/model_type), and the same Add/Edit modal
 * pattern. Unlike attributes (read-only catalog, no delete route), the
 * scoring-models router also exposes DELETE, so the edit modal grows a
 * Delete button (see openEditScoringModelModal). */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var STATUS_BADGE_CLASSES = {
    ACTIVE: "bg-green-100 text-green-700",
    INACTIVE: "bg-slate-100 text-slate-500",
    TRAINING: "bg-blue-100 text-blue-700",
    DEPRECATED: "bg-amber-100 text-amber-700",
    FAILED: "bg-red-100 text-red-700"
  };
  function statusBadgeClass(v) { return STATUS_BADGE_CLASSES[v] || "bg-slate-100 text-slate-500"; }

  var TYPE_BADGE_CLASSES = {
    classification: "bg-indigo-100 text-indigo-700",
    regression: "bg-cyan-100 text-cyan-700",
    clustering: "bg-fuchsia-100 text-fuchsia-700",
    rules_engine: "bg-slate-100 text-slate-600",
    generative_llm: "bg-violet-100 text-violet-700"
  };
  function typeBadgeClass(v) { return TYPE_BADGE_CLASSES[v] || "bg-slate-100 text-slate-500"; }

  var TYPE_ICONS = {
    classification: "\ud83c\udff7\ufe0f",
    regression: "\ud83d\udcc8",
    clustering: "\ud83e\udde9",
    rules_engine: "\u2699\ufe0f",
    generative_llm: "\ud83e\udd16"
  };
  function typeIcon(v) { return TYPE_ICONS[v] || "\ud83d\udd2e"; }

  function featureCountLabel(features) {
    var n = Array.isArray(features) ? features.length : 0;
    return n + (n === 1 ? " feature" : " features");
  }

  function rowVm(m) {
    return $.extend({}, m, {
      typeIcon: typeIcon(m.model_type),
      typeLabel: fmt.titleCase(m.model_type),
      typeBadgeClass: typeBadgeClass(m.model_type),
      statusLabel: fmt.titleCase(m.status),
      statusBadgeClass: statusBadgeClass(m.status),
      scheduleLabel: m.schedule_definition || "\u2014",
      featureCountLabel: featureCountLabel(m.input_features),
      updatedLabel: fmt.dateTime(m.updated_at)
    });
  }

  var dtv = C360.DataTableView.create({
    columns: [
      {
        label: "Model", type: "identity", nameField: "display_name", subField: "scoring_model_name", subStyle: "tag",
        avatarField: "typeIcon", avatarBg: "bg-violet-100", avatarColor: "text-violet-700", avatarTextClass: "text-base"
      },
      { label: "Type", type: "badge", field: "typeLabel", classField: "typeBadgeClass" },
      { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" },
      { label: "Schedule", field: "scheduleLabel" },
      { label: "Features", field: "featureCountLabel" },
      { label: "Updated", field: "updatedLabel" }
    ],
    rowVm: rowVm,
    rowId: function (vm) { return vm.scoring_model_name; },
    rowClickable: false, // no dedicated detail page -- Edit modal covers view+edit+delete
    onEdit: function (id) { openEditScoringModelModal(id); },
    editLabel: "Edit",
    resourceLabel: "scoring model",
    clientSide: true,
    clientSideLimit: 500,
    fetch: function (params) { return api("/metadata/scoring-models", params); },
    clientFilters: {
      q: function (vm, value) {
        var needle = value.toLowerCase();
        return (vm.display_name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.scoring_model_name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.description || "").toLowerCase().indexOf(needle) !== -1;
      },
      type: function (vm, value) { return vm.model_type === value; },
      status: function (vm, value) { return vm.status === value; }
    },
    onFetched: function (items) { scoringModelsByName = {}; items.forEach(function (m) { scoringModelsByName[m.scoring_model_name] = m; }); },
    onError: function (xhr) { showApiError("loading scoring models", xhr); },
    el: {
      thead: "#scoring-models-thead",
      tbody: "#scoring-models-tbody",
      loading: "#scoring-models-loading",
      empty: "#scoring-models-empty",
      countLabel: "#scoring-models-count-label"
    }
  });

  var scoringModelsByName = {}; // last-fetched rows, keyed by scoring_model_name -- backs the Edit modal

  function load() { return dtv.load(false); }

  function parseCsvList(value) {
    return String(value || "")
      .split(",")
      .map(function (x) { return x.trim(); })
      .filter(function (x) { return x.length > 0; });
  }

  // Non-null while the modal is editing an existing row (its scoring_model_name,
  // the table's own primary key) -- null means "creating a new row".
  var editingScoringModelName = null;

  function openAddScoringModelModal() {
    editingScoringModelName = null;
    $("#scoring-model-form-title").text("Add Scoring Model");
    $("#scoring-model-form-subtitle").text("Registers a new row in the cdp_scoring_models registry");
    $("#scoring-model-form-save-label").text("Save Model");
    $("#btn-scoring-model-delete").addClass("hidden");
    $("#scoring-model-add-error").addClass("hidden").text("");
    $("#scoring-model-add-name").val("").prop("disabled", false);
    $("#scoring-model-add-display-name").val("");
    $("#scoring-model-add-description").val("");
    $("#scoring-model-add-type").val("classification");
    $("#scoring-model-add-status").val("ACTIVE");
    $("#scoring-model-add-schedule").val("");
    $("#scoring-model-add-features").val("");
    $("#scoring-model-add-hyperparameters").val("");
    $("#scoring-model-form-modal").removeClass("hidden");
  }

  function openEditScoringModelModal(name) {
    var m = scoringModelsByName[name];
    if (!m) return;
    editingScoringModelName = name;
    $("#scoring-model-form-title").text("Edit Scoring Model");
    $("#scoring-model-form-subtitle").text("Updates this cdp_scoring_models registry row");
    $("#scoring-model-form-save-label").text("Save Changes");
    $("#btn-scoring-model-delete").removeClass("hidden");
    $("#scoring-model-add-error").addClass("hidden").text("");
    // scoring_model_name is the primary key and isn't part of ScoringModelUpdate
    // -- shown for context but not editable.
    $("#scoring-model-add-name").val(m.scoring_model_name).prop("disabled", true);
    $("#scoring-model-add-display-name").val(m.display_name);
    $("#scoring-model-add-description").val(m.description || "");
    $("#scoring-model-add-type").val(m.model_type);
    $("#scoring-model-add-status").val(m.status);
    $("#scoring-model-add-schedule").val(m.schedule_definition || "");
    $("#scoring-model-add-features").val((m.input_features || []).join(", "));
    $("#scoring-model-add-hyperparameters").val(m.hyperparameters && Object.keys(m.hyperparameters).length ? JSON.stringify(m.hyperparameters, null, 2) : "");
    $("#scoring-model-form-modal").removeClass("hidden");
  }

  function closeScoringModelModal() {
    $("#scoring-model-form-modal").addClass("hidden");
  }

  function submitScoringModelForm() {
    var $error = $("#scoring-model-add-error");
    $error.addClass("hidden").text("");

    var name = $.trim($("#scoring-model-add-name").val());
    var displayName = $.trim($("#scoring-model-add-display-name").val());
    if (!name || !displayName) {
      $error.removeClass("hidden").text("Model Name and Display Name are required.");
      return;
    }

    var hyperparametersRaw = $.trim($("#scoring-model-add-hyperparameters").val());
    var hyperparameters = {};
    if (hyperparametersRaw) {
      try {
        hyperparameters = JSON.parse(hyperparametersRaw);
        if (typeof hyperparameters !== "object" || hyperparameters === null || Array.isArray(hyperparameters)) {
          $error.removeClass("hidden").text("Hyperparameters must be a valid JSON object (e.g. {\"key\": \"value\"}).");
          return;
        }
      } catch (e) {
        $error.removeClass("hidden").text("Hyperparameters is not valid JSON. Format as {\"key\": \"value\"}.");
        return;
      }
    }

    var isEdit = editingScoringModelName !== null;
    var payload = {
      display_name: displayName,
      description: $.trim($("#scoring-model-add-description").val()) || null,
      model_type: $("#scoring-model-add-type").val(),
      status: $("#scoring-model-add-status").val(),
      schedule_definition: $.trim($("#scoring-model-add-schedule").val()) || null,
      input_features: parseCsvList($("#scoring-model-add-features").val()),
      hyperparameters: hyperparameters
    };
    // scoring_model_name is only settable on create (ScoringModelUpdate has
    // no such field -- it's the immutable primary key).
    if (!isEdit) payload.scoring_model_name = name;

    var request = isEdit
      ? api("/metadata/scoring-models/" + encodeURIComponent(editingScoringModelName), payload, "PATCH")
      : api("/metadata/scoring-models", payload, "POST");

    request
      .done(function () {
        closeScoringModelModal();
        load();
      })
      .fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || ("Could not " + (isEdit ? "update" : "create") + " scoring model.");
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : JSON.stringify(detail));
      });
  }

  function deleteScoringModel() {
    if (!editingScoringModelName) return;
    var m = scoringModelsByName[editingScoringModelName];
    var label = m && m.display_name ? m.display_name : editingScoringModelName;
    if (!window.confirm("Delete scoring model '" + label + "'?")) return;

    api("/metadata/scoring-models/" + encodeURIComponent(editingScoringModelName), {}, "DELETE")
      .done(function () {
        closeScoringModelModal();
        load();
      })
      .fail(function (xhr) { showApiError("deleting scoring model", xhr); });
  }

  function bindEvents() {
    dtv.bindSearch("#scoring-models-search-input", "q", 300);
    dtv.bindSelect("#scoring-models-type-filter", "type");
    dtv.bindSelect("#scoring-models-status-filter", "status");
    dtv.bindRowEdit();

    $(document).on("click", "#btn-scoring-model-add", openAddScoringModelModal);
    $(document).on("click", "#btn-scoring-model-add-cancel", closeScoringModelModal);
    $(document).on("click", "#btn-scoring-model-add-save", submitScoringModelForm);
    $(document).on("click", "#btn-scoring-model-delete", deleteScoringModel);
    $(document).on("click", "#scoring-model-form-modal", function (e) {
      if (e.target === this) closeScoringModelModal();
    });
  }

  // Owns the "/scoring" tab/route (see router.js).
  C360.router.define("/scoring", {
    section: "view-scoring",
    tab: "scoring",
    mount: function () { load(); }
  });

  C360.scoringModelView = { load: load, bindEvents: bindEvents };
})(window.C360);
 