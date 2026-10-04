/* Customer 360 Admin -- unified AI-agent registry view (cdp_ai_agents).
 *
 * Modeled directly on attributes-view.js: a real consumer of the shared
 * C360.DataTableView component (static/js/data-table-view.js), client-side
 * filtering (the generic CRUD router behind /ai-agents only
 * supports skip/limit/status/model_type), and the same Add/Edit modal
 * pattern. Unlike attributes (read-only catalog, no delete route), the
 * AI-agent router also exposes DELETE, so the edit modal grows a Delete
 * button (see openEditAiAgentModal). */
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
    ranking_recommendation: "bg-emerald-100 text-emerald-700",
    forecasting: "bg-blue-100 text-blue-700",
    anomaly_detection: "bg-rose-100 text-rose-700",
    uplift_modeling: "bg-orange-100 text-orange-700",
    semantic_embedding: "bg-purple-100 text-purple-700",
    graph_ml: "bg-teal-100 text-teal-700",
    optimization: "bg-yellow-100 text-yellow-700",
    rules_engine: "bg-slate-100 text-slate-600",
    generative_llm: "bg-violet-100 text-violet-700"
  };
  function typeBadgeClass(v) { return TYPE_BADGE_CLASSES[v] || "bg-slate-100 text-slate-500"; }

  var TYPE_ICONS = {
    classification: "\ud83c\udff7\ufe0f",
    regression: "\ud83d\udcc8",
    clustering: "\ud83e\udde9",
    ranking_recommendation: "\ud83c\udfaf",
    forecasting: "\ud83d\udd2e",
    anomaly_detection: "\u26a0\ufe0f",
    uplift_modeling: "\ud83d\udcc8",
    semantic_embedding: "\ud83d\udcdd",
    graph_ml: "\ud83d\udd78\ufe0f",
    optimization: "\u2696\ufe0f",
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
      typeLabel: fmt.titleCase(String(m.model_type || "").replace(/_/g, " ")),
      typeBadgeClass: typeBadgeClass(m.model_type),
      statusLabel: fmt.titleCase(m.status),
      statusBadgeClass: statusBadgeClass(m.status),
      scheduleLabel: formatCronSchedule(m.schedule_definition),
      scheduleTitle: m.schedule_definition ? "Cron: " + m.schedule_definition : "",
      featureCountLabel: featureCountLabel(m.input_features),
      modelLabel: m.model_name || "Not configured",
      promptVersionLabel: m.prompt_key ? "v" + (m.instruction_version || 1) : "No prompt",
      updatedLabel: fmt.dateTime(m.updated_at)
    });
  }

  var dtv = C360.DataTableView.create({
    columns: [
      {
        label: "Agent", type: "identity", nameField: "display_name", subField: "agent_code", subStyle: "tag",
        avatarField: "typeIcon", avatarBg: "bg-violet-100", avatarColor: "text-violet-700", avatarTextClass: "text-base"
      },
      { label: "Agent Type", type: "badge", field: "typeLabel", classField: "typeBadgeClass" },
      { label: "Model", field: "modelLabel" },
      { label: "Lifecycle", type: "badge", field: "statusLabel", classField: "statusBadgeClass" },
      { label: "Prompt Version", field: "promptVersionLabel" },
      { label: "Run Schedule", field: "scheduleLabel", titleField: "scheduleTitle" },
      { label: "Input Features", field: "featureCountLabel" },
      { label: "Last Updated", field: "updatedLabel" }
    ],
    rowVm: rowVm,
    rowId: function (vm) { return vm.agent_code; },
    rowClickable: false, // no dedicated detail page -- Edit modal covers view+edit+delete
    onEdit: function (id) { openEditAiAgentModal(id); },
    editLabel: "Edit",
    resourceLabel: "AI agent",
    clientSide: true,
    clientSideLimit: 500,
    fetch: function (params) { return api("/ai-agents/", params); },
    clientFilters: {
      q: function (vm, value) {
        var needle = value.toLowerCase();
        return (vm.display_name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.agent_code || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.model_name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.description || "").toLowerCase().indexOf(needle) !== -1;
      },
      type: function (vm, value) { return vm.model_type === value; },
      status: function (vm, value) { return vm.status === value; },
      model: function (vm, value) {
        return value === "configured" ? !!vm.model_name : !vm.model_name;
      },
      prompt: function (vm, value) {
        return value === "backed" ? !!vm.prompt_key : !vm.prompt_key;
      },
      schedule: function (vm, value) {
        return value === "scheduled" ? !!vm.schedule_definition : !vm.schedule_definition;
      }
    },
    onFetched: function (items) {
      aiAgentsByCode = {};
      items.forEach(function (m) { aiAgentsByCode[m.agent_code] = m; });
      updateAgentSummary(items);
    },
    onError: function (xhr) { showApiError("loading AI agents", xhr); },
    el: {
      thead: "#agent-models-thead",
      tbody: "#agent-models-tbody",
      loading: "#agent-models-loading",
      empty: "#agent-models-empty",
      countLabel: "#agent-models-count-label"
    }
  });

  var aiAgentsByCode = {}; // last-fetched rows, keyed by agent_code -- backs the Edit modal

  function updateAgentSummary(items) {
    var counts = { ACTIVE: 0, TRAINING: 0, FAILED: 0, INACTIVE: 0 };
    var updatedToday = 0;
    var today = new Date().toISOString().slice(0, 10);
    (items || []).forEach(function (item) {
      var status = String(item.status || "").toUpperCase();
      if (Object.prototype.hasOwnProperty.call(counts, status)) counts[status] += 1;
      if (item.updated_at && new Date(item.updated_at).toISOString().slice(0, 10) === today) updatedToday += 1;
    });
    var total = (items || []).length;
    $("#agent-model-summary-total, #agent-model-tab-total").text(total);
    $("#agent-model-summary-active, #agent-model-tab-active").text(counts.ACTIVE);
    $("#agent-model-summary-training, #agent-model-tab-training").text(counts.TRAINING);
    $("#agent-model-summary-failed, #agent-model-tab-failed").text(counts.FAILED);
    $("#agent-model-tab-inactive").text(counts.INACTIVE);
    $("#agent-model-summary-active-rate").text(total ? Math.round((counts.ACTIVE / total) * 100) + "% of total" : "No active agents");
    $("#agent-model-summary-updated").text(updatedToday);
  }

  function syncStatusTab(status) {
    $(".agent-model-status-tab").each(function () {
      var selected = String($(this).attr("data-status") || "") === String(status || "");
      $(this)
        .attr("aria-selected", selected ? "true" : "false")
        .toggleClass("border-violet-600 text-violet-700", selected)
        .toggleClass("border-transparent text-slate-500", !selected);
    });
  }

  function load() { return dtv.load(false); }

  function populateSchedulePresets() {
    var schedule = C360.AgentWorkflowSchedule;
    var $select = $("#agent-model-add-schedule-preset");
    if (!schedule || !$select.length) return;
    $select.empty();
    schedule.presets.forEach(function (preset) {
      $("<option></option>").attr("value", preset.value).text(preset.label).appendTo($select);
    });
  }

  function updateScheduleExplanation() {
    var schedule = C360.AgentWorkflowSchedule;
    var $preset = $("#agent-model-add-schedule-preset");
    var $custom = $("#agent-model-add-schedule-custom");
    var $input = $("#agent-model-add-schedule");
    if (!schedule || !$preset.length) return;

    var presetValue = String($preset.val() || "");
    var isCustom = presetValue === "custom";
    var expression = isCustom ? $.trim($input.val()) : presetValue;
    var isValid = schedule.isValid(expression);
    $custom.toggleClass("hidden", !isCustom);
    $input.attr("aria-invalid", isCustom && !isValid ? "true" : "false")
      .toggleClass("border-red-300 bg-red-50", isCustom && !isValid)
      .toggleClass("border-slate-200/80 bg-slate-50/50", !isCustom || isValid);
    $("#agent-model-add-schedule-explanation")
      .text(expression ? "Runs: " + schedule.label(expression) : "Uses the agent default schedule.")
      .toggleClass("hidden", !expression && isCustom);
  }

  function parseCsvList(value) {
    return String(value || "")
      .split(/[,\n]+/)
      .map(function (x) { return x.trim(); })
      .filter(function (x) { return x.length > 0; });
  }

  // Non-null while the modal is editing an existing row (its agent_code,
  // the table's own primary key) -- null means "creating a new row".
  var editingAiAgentCode = null;

  function openAddAiAgentModal() {
    editingAiAgentCode = null;
    $("#agent-model-form-title").text("Add AI Agent");
    $("#agent-model-form-subtitle").text("Registers a new row in the cdp_ai_agents registry");
    $("#agent-model-form-save-label").text("Save Agent");
    $("#btn-agent-model-delete").addClass("hidden").removeClass("inline-flex");
    $("#agent-model-add-error").addClass("hidden").text("");
    $("#agent-model-add-name").val("").prop("disabled", false);
    $("#agent-model-add-display-name").val("");
    $("#agent-model-add-description").val("");
    $("#agent-model-add-model-name").val("");
    $("#agent-model-add-type").val("classification");
    $("#agent-model-add-status").val("ACTIVE");
    populateSchedulePresets();
    $("#agent-model-add-schedule-preset").val("");
    $("#agent-model-add-schedule").val("");
    updateScheduleExplanation();
    $("#agent-model-add-features").val("");
    $("#agent-model-add-hyperparameters").val("");
    $("#agent-model-add-prompt-key").val("");
    $("#agent-model-add-prompt-engine").val("none");
    $("#agent-model-add-instructions").val("");
    $("#agent-model-add-required-vars").val("");
    $("#ai-agent-form-modal").removeClass("hidden");
  }

  function openEditAiAgentModal(name) {
    var m = aiAgentsByCode[name];
    if (!m) return;
    editingAiAgentCode = name;
    $("#agent-model-form-title").text("Edit AI Agent");
    $("#agent-model-form-subtitle").text("Updates this cdp_ai_agents registry row");
    $("#agent-model-form-save-label").text("Save Changes");
    $("#btn-agent-model-delete").removeClass("hidden").addClass("inline-flex");
    $("#agent-model-add-error").addClass("hidden").text("");
    // agent_code is the primary key and isn't part of AiAgentUpdate
    // -- shown for context but not editable.
    $("#agent-model-add-name").val(m.agent_code).prop("disabled", true);
    $("#agent-model-add-display-name").val(m.display_name);
    $("#agent-model-add-description").val(m.description || "");
    $("#agent-model-add-model-name").val(m.model_name || "");
    $("#agent-model-add-type").val(m.model_type);
    $("#agent-model-add-status").val(m.status);
    populateSchedulePresets();
    $("#agent-model-add-schedule").val(m.schedule_definition || "");
    var schedule = C360.AgentWorkflowSchedule;
    $("#agent-model-add-schedule-preset").val(
      schedule ? schedule.presetFor(m.schedule_definition || "").value : ""
    );
    updateScheduleExplanation();
    $("#agent-model-add-features").val((m.input_features || []).join(", "));
    $("#agent-model-add-hyperparameters").val(m.hyperparameters && Object.keys(m.hyperparameters).length ? JSON.stringify(m.hyperparameters, null, 2) : "");
    $("#agent-model-add-prompt-key").val(m.prompt_key || "");
    $("#agent-model-add-prompt-engine").val(m.prompt_engine || "none");
    $("#agent-model-add-instructions").val(m.system_instructions || "");
    $("#agent-model-add-required-vars").val((m.required_variables || []).join(", "));
    $("#ai-agent-form-modal").removeClass("hidden");
  }

  function closeAiAgentModal() {
    $("#ai-agent-form-modal").addClass("hidden");
  }

  function submitAiAgentForm() {
    var $error = $("#agent-model-add-error");
    $error.addClass("hidden").text("");

    var name = $.trim($("#agent-model-add-name").val());
    var displayName = $.trim($("#agent-model-add-display-name").val());
    if (!name || !displayName) {
      $error.removeClass("hidden").text("Agent Code and Display Name are required.");
      return;
    }

    var scheduleDefinition = $.trim($("#agent-model-add-schedule").val()) || null;
    if (scheduleDefinition && C360.AgentWorkflowSchedule && !C360.AgentWorkflowSchedule.isValid(scheduleDefinition)) {
      $error.removeClass("hidden").text("Schedule must be a valid five-field cron expression or supported @macro.");
      return;
    }

    var hyperparametersRaw = $.trim($("#agent-model-add-hyperparameters").val());
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

    var isEdit = editingAiAgentCode !== null;
    var payload = {
      display_name: displayName,
      description: $.trim($("#agent-model-add-description").val()) || null,
      model_name: $.trim($("#agent-model-add-model-name").val()) || null,
      model_type: $("#agent-model-add-type").val(),
      status: $("#agent-model-add-status").val(),
      schedule_definition: scheduleDefinition,
      input_features: parseCsvList($("#agent-model-add-features").val()),
      hyperparameters: hyperparameters,
      prompt_key: $.trim($("#agent-model-add-prompt-key").val()) || null,
      prompt_engine: $("#agent-model-add-prompt-engine").val(),
      system_instructions: $.trim($("#agent-model-add-instructions").val()) || null,
      required_variables: parseCsvList($("#agent-model-add-required-vars").val())
    };
    // agent_code is only settable on create (AiAgentUpdate has
    // no such field -- it's the immutable primary key).
    if (!isEdit) payload.agent_code = name;

    var request = isEdit
      ? api("/ai-agents/" + encodeURIComponent(editingAiAgentCode), payload, "PATCH")
      : api("/ai-agents/", payload, "POST");

    request
      .done(function () {
        closeAiAgentModal();
        load();
      })
      .fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || ("Could not " + (isEdit ? "update" : "create") + " AI agent.");
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : JSON.stringify(detail));
      });
  }

  function deleteAiAgent() {
    if (!editingAiAgentCode) return;
    var m = aiAgentsByCode[editingAiAgentCode];
    var label = m && m.display_name ? m.display_name : editingAiAgentCode;
    if (!window.confirm("Delete AI agent '" + label + "'?")) return;

    api("/ai-agents/" + encodeURIComponent(editingAiAgentCode), {}, "DELETE")
      .done(function () {
        closeAiAgentModal();
        load();
      })
      .fail(function (xhr) { showApiError("deleting AI agent", xhr); });
  }

  function clearFilters() {
    [
      "#agent-models-search-input",
      "#agent-models-type-filter",
      "#agent-models-status-filter",
      "#agent-models-model-filter",
      "#agent-models-prompt-filter",
      "#agent-models-schedule-filter"
    ].forEach(function (selector) { $(selector).val(""); });
    dtv.clearFilters();
    syncStatusTab("");
  }

  function bindEvents() {
    dtv.bindSearch("#agent-models-search-input", "q", 300);
    dtv.bindSelect("#agent-models-type-filter", "type");
    dtv.bindSelect("#agent-models-status-filter", "status");
    dtv.bindSelect("#agent-models-model-filter", "model");
    dtv.bindSelect("#agent-models-prompt-filter", "prompt");
    dtv.bindSelect("#agent-models-schedule-filter", "schedule");
    dtv.bindRowEdit();

    $("#agent-models-status-filter")
      .off("change.aiAgentStatusTabs")
      .on("change.aiAgentStatusTabs", function () { syncStatusTab($(this).val()); });
    $("#agent-model-status-tabs")
      .off("click.aiAgentStatusTabs")
      .on("click.aiAgentStatusTabs", "button[data-status]", function () {
        $("#agent-models-status-filter").val($(this).attr("data-status")).trigger("change");
      });
    syncStatusTab($("#agent-models-status-filter").val());

    $(document).on("click", "#btn-agent-models-clear-filters", clearFilters);
    $(document).on("click", "#btn-agent-model-add", openAddAiAgentModal);
    $(document).on("click", "#btn-agent-model-add-cancel", closeAiAgentModal);
    $(document).on("click", "#btn-agent-model-modal-close", closeAiAgentModal);
    $(document).on("click", "#btn-agent-model-add-save", submitAiAgentForm);
    $(document).on("click", "#btn-agent-model-delete", deleteAiAgent);
    $(document).on("change", "#agent-model-add-schedule-preset", function () {
      var value = String($(this).val() || "");
      if (value !== "custom") $("#agent-model-add-schedule").val(value);
      updateScheduleExplanation();
    });
    $(document).on("input", "#agent-model-add-schedule", function () {
      $("#agent-model-add-schedule-preset").val("custom");
      updateScheduleExplanation();
    });
    $(document).on("click", "#ai-agent-form-modal", function (e) {
      if (e.target === this) closeAiAgentModal();
    });
    $(document).off("keydown.aiAgentModal").on("keydown.aiAgentModal", function (e) {
      if (e.key === "Escape" && !$("#ai-agent-form-modal").hasClass("hidden")) closeAiAgentModal();
    });
  }

  // Owns the "/agent" tab/route (see router.js).
  C360.router.define("/agent", {
    section: "view-agent",
    tab: "agent",
    mount: function () { load(); }
  });

  C360.aiAgentView = { load: load, bindEvents: bindEvents };
})(window.C360);
 