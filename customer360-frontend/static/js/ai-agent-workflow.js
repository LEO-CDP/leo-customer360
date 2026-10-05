/* Customer 360 -- segment agent workflow editor. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var SCHEDULE_PRESETS = [
    { value: "", label: "Use agent default", expression: "" },
    { value: "*/10 * * * *", label: "Every 10 minutes", expression: "*/10 * * * *" },
    { value: "*/30 * * * *", label: "Every 30 minutes", expression: "*/30 * * * *" },
    { value: "0 0 * * *", label: "At midnight", expression: "0 0 * * *" },
    { value: "0 9 * * *", label: "Every morning at 09:00", expression: "0 9 * * *" },
    { value: "0 15 * * *", label: "Every afternoon at 15:00", expression: "0 15 * * *" },
    { value: "0 18 * * *", label: "Every evening at 18:00", expression: "0 18 * * *" },
    { value: "0 * * * *", label: "Every hour", expression: "0 * * * *" },
    { value: "0 */2 * * *", label: "Every 2 hours", expression: "0 */2 * * *" },
    { value: "0 */4 * * *", label: "Every 4 hours", expression: "0 */4 * * *" },
    { value: "0 */6 * * *", label: "Every 6 hours", expression: "0 */6 * * *" },
    { value: "0 */12 * * *", label: "Every 12 hours", expression: "0 */12 * * *" },
    { value: "0 12 * * *", label: "Every day at 12:00", expression: "0 12 * * *" },
    { value: "0 9,15 * * *", label: "Twice daily at 09:00 and 15:00", expression: "0 9,15 * * *" },
    { value: "0 9 */2 * *", label: "Every 2 days at 09:00", expression: "0 9 */2 * *" },
    { value: "0 9 */3 * *", label: "Every 3 days at 09:00", expression: "0 9 */3 * *" },
    { value: "0 9 */4 * *", label: "Every 4 days at 09:00", expression: "0 9 */4 * *" },
    { value: "0 9 */5 * *", label: "Every 5 days at 09:00", expression: "0 9 */5 * *" },
    { value: "0 9 */6 * *", label: "Every 6 days at 09:00", expression: "0 9 */6 * *" },
    { value: "0 9 * * 1-5", label: "Every weekday at 09:00", expression: "0 9 * * 1-5" },
    { value: "0 9 * * 0,6", label: "Every weekend at 09:00", expression: "0 9 * * 0,6" },
    { value: "0 9 * * 0", label: "Every Sunday at 09:00", expression: "0 9 * * 0" },
    { value: "0 9 * * 5", label: "Every Friday at 09:00", expression: "0 9 * * 5" },
    { value: "0 9 * * 1", label: "Every week on Monday at 09:00", expression: "0 9 * * 1" },
    { value: "0 9 1 * *", label: "Every month on day 1 at 09:00", expression: "0 9 1 * *" },
    { value: "0 9 1 */2 *", label: "Every 2 months on day 1 at 09:00", expression: "0 9 1 */2 *" },
    { value: "0 9 1 */3 *", label: "Every 3 months on day 1 at 09:00", expression: "0 9 1 */3 *" },
    { value: "0 9 1 */4 *", label: "Every 4 months on day 1 at 09:00", expression: "0 9 1 */4 *" },
    { value: "0 9 1 */5 *", label: "Every 5 months on day 1 at 09:00", expression: "0 9 1 */5 *" },
    { value: "0 9 1 */6 *", label: "Every 6 months on day 1 at 09:00", expression: "0 9 1 */6 *" },
    { value: "0 9 1 1 *", label: "Every year on January 1 at 09:00", expression: "0 9 1 1 *" },
    { value: "custom", label: "Custom cron expression", expression: null }
  ];

  var SCHEDULE_MACROS = {
    "@annually": true,
    "@daily": true,
    "@hourly": true,
    "@monthly": true,
    "@reboot": true,
    "@weekly": true,
    "@yearly": true
  };

  function cronLabel(expression) {
    var formatter = window.formatCronSchedule;
    return formatter ? formatter(expression) : String(expression || "No schedule configured");
  }

  C360.AgentWorkflowSchedule = {
    presets: SCHEDULE_PRESETS,
    isValid: function (expression) {
      var raw = String(expression || "").trim();
      if (!raw || SCHEDULE_MACROS[raw.toLowerCase()]) return true;
      var fields = raw.split(/\s+/);
      return fields.length === 5 && fields.every(function (field) {
        return /^[0-9*/?,A-Za-z#LWH-]+$/.test(field);
      });
    },
    presetFor: function (expression) {
      var raw = String(expression || "").trim();
      return SCHEDULE_PRESETS.find(function (preset) { return preset.expression === raw; }) ||
        SCHEDULE_PRESETS[SCHEDULE_PRESETS.length - 1];
    },
    label: cronLabel
  };

  class AgentWorkflowController {
    /**
     * Manage the ordered workflow editor rendered inside a segment detail view.
     * @param {{api: Function}} options API adapter used by the shared frontend.
     */
    constructor(options) {
      this.api = options.api;
      this.segmentId = null;
      this.steps = [];
      this.agents = [];
      this.contentItems = [];
    }

    load(segmentId) {
      this.segmentId = segmentId;
      this.steps = [];
      this.agents = [];
      this.contentItems = [];
      this.resetView();

      return $.when(
        this.api("/segments/" + segmentId + "/workflow"),
        this.loadCatalog()
      )
        .done((workflowResponse) => {
          this.steps = workflowResponse[0] || [];
          this.render();
          this.bindEvents();
        })
        .fail((xhr) => {
          this.showError(this.errorMessage(xhr, "Could not load the agent workflow."), true);
        })
        .always(() => {
          $("#segment-workflow-loading").addClass("hidden");
        });
    }

    loadCatalog() {
      return this.api("/ai-agents/", { limit: 500, status: "ACTIVE" }).then((agents) => {
        this.agents = agents || [];
        if (!this.agents.some((agent) => String(agent.model_type || "").toUpperCase() === "RANKING_RECOMMENDATION")) {
          this.contentItems = [];
          return [];
        }
        return this.api("/content-items/", {
          limit: 100,
          tenant_id: C360.config.current && C360.config.current.tenantId
        }).done((contentItems) => {
          // Keep inactive items so an existing workflow reference cannot be
          // lost when the user saves unrelated changes.
          this.contentItems = contentItems || [];
        });
      });
    }

    resetView() {
      $("#segment-workflow-loading").removeClass("hidden");
      $("#segment-workflow-empty, #segment-workflow-list").addClass("hidden");
      $("#segment-workflow-error").addClass("hidden").removeClass("flex");
      $("#segment-workflow-error-message").text("");
      $("#segment-workflow-retry").off(".workflow").on("click.workflow", () => {
        if (this.segmentId) this.load(this.segmentId);
      });
      $("#segment-workflow-save-status")
        .addClass("hidden")
        .removeClass("inline-flex text-amber-600 text-emerald-600")
        .text("");
      $("#segment-workflow-summary")
        .removeClass("bg-red-50 text-red-700 bg-violet-100 text-violet-700")
        .addClass("bg-slate-100 text-slate-500")
        .text("Loading...");
      $("#btn-segment-workflow-save, #btn-segment-workflow-add").prop("disabled", true);
    }

    errorMessage(xhr, fallback) {
      var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
      if (detail) return detail;
      if (xhr && xhr.status >= 500) {
        return "The workflow API returned HTTP " + xhr.status + ". Check customer360-api logs and restart the API after updating agent metadata schemas.";
      }
      return fallback;
    }

    showError(message, fatal) {
      $("#segment-workflow-error-message").text(message);
      $("#segment-workflow-error").removeClass("hidden").addClass("flex");
      if (fatal) {
        $("#segment-workflow-empty, #segment-workflow-list").addClass("hidden");
        $("#btn-segment-workflow-save, #btn-segment-workflow-add").prop("disabled", true);
      }
      $("#segment-workflow-summary")
        .removeClass("bg-slate-100 text-slate-500")
        .addClass("bg-red-50 text-red-700")
        .text("Needs attention");
    }

    markDirty() {
      $("#segment-workflow-save-status")
        .removeClass("hidden text-emerald-600")
        .addClass("inline-flex text-amber-600")
        .text("Unsaved changes");
    }

    agentFor(agentCode) {
      return this.agents.find((agent) => agent.agent_code === agentCode) || null;
    }

    supportsCandidateContent(agentCode, step) {
      var agent = this.agentFor(agentCode);
      var modelType = agent && agent.model_type || step && (step.agent_model_type || step.model_type);
      return String(modelType || "").trim().toUpperCase() === "RANKING_RECOMMENDATION";
    }

    isValidSchedule(expression) {
      var raw = String(expression || "").trim();
      if (!raw || SCHEDULE_MACROS[raw.toLowerCase()]) return true;
      var fields = raw.split(/\s+/);
      return fields.length === 5 && fields.every((field) => /^[0-9*/?,A-Za-z#LWH-]+$/.test(field));
    }

    schedulePresetFor(expression) {
      var raw = String(expression || "").trim();
      return SCHEDULE_PRESETS.find((preset) => preset.expression === raw) ||
        SCHEDULE_PRESETS[SCHEDULE_PRESETS.length - 1];
    }

    render() {
      var $list = $("#segment-workflow-list").empty();
      this.steps.forEach((step, index) => {
        $list.append(this.renderStep(step, index));
      });

      var hasSteps = this.steps.length > 0;
      $list.toggleClass("hidden", !hasSteps);
      $("#segment-workflow-empty").toggleClass("hidden", hasSteps);
      $("#segment-workflow-summary")
        .removeClass("bg-slate-100 text-slate-500")
        .addClass(hasSteps ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-500")
        .text(hasSteps ? this.steps.length + (this.steps.length === 1 ? " step" : " steps") : "Not configured");
      $("#btn-segment-workflow-save, #btn-segment-workflow-add").prop("disabled", false);
    }

    renderStep(step, index) {
      var catalogAgent = this.agentFor(step.agent_code);
      var agentName = step.agent_display_name || (catalogAgent && catalogAgent.display_name) || step.agent_code || "AI agent";
      var $row = $("<div></div>")
        .addClass("workflow-step relative pl-10 sm:pl-14")
        .attr("data-workflow-index", index);
      $("<span></span>")
        .addClass("absolute left-0 top-4 z-10 inline-flex h-8 w-8 items-center justify-center rounded-full border-2 border-violet-500 bg-white text-xs font-bold text-violet-700 shadow-sm sm:h-10 sm:w-10")
        .text(index + 1)
        .appendTo($row);

      var $card = $("<div></div>").addClass("overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xs transition-shadow hover:shadow-sm");
      var $header = $("<div></div>").addClass("flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 bg-slate-50/60 px-4 py-3 sm:px-5");
      var $title = $("<div></div>").addClass("min-w-0");
      var statusClass = step.is_active === false ? "bg-slate-100 text-slate-500" : "bg-emerald-100 text-emerald-700";
      var $titleLine = $("<div></div>").addClass("flex flex-wrap items-center gap-2");
      $("<h4></h4>").addClass("truncate text-sm font-bold text-slate-900").text(agentName).appendTo($titleLine);
      var $statusBadge = $("<span></span>").addClass("workflow-step-status rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide " + statusClass)
        .text(step.is_active === false ? "Paused" : "Active").appendTo($titleLine);
      $titleLine.appendTo($title);
      $("<div></div>").addClass("mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500").append(
        $("<code></code>").addClass("font-mono text-violet-700").text(step.agent_code || "-"),
        $("<span></span>").addClass("text-slate-300").text("•"),
        $("<span></span>").text(step.agent_model_type || (catalogAgent && catalogAgent.model_type) || "Agent")
      ).appendTo($title);

      var $actions = $("<div></div>").addClass("flex shrink-0 items-center gap-1.5");
      var action = function (label, className, title) {
        return $("<button></button>").attr({ type: "button", title: title, "aria-label": title }).addClass(className).text(label);
      };
      action("↑", "workflow-step-up rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 transition-colors hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:cursor-not-allowed disabled:opacity-40", "Move step up").prop("disabled", index === 0).appendTo($actions);
      action("↓", "workflow-step-down rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 transition-colors hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:cursor-not-allowed disabled:opacity-40", "Move step down").prop("disabled", index === this.steps.length - 1).appendTo($actions);
      action("Remove", "workflow-step-remove rounded border border-red-200 bg-white px-2 py-1 text-xs font-semibold text-red-600 transition-colors hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500", "Remove this step").appendTo($actions);
      $header.append($title, $actions).appendTo($row);

      var $grid = $("<div></div>").addClass("grid gap-4 p-4 sm:p-5 lg:grid-cols-12");
      var $agentLabel = $("<label></label>").addClass("block lg:col-span-5");
      $("<span></span>").addClass("mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500").text("AI agent").appendTo($agentLabel);
      var $agent = $("<select></select>").attr("id", "workflow-agent-" + index).addClass("workflow-agent w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/20");
      this.agents.forEach((agent) => {
        $("<option></option>").attr("value", agent.agent_code).text((agent.display_name || agent.agent_code) + " (" + agent.agent_code + ")").appendTo($agent);
      });
      if (!catalogAgent && step.agent_code) {
        $("<option></option>")
          .attr("value", step.agent_code)
          .text((step.agent_display_name || step.agent_code) + " (" + step.agent_code + ", currently inactive)")
          .appendTo($agent);
      }
      $agent.val(step.agent_code);
      $agentLabel.append($agent).appendTo($grid);

      var $orderLabel = $("<label></label>").addClass("block lg:col-span-3");
      $("<span></span>").addClass("mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500").text("Run order").appendTo($orderLabel);
      $("<input>").attr({ id: "workflow-order-" + index, type: "number", min: 1, step: 1, "aria-label": "Run order for " + agentName }).addClass("workflow-order w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/20").val(step.execution_order || index + 1).appendTo($orderLabel);
      $("<span></span>").addClass("mt-1 block text-[11px] text-slate-400").text("Lowest number runs first.").appendTo($orderLabel);
      $orderLabel.appendTo($grid);

      var $activeLabel = $("<label></label>")
        .attr("title", "Paused steps remain configured in this workflow but are skipped when the workflow runs.")
        .addClass("flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2.5 text-sm text-slate-700 transition-colors hover:border-violet-200 hover:bg-violet-50/50 lg:col-span-4");
      var $activeCopy = $("<span></span>").addClass("min-w-0");
      $("<span></span>").addClass("block text-xs font-semibold text-slate-700").text("Step status").appendTo($activeCopy);
      var $activeState = $("<span></span>").addClass("workflow-active-state mt-0.5 block text-[11px] font-medium text-emerald-700").text(step.is_active === false ? "Paused" : "Enabled").appendTo($activeCopy);
      $("<span></span>").addClass("mt-0.5 block text-[10px] leading-tight text-slate-400").text("Pause without removing its configuration").appendTo($activeCopy);
      var $activeToggle = $("<span></span>").addClass("relative inline-flex shrink-0 items-center");
      var $activeInput = $("<input>").attr({ type: "checkbox", "aria-label": "Enable " + agentName }).addClass("workflow-active peer sr-only").prop("checked", step.is_active !== false);
      $("<span></span>").addClass("h-6 w-11 rounded-full bg-slate-300 transition-colors peer-checked:bg-emerald-500 peer-focus-visible:ring-2 peer-focus-visible:ring-violet-500 peer-focus-visible:ring-offset-2").appendTo($activeToggle);
      $("<span></span>").addClass("pointer-events-none absolute left-1 h-4 w-4 rounded-full bg-white shadow-sm transition-transform peer-checked:translate-x-5").appendTo($activeToggle);
      $activeToggle.prepend($activeInput);
      $activeLabel.append($activeCopy, $activeToggle);
      $activeLabel.appendTo($grid);

      var refreshActiveState = () => {
        var enabled = $activeInput.is(":checked");
        $activeState.text(enabled ? "Enabled" : "Paused")
          .toggleClass("text-emerald-700", enabled)
          .toggleClass("text-amber-700", !enabled);
        $statusBadge.text(enabled ? "Active" : "Paused")
          .toggleClass("bg-emerald-100 text-emerald-700", enabled)
          .toggleClass("bg-slate-100 text-slate-500", !enabled);
        $activeLabel.toggleClass("border-emerald-200 bg-emerald-50/60", enabled)
          .toggleClass("border-slate-200 bg-slate-50/70", !enabled);
      };
      $activeInput.on("change", refreshActiveState);
      refreshActiveState();

      var $scheduleLabel = $("<div></div>").addClass("block lg:col-span-12");
      var $scheduleHeading = $("<div></div>").addClass("mb-1 flex flex-wrap items-center justify-between gap-2");
      $("<span></span>").addClass("text-[11px] font-semibold uppercase tracking-wide text-slate-500").text("Schedule override").appendTo($scheduleHeading);
      $("<span></span>").addClass("text-[11px] text-slate-400").text("Optional").appendTo($scheduleHeading);
      $scheduleHeading.appendTo($scheduleLabel);
      var rawSchedule = String(step.schedule_definition || "").trim();
      var $schedulePreset = $("<select></select>").attr({ id: "workflow-schedule-preset-" + index, "aria-label": "Schedule preset for " + agentName }).addClass("workflow-schedule-preset w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/20");
      SCHEDULE_PRESETS.forEach((preset) => $("<option></option>").attr("value", preset.value).text(preset.label).appendTo($schedulePreset));
      $schedulePreset.val(this.schedulePresetFor(rawSchedule).value).appendTo($scheduleLabel);
      var $customSchedule = $("<div></div>").addClass("workflow-custom-schedule mt-2");
      var $scheduleInput = $("<input>").attr({
        id: "workflow-schedule-" + index,
        type: "text",
        maxlength: 100,
        placeholder: "e.g. 30 8 * * 1-5 or @daily",
        "aria-label": "Custom cron schedule for " + agentName,
        "aria-describedby": "workflow-schedule-help-" + index
      }).addClass("workflow-schedule w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/20").val(rawSchedule);
      $("<span></span>").attr("id", "workflow-schedule-help-" + index).addClass("mt-1 block text-[11px] text-slate-400").text("Use five fields: minute hour day-of-month month day-of-week.").appendTo($customSchedule);
      var $scheduleError = $("<span></span>").addClass("workflow-schedule-error mt-1 hidden block text-[11px] font-medium text-red-600").attr("role", "alert").text("Enter a valid five-field cron expression or supported @macro.").appendTo($customSchedule);
      $customSchedule.prepend($scheduleInput).appendTo($scheduleLabel);
      var $scheduleDescription = $("<span></span>").addClass("workflow-schedule-description mt-1 block text-[11px] text-slate-500");
      var $inherited = $("<span></span>").addClass("workflow-inherited-schedule mt-1 block text-[11px] text-slate-400");
      $scheduleLabel.append($scheduleDescription, $inherited).appendTo($grid);

      var $candidateSlot = $("<div></div>")
        .attr("data-candidate-slot", "true")
        .addClass("lg:col-span-12");
      var renderCandidateSection = (agentCode, candidateIds) => {
        $candidateSlot.empty();
        if (this.supportsCandidateContent(agentCode, step)) {
          this.renderCandidatePicker(
            $.extend({}, step, { candidate_content_item_ids: candidateIds }),
            index,
            agentName
          ).appendTo($candidateSlot);
          return;
        }
        var $message = $("<div></div>").addClass("rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-3");
        $("<div></div>").addClass("text-xs font-semibold text-slate-700").text("Candidate content is not used by this agent").appendTo($message);
        $("<p></p>").addClass("mt-1 text-[11px] leading-relaxed text-slate-500").text("This field is available only for RANKING_RECOMMENDATION agents. Select one of those agents to restrict its recommendations to specific content or products.").appendTo($message);
        $message.appendTo($candidateSlot);
      };
      renderCandidateSection($agent.val(), step.candidate_content_item_ids || []);
      $candidateSlot.appendTo($grid);

      var $configLabel = $("<label></label>").addClass("block lg:col-span-12");
      $("<span></span>").addClass("mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500").text("Agent configuration (JSON)").appendTo($configLabel);
      $("<textarea></textarea>").attr({ id: "workflow-configuration-" + index, rows: 5, spellcheck: "false", "aria-label": "JSON configuration for " + agentName }).addClass("workflow-configuration w-full resize-y rounded-lg border-0 bg-slate-900 px-3 py-3 font-mono text-xs leading-relaxed text-emerald-300 shadow-inner focus:outline-none focus:ring-2 focus:ring-violet-500").val(JSON.stringify(step.configuration || {}, null, 2)).appendTo($configLabel);
      $("<span></span>").addClass("mt-1 block text-[11px] text-slate-400").text("Optional JSON object for segment-specific parameters.").appendTo($configLabel);
      $grid.append($configLabel);
      $card.append($header, $grid).appendTo($row);

      var refreshSchedule = () => {
        var selectedAgent = this.agentFor($agent.val());
        var inherited = selectedAgent && selectedAgent.schedule_definition || step.agent_schedule_definition;
        var isCustom = $schedulePreset.val() === "custom";
        var selectedExpression = isCustom ? String($scheduleInput.val() || "").trim() : String($schedulePreset.val() || "");
        var isValid = this.isValidSchedule(selectedExpression);
        $scheduleInput.toggleClass("hidden", !isCustom)
          .attr("aria-invalid", isCustom && !isValid ? "true" : "false")
          .toggleClass("border-red-300 bg-red-50 focus:border-red-500 focus:ring-red-500/20", isCustom && !isValid)
          .toggleClass("border-slate-300 bg-white focus:border-violet-500 focus:ring-violet-500/20", !isCustom || isValid);
        $scheduleError.toggleClass("hidden", !isCustom || isValid);
        var effective = selectedExpression || inherited || null;
        $scheduleDescription.text("Runs: " + cronLabel(effective));
        $inherited.text("Effective schedule: " + cronLabel(effective));
      };
      $agent.on("change", () => {
        var candidateIds = this.supportsCandidateContent($agent.val(), step)
          ? (step.candidate_content_item_ids || [])
          : [];
        renderCandidateSection($agent.val(), candidateIds);
        refreshSchedule();
      });
      $schedulePreset.on("change", () => {
        if ($schedulePreset.val() !== "custom") $scheduleInput.val($schedulePreset.val());
        refreshSchedule();
      });
      $scheduleInput.on("input", () => {
        $schedulePreset.val("custom");
        refreshSchedule();
      });
      refreshSchedule();
      return $row;
    }

    renderCandidatePicker(step, index, agentName) {
      var $candidateLabel = $("<div></div>").addClass("block lg:col-span-12");
      var $heading = $("<div></div>").addClass("mb-1 flex flex-wrap items-center justify-between gap-2");
      $("<span></span>").addClass("text-[11px] font-semibold uppercase tracking-wide text-slate-500").text("Candidate content/products").appendTo($heading);
      var $count = $("<span></span>").addClass("workflow-candidate-count text-[11px] font-semibold text-violet-700").text("0 selected").appendTo($heading);
      $heading.appendTo($candidateLabel);

      var selectedIds = (step.candidate_content_item_ids || []).map(String);
      var items = this.contentItems.slice();
      selectedIds.forEach((id) => {
        if (!items.some((item) => String(item.content_item_id) === id)) {
          items.push({ content_item_id: id, title: "Previously selected content", item_type: "unavailable", status_code: 0 });
        }
      });

      var $candidate = $("<select></select>").attr({ id: "workflow-candidates-" + index, multiple: "multiple", "aria-hidden": "true", tabindex: "-1" }).addClass("workflow-candidates hidden");
      items.forEach((item) => $("<option></option>").attr("value", item.content_item_id).text(item.title || item.content_item_id).appendTo($candidate));
      $candidate.val(selectedIds).appendTo($candidateLabel);
      var $search = $("<input>").attr({ type: "search", placeholder: items.length ? "Search content or products..." : "No content items available", "aria-label": "Search candidate content for " + agentName }).prop("disabled", !items.length).addClass("workflow-candidate-search w-full rounded-t-lg border-0 border-b border-slate-200 bg-slate-50/60 px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400 focus:border-violet-300 focus:outline-none focus:ring-2 focus:ring-violet-500/20");
      var $list = $("<div></div>").addClass("workflow-candidate-list max-h-52 overflow-y-auto p-1");
      var $noResults = $("<div></div>").addClass("workflow-candidate-no-results px-3 py-4 text-center text-xs text-slate-500").text(items.length ? "No matching content items." : "No active content items are available for this tenant.").toggleClass("hidden", !!items.length);
      items.forEach((item) => {
        var id = String(item.content_item_id);
        var selected = selectedIds.indexOf(id) !== -1;
        var active = Number(item.status_code) === 1;
        var title = item.title || id;
        var $option = $("<label></label>").attr("data-search-text", (title + " " + (item.item_type || "") + " " + id).toLowerCase()).addClass("workflow-candidate-option flex cursor-pointer items-start gap-2 rounded-md px-2 py-2 transition-colors hover:bg-violet-50 has-[:checked]:bg-violet-50");
        $("<input>").attr({ type: "checkbox", value: id, "aria-label": "Select " + title }).addClass("workflow-candidate-checkbox mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-violet-700 focus:ring-violet-500").prop({ checked: selected, disabled: !active && !selected }).appendTo($option);
        var $text = $("<span></span>").addClass("min-w-0 flex-1");
        $("<span></span>").addClass("block truncate text-xs font-medium text-slate-700").text(title).appendTo($text);
        $("<span></span>").addClass("mt-0.5 block text-[10px] text-slate-400").text((item.item_type || "content") + (active ? "" : " · inactive, already selected")).appendTo($text);
        $option.append($text).appendTo($list);
      });
      var $picker = $("<div></div>").addClass("rounded-lg border border-slate-300 bg-white").append($search, $list, $noResults).appendTo($candidateLabel);
      var $selected = $("<div></div>").addClass("workflow-selected-candidates mt-2 flex flex-wrap gap-1.5").appendTo($candidateLabel);
      $("<span></span>").addClass("mt-1 block text-[11px] text-slate-400").text(items.length ? "Select active items to restrict recommendations. Selected inactive items are preserved." : "No content items are available for this tenant.").appendTo($candidateLabel);

      var renderSelected = () => {
        var ids = ($candidate.val() || []).map(String);
        $selected.empty();
        if (!ids.length) $("<span></span>").addClass("text-[11px] italic text-slate-400").text("No content restrictions; the agent may use any eligible item.").appendTo($selected);
        ids.forEach((id) => {
          var item = items.find((candidate) => String(candidate.content_item_id) === id);
          var title = item ? (item.title || id) : id;
          var $chip = $("<span></span>").addClass("inline-flex max-w-full items-center gap-1 rounded-md border border-violet-200 bg-violet-50 px-2 py-1 text-[11px] text-violet-700");
          $("<span></span>").addClass("max-w-[14rem] truncate").text(title).appendTo($chip);
          $("<button></button>").attr({ type: "button", "data-content-id": id, "aria-label": "Remove " + title }).addClass("workflow-candidate-remove rounded p-0.5 text-violet-500 transition-colors hover:bg-violet-100 hover:text-violet-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500").text("×").appendTo($chip);
          $chip.appendTo($selected);
        });
        $count.text(ids.length + " selected");
      };
      var sync = () => {
        var ids = [];
        $list.find(".workflow-candidate-checkbox:checked").each(function () { ids.push(String($(this).val())); });
        $candidate.val(ids);
        renderSelected();
      };
      $search.on("input", () => {
        var query = String($search.val() || "").trim().toLowerCase();
        var visible = 0;
        $list.find(".workflow-candidate-option").each(function () {
          var matches = !query || String($(this).attr("data-search-text") || "").indexOf(query) !== -1;
          $(this).toggleClass("hidden", !matches);
          if (matches) visible += 1;
        });
        $noResults.toggleClass("hidden", visible > 0);
      });
      $list.on("change", ".workflow-candidate-checkbox", sync);
      $selected.on("click", ".workflow-candidate-remove", (event) => {
        var id = String($(event.currentTarget).attr("data-content-id"));
        $list.find(".workflow-candidate-checkbox").filter(function () { return String($(this).val()) === id; }).prop("checked", false);
        sync();
        this.markDirty();
      });
      renderSelected();
      return $candidateLabel;
    }

    collectSteps() {
      var seenAgents = {};
      var steps = [];
      var invalidConfiguration = false;
      $("#segment-workflow-list .workflow-step").each((index, element) => {
        var $row = $(element);
        var agentCode = String($row.find(".workflow-agent").val() || "");
        if (!agentCode || seenAgents[agentCode]) throw new Error("Each agent may appear only once in a workflow.");
        seenAgents[agentCode] = true;
        var executionOrder = Number($row.find(".workflow-order").val());
        if (!Number.isInteger(executionOrder) || executionOrder < 1) throw new Error("Run order must be a positive whole number.");
        var scheduleDefinition = String($row.find(".workflow-schedule").val() || "").trim() || null;
        if (scheduleDefinition && !this.isValidSchedule(scheduleDefinition)) {
          throw new Error("Step " + (index + 1) + " has an invalid schedule. Use a five-field cron expression or supported @macro.");
        }
        var configuration;
        try {
          configuration = JSON.parse(String($row.find(".workflow-configuration").val() || "{}"));
        } catch (error) {
          invalidConfiguration = true;
          return;
        }
        if (!configuration || Array.isArray(configuration) || typeof configuration !== "object") {
          invalidConfiguration = true;
          return;
        }
        steps.push({
          agent_code: agentCode,
          execution_order: executionOrder,
          is_active: $row.find(".workflow-active").is(":checked"),
          schedule_definition: scheduleDefinition,
          candidate_content_item_ids: $row.find(".workflow-candidates").val() || [],
          configuration: configuration
        });
      });
      if (invalidConfiguration) throw new Error("Every agent configuration must be a JSON object.");
      var orders = steps.map((step) => step.execution_order);
      if (orders.length !== new Set(orders).size) throw new Error("Each workflow step must have a unique run order.");
      return steps.sort((left, right) => left.execution_order - right.execution_order);
    }

    bindEvents() {
      $("#btn-segment-workflow-add, #btn-segment-workflow-save").off(".workflow");
      $("#segment-workflow-list").off(".workflow");
      $("#btn-segment-workflow-add").on("click.workflow", () => {
        var used = {};
        this.steps.forEach((step) => { used[step.agent_code] = true; });
        var activeAgents = this.agents.filter((agent) => String(agent.status).toUpperCase() === "ACTIVE");
        var available = activeAgents.find((agent) => !used[agent.agent_code]);
        if (!available) {
          this.showError(
            activeAgents.length
              ? "All active agents are already configured for this segment."
              : "No active AI agents are available. Activate an agent in the AI Agent catalog first."
          );
          return;
        }
        this.steps.push({ agent_code: available.agent_code, execution_order: this.steps.length + 1, is_active: true, schedule_definition: null, candidate_content_item_ids: [], configuration: {} });
        $("#segment-workflow-error").addClass("hidden").removeClass("flex");
        $("#segment-workflow-error-message").text("");
        this.render();
        this.markDirty();
      });
      $("#btn-segment-workflow-save").on("click.workflow", () => {
        var steps;
        try {
          steps = this.collectSteps();
        } catch (error) {
          this.showError(error.message, false);
          return;
        }
        var $button = $("#btn-segment-workflow-save");
        $button.prop("disabled", true).text("Saving...");
        this.api("/segments/" + this.segmentId + "/workflow", { steps: steps }, "PUT")
          .done((saved, _textStatus, xhr) => {
            this.steps = saved || [];
            this.render();
            var runId = xhr && xhr.getResponseHeader("X-Dagster-Run-Id");
            $("#segment-workflow-save-status")
              .removeClass("hidden text-amber-600")
              .addClass("inline-flex text-emerald-600")
              .attr("title", runId ? "Dagster run ID: " + runId : "")
              .text(runId ? "Saved · run queued" : "Saved");
            setTimeout(() => $("#segment-workflow-save-status").addClass("hidden"), 2500);
          })
          .fail((xhr) => this.showError(this.errorMessage(xhr, "Could not save the agent workflow."), false))
          .always(() => $button.prop("disabled", false).text("Save workflow"));
      });
      $("#segment-workflow-list").on("click.workflow", ".workflow-step-remove", (event) => {
        var index = Number($(event.currentTarget).closest(".workflow-step").attr("data-workflow-index"));
        this.steps.splice(index, 1);
        this.render();
        this.markDirty();
      });
      $("#segment-workflow-list").on("click.workflow", ".workflow-step-up, .workflow-step-down", (event) => {
        var index = Number($(event.currentTarget).closest(".workflow-step").attr("data-workflow-index"));
        var target = $(event.currentTarget).hasClass("workflow-step-up") ? index - 1 : index + 1;
        if (target < 0 || target >= this.steps.length) return;
        var moved = this.steps.splice(index, 1)[0];
        this.steps.splice(target, 0, moved);
        this.steps.forEach((step, position) => { step.execution_order = position + 1; });
        this.render();
        this.markDirty();
      });
      $("#segment-workflow-list").on("input.workflow change.workflow", ".workflow-step input:not(.workflow-candidate-search), .workflow-step select, .workflow-step textarea", () => this.markDirty());
    }
  }

  C360.AgentWorkflowController = AgentWorkflowController;
})(window.C360);