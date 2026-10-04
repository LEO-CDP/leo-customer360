/* Customer 360 Admin -- Segments (Audience Builder) list + detail view. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var currentSegmentId = null;

  function processedByLabel(v) { return v === "ai_agent" ? "AI Agent" : "Human"; }
  function processedByBadgeClass(v) { return v === "ai_agent" ? "bg-purple-100 text-purple-700" : "bg-slate-100 text-slate-600"; }
  function activeLabel(v) { return v ? "Active" : "Inactive"; }
  function activeBadgeClass(v) { return v ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500"; }

  var segmentsById = {};
  var editingSegmentId = null;
  // True once the AI filled the builder in this form session; saved as processed_by='ai_agent'.
  var rulesFromAi = false;
  // "Describe with AI" state for this form session. Untrusted text: render with .text()/.val() only.
  var aiSoFar = null;
  var aiLastQuestion = null;
  var aiRequest = null;
  var aiRequestSequence = 0;
  var aiLockedControls = [];
  var queryBuilderReady = false;
  var segmentAttributes = [];
  var attributeLoadSequence = 0;
  var workflowController = new C360.AgentWorkflowController({ api: api });

  function sqlQuote(value) {
    return "'" + String(value == null ? "" : value).replace(/'/g, "''") + "'";
  }

  function sqlValue(value, dataType) {
    var type = String(dataType || "TEXT").toUpperCase();
    if (type === "BOOLEAN" || type === "BOOL") {
      var booleanValue = String(value).trim().toLowerCase();
      if (value !== true && value !== false && booleanValue !== "true" && booleanValue !== "false" && booleanValue !== "1" && booleanValue !== "0") {
        throw new Error("Enter a valid boolean value.");
      }
      return value === true || booleanValue === "true" || booleanValue === "1" ? "TRUE" : "FALSE";
    }
    if (["SMALLINT", "INTEGER", "INT", "BIGINT", "SERIAL", "BIGSERIAL"].indexOf(type) !== -1) {
      var number = Number(value);
      if (!isFinite(number) || !Number.isInteger(number)) throw new Error("Enter a valid integer.");
      return String(number);
    }
    if (["NUMERIC", "DECIMAL", "REAL", "FLOAT", "DOUBLE", "DOUBLE PRECISION", "NUMBER"].indexOf(type) !== -1) {
      var decimal = Number(value);
      if (!isFinite(decimal)) throw new Error("Enter a valid number.");
      return String(decimal);
    }
    if (type === "JSON" || type === "JSONB") {
      try {
        JSON.parse(String(value));
      } catch (error) {
        throw new Error("Enter valid JSON.");
      }
      return sqlQuote(value) + (type === "JSONB" ? "::jsonb" : "::json");
    }
    if (type === "ARRAY") {
      throw new Error("Array attributes are not available for segment rules.");
    }
    if (type === "DATE" || type === "TIME" || type === "TIMESTAMP" || type === "TIMESTAMPTZ" || type === "DATETIME") {
      return sqlQuote(value);
    }
    if (!String(value == null ? "" : value).trim()) {
      throw new Error("Enter a value.");
    }
    if (typeof value === "number" && !isFinite(value)) {
      throw new Error("Enter a valid number.");
    }
    if (typeof value === "number") {
      return String(value);
    }
    return sqlQuote(value);
  }

  function ruleSql(rule, filtersById) {
    if (!rule || !rule.id || !filtersById[rule.id]) throw new Error("Choose a valid profile attribute for every rule.");
    var filter = filtersById[rule.id];
    var field = filter.field;
    var operator = rule.operator;
    var values;
    if (Array.isArray(rule.value)) {
      values = rule.value;
    } else if ((operator === "in" || operator === "not_in") && typeof rule.value === "string") {
      values = rule.value.split(",").map(function (value) { return $.trim(value); });
    } else {
      values = [rule.value];
    }
    var value;
    if (operator === "is_null") return field + " IS NULL";
    if (operator === "is_not_null") return field + " IS NOT NULL";
    if (operator === "is_empty") return field + " = ''";
    if (operator === "is_not_empty") return field + " <> ''";
    if (operator === "in" || operator === "not_in") {
      values = values.filter(function (item) { return item !== null && typeof item !== "undefined" && String(item).trim() !== ""; });
      if (!values.length) throw new Error("Enter at least one value for each list rule.");
      value = values.map(function (item) { return sqlValue(item, filter.data_type); }).join(", ");
      return field + (operator === "in" ? " IN (" : " NOT IN (") + value + ")";
    }
    if (operator === "between" || operator === "not_between") {
      if (values.length < 2) throw new Error("Enter two values for a range rule.");
      return field + (operator === "between" ? " BETWEEN " : " NOT BETWEEN ") +
        sqlValue(values[0], filter.data_type) + " AND " + sqlValue(values[1], filter.data_type);
    }
    var operators = {
      equal: "=", not_equal: "<>", less: "<", less_or_equal: "<=",
      greater: ">", greater_or_equal: ">=", begins_with: "LIKE",
      contains: "LIKE", ends_with: "LIKE"
    };
    if (!operators[operator]) throw new Error("Unsupported rule operator.");
    var scalar = values[0];
    if (operator === "begins_with") scalar = String(scalar) + "%";
    if (operator === "contains") scalar = "%" + String(scalar) + "%";
    if (operator === "ends_with") scalar = "%" + String(scalar);
    return field + " " + operators[operator] + " " + sqlValue(scalar, filter.data_type);
  }

  function rulesSql(group, filtersById) {
    if (!group || !group.rules || !group.rules.length) throw new Error("Add at least one audience rule.");
    return "(" + group.rules.map(function (rule) {
      return rule.rules ? rulesSql(rule, filtersById) : ruleSql(rule, filtersById);
    }).join(" " + String(group.condition || "AND").toUpperCase() + " ") + ")";
  }

  function queryBuilderFilters(attributes) {
    return attributes.filter(function (attribute) {
      return $.fn.queryBuilder.catalogType(attribute.data_type).category !== "array";
    }).map(function (attribute) {
      var typeInfo = $.fn.queryBuilder.catalogType(attribute.data_type);
      var operators;
      if (typeInfo.category === "integer" || typeInfo.category === "number") {
        operators = ["equal", "not_equal", "less", "less_or_equal", "greater", "greater_or_equal", "between", "not_between", "in", "not_in", "is_null", "is_not_null"];
      } else if (typeInfo.category === "datetime") {
        operators = ["equal", "not_equal", "less", "less_or_equal", "greater", "greater_or_equal", "between", "not_between", "in", "not_in", "is_null", "is_not_null"];
      } else if (typeInfo.category === "boolean") {
        operators = ["equal", "not_equal", "is_null", "is_not_null"];
      } else if (typeInfo.category === "json") {
        operators = ["equal", "not_equal", "is_null", "is_not_null"];
      } else {
        operators = ["equal", "not_equal", "contains", "begins_with", "ends_with", "is_empty", "is_not_empty", "is_null", "is_not_null", "in", "not_in"];
      }
      var filter = $.extend({}, typeInfo, {
        id: attribute.field,
        label: attribute.name || attribute.field,
        data_type: attribute.data_type || "TEXT",
        value_separator: ",",
        operators: operators,
        optgroup: attribute.attribute_group
      });
      var isDateField = String(attribute.data_type || "").trim().toUpperCase() === "DATE" ||
        attribute.field === "last_activity_at";
      if (isDateField) {
        filter.type = "date";
        filter.input = "date";
        filter.placeholder = "YYYY-MM-DD";
        filter.validation = $.extend({}, filter.validation, {
          format: "YYYY-MM-DD"
        });
      }
      if (attribute.field === "status_code") {
        filter.type = "integer";
        filter.input = "select";
        filter.values = { 1: "Active", 0: "Inactive" };
        filter.operators = ["equal", "not_equal", "is_null", "is_not_null"];
      }
      return filter;
    });
  }

  function normalizeSegmentRules(rules) {
    if (!rules || typeof rules !== "object") return rules;
    return $.extend({}, rules, {
      rules: (rules.rules || []).map(function (rule) {
        if (rule && rule.rules) return normalizeSegmentRules(rule);
        return $.extend({}, rule, { id: rule && (rule.id || rule.field) });
      })
    });
  }

  function loadSegmentAttributes(domain, rules) {
    var $builder = $("#segment-query-builder");
    var loadSequence = ++attributeLoadSequence;
    $("#segment-query-builder-loading").removeClass("hidden");
    if (queryBuilderReady && typeof $builder.queryBuilder === "function") $builder.queryBuilder("destroy");
    $builder.empty();
    queryBuilderReady = false;
    var params = domain && domain !== "all" ? { domain: domain } : {};
    return api("/segments/segmentable-profile-attributes", params)
      .done(function (attributes) {
        if (loadSequence !== attributeLoadSequence) return;
        segmentAttributes = attributes || [];
        $("#segment-form-attribute-count").text(segmentAttributes.length + " attributes available");
        if (typeof $builder.queryBuilder !== "function") {
          $("#segment-form-error").removeClass("hidden").text("jQuery QueryBuilder could not be loaded.");
          return;
        }
        if (!segmentAttributes.length) {
          $("#segment-form-error").removeClass("hidden").text("No segmentable profile attributes are available for this domain.");
          $("#segment-query-builder-loading").addClass("hidden");
          return;
        }
        try {
          $builder.queryBuilder({
            filters: queryBuilderFilters(segmentAttributes),
            allow_empty: true,
            plugins: ["tw-tooltip-errors"]
          });
          queryBuilderReady = true;
          if (rules && rules.rules && rules.rules.length) {
            $builder.queryBuilder("setRules", normalizeSegmentRules(rules));
          }
        } catch (error) {
          queryBuilderReady = false;
          $("#segment-form-error").removeClass("hidden").text("Could not start the rule builder: " + error.message);
        }
        $("#segment-query-builder-loading").addClass("hidden");
      })
      .fail(function (xhr) {
        $("#segment-query-builder-loading").addClass("hidden");
        showApiError("loading segment attributes", xhr);
      });
  }

  function closeSegmentForm() {
    attributeLoadSequence += 1;
    aiRequestSequence += 1;
    if (aiRequest) {
      var request = aiRequest;
      aiRequest = null;
      request.abort();
    }
    setSegmentAiProcessing(false);
    $("#segment-form-modal").addClass("hidden");
    if (queryBuilderReady) {
      $("#segment-query-builder").queryBuilder("destroy");
      queryBuilderReady = false;
    }
  }

  function openSegmentForm(segment) {
    editingSegmentId = segment ? segment.segment_id : null;
    $("#segment-form-title").text(segment ? "Edit segment" : "Create segment");
    $("#segment-form-save-label").text(segment ? "Save changes" : "Create segment");
    $("#segment-form-error").addClass("hidden").text("");
    $("#segment-form-name").val(segment ? segment.segment_name : "");
    $("#segment-form-tag").val(segment ? segment.segment_tag : "");
    $("#segment-form-description").val(segment ? (segment.description || "") : "");
    $("#segment-form-domain").val(segment ? (segment.domain || "all") : "all");
    rulesFromAi = false;
    $("#segment-form-ai-text").val("");
    $("#segment-form-ai-result").addClass("hidden");
    resetAiState();
    $("#segment-form-modal").removeClass("hidden");
    loadSegmentAttributes(segment ? segment.domain : "all", segment ? segment.json_rules : null);
  }

  // Sync the AI state into the UI; call after every state change.
  function renderAiState() {
    var hasState = !!(aiSoFar || aiLastQuestion);
    if (aiSoFar) {
      $("#segment-form-ai-sofar-wrap").removeClass("hidden");
      $("#segment-form-ai-sofar").val(aiSoFar);
    } else {
      $("#segment-form-ai-sofar-wrap").addClass("hidden");
      $("#segment-form-ai-sofar").val("");
    }
    if (aiLastQuestion) {
      $("#segment-form-ai-question").removeClass("hidden").text(aiLastQuestion);
    } else {
      $("#segment-form-ai-question").addClass("hidden").text("");
    }
    $("#btn-segment-form-ai-reset").toggleClass("hidden", !hasState);
    $("#segment-form-ai-label").text(hasState ? "Update rules" : "Generate rules");
  }

  // Clears the AI state only, not the builder or the name/tag fields.
  function resetAiState() {
    aiSoFar = null;
    aiLastQuestion = null;
    renderAiState();
  }

  function setSegmentAiProcessing(processing) {
    var $modal = $("#segment-form-modal");
    var $progress = $("#segment-form-ai-progress");
    var $aiButton = $("#btn-segment-form-ai");

    if (processing) {
      aiLockedControls = [];
      $modal.find("input, textarea, select, button").each(function () {
        if (this.id === "btn-segment-form-close") return;
        var $control = $(this);
        aiLockedControls.push({
          element: this,
          readonly: $control.prop("readonly"),
          disabled: $control.prop("disabled")
        });
        if (this.tagName === "INPUT" || this.tagName === "TEXTAREA") {
          $control.prop("readonly", true);
        } else {
          $control.prop("disabled", true);
        }
        $control.addClass("segment-ai-locked");
      });
      $modal.attr("aria-busy", "true");
      $progress.removeClass("hidden").attr("aria-busy", "true");
      $aiButton.addClass("opacity-60 cursor-not-allowed");
      $("#segment-form-ai-spinner").removeClass("hidden");
      $("#segment-form-ai-label").text("Processing...");
      return;
    }

    aiLockedControls.forEach(function (control) {
      var $control = $(control.element);
      $control.prop("readonly", control.readonly).prop("disabled", control.disabled)
        .removeClass("segment-ai-locked");
    });
    aiLockedControls = [];
    $modal.removeAttr("aria-busy");
    $progress.addClass("hidden").attr("aria-busy", "false");
    $aiButton.removeClass("opacity-60 cursor-not-allowed");
    $("#segment-form-ai-spinner").addClass("hidden");
  }

  function showAiResult(kind, message, suggestions) {
    var styles = {
      valid: "border border-emerald-200 bg-emerald-50 text-emerald-800",
      needs_clarification: "border border-amber-200 bg-amber-50 text-amber-800",
      rejected: "border border-red-200 bg-red-50 text-red-700"
    };
    // .text(), never .html(): untrusted.
    $("#segment-form-ai-result").removeClass("hidden " + Object.keys(styles).map(function (k) { return styles[k]; }).join(" "))
      .addClass(styles[kind] || styles.rejected);
    $("#segment-form-ai-message").text(message || "");
    var hasSuggestions = !!(suggestions && suggestions.length);
    $("#segment-form-ai-suggestions").toggleClass("hidden", !hasSuggestions)
      .text(hasSuggestions ? "Options: " + suggestions.join(", ") : "");
  }

  // Text -> validated json_rules; rulesSql() on save stays the only SQL path.
  function generateRulesFromText() {
    var description = $.trim($("#segment-form-ai-text").val());
    if (!description) {
      showAiResult("needs_clarification", "Describe the customers you want in this segment.");
      return;
    }
    if (!queryBuilderReady) {
      showAiResult("needs_clarification", "The rule builder is still loading. Try again in a moment.");
      return;
    }
    var domain = $("#segment-form-domain").val() || "all";
    // Pick up user edits to "So far".
    aiSoFar = $.trim($("#segment-form-ai-sofar").val()) || null;
    var currentRules = null;
    try {
      // null unless the builder is valid.
      var existingRules = $("#segment-query-builder").queryBuilder("getRules");
      if (existingRules && existingRules.rules && existingRules.rules.length) currentRules = existingRules;
    } catch (error) {
      currentRules = null;
    }
    var requestSequence = ++aiRequestSequence;
    setSegmentAiProcessing(true);
    aiRequest = api("/segments/from-description", {
      description: description,
      domain: domain,
      so_far: aiSoFar,
      last_question: aiLastQuestion,
      current_rules: currentRules
    }, "POST")
      .done(function (result) {
        if (requestSequence !== aiRequestSequence) return;
        aiSoFar = (result && result.so_far) || aiSoFar;
        aiLastQuestion = (result && result.validation_status === "needs_clarification") ? (result.question || null) : null;
        if (!result || result.validation_status !== "valid" || !result.ready_for_segment_persistence) {
          showAiResult(result && result.validation_status, (result && (result.question || result.interpretation)) ||
            "Could not turn that description into rules.", result && result.suggestions);
          return;
        }
        try {
          $("#segment-query-builder").queryBuilder("setRules", normalizeSegmentRules(result.json_rules));
        } catch (error) {
          showAiResult("rejected", "The generated rules could not be loaded: " + error.message);
          return;
        }
        rulesFromAi = true;
        if (!$.trim($("#segment-form-name").val()) && result.segment_name) $("#segment-form-name").val(result.segment_name);
        if (!$.trim($("#segment-form-tag").val()) && result.segment_tag) $("#segment-form-tag").val(result.segment_tag);
        if (!$.trim($("#segment-form-description").val())) $("#segment-form-description").val(description);
        $("#segment-form-ai-text").val("");
        showAiResult("valid", result.interpretation || "Rules generated. Review them below before saving.");
      })
      .fail(function (xhr) {
        if (requestSequence !== aiRequestSequence) return;
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        showAiResult("rejected", typeof detail === "string" ? detail : "The AI service could not generate rules right now.");
      })
      .always(function () {
        if (requestSequence !== aiRequestSequence) return;
        aiRequest = null;
        setSegmentAiProcessing(false);
        renderAiState();
      });
  }

  function submitSegmentForm() {
    var $error = $("#segment-form-error");
    $error.addClass("hidden").text("");
    var name = $.trim($("#segment-form-name").val());
    var tag = $.trim($("#segment-form-tag").val());
    if (!name || !tag) {
      $error.removeClass("hidden").text("Segment name and segment tag are required.");
      return;
    }
    if (!queryBuilderReady) {
      $error.removeClass("hidden").text("The rule builder is still loading.");
      return;
    }
    try {
      var rules = $("#segment-query-builder").queryBuilder("getRules", { allow_invalid: true });
      if (!rules || !rules.valid) throw new Error("Complete every audience rule before saving.");
      var filtersById = {};
      segmentAttributes.forEach(function (attribute) { filtersById[attribute.field] = attribute; });
      var sqlRules = rulesSql(rules, filtersById);
      var payload = {
        segment_name: name,
        segment_tag: tag,
        domain: $("#segment-form-domain").val() || "all",
        description: $.trim($("#segment-form-description").val()) || null,
        json_rules: rules,
        sql_rules: sqlRules,
        processed_by: rulesFromAi ? "ai_agent" : "human",
        is_active: true
      };
      if (!editingSegmentId) payload.tenant_id = C360.config.current.tenantId;
      var wasEditing = !!editingSegmentId;
      var savedSegmentId = editingSegmentId;
      var request = editingSegmentId
        ? api("/segments/" + editingSegmentId, payload, "PATCH")
        : api("/segments/", payload, "POST");
      $("#btn-segment-form-save").prop("disabled", true).addClass("opacity-60");
      request.done(function (response) {
        closeSegmentForm();
        loadList(false, true);
        var targetSegmentId = savedSegmentId || (response && response.segment_id);
        if (targetSegmentId && targetSegmentId === currentSegmentId) loadDetail(targetSegmentId);
        showToast(wasEditing ? "Segment updated" : "Segment created", "success");
        if (wasEditing && targetSegmentId) {
          currentSegmentId = targetSegmentId;
          refreshSegmentDetail();
        }
      }).fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || "Could not save segment.";
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : JSON.stringify(detail));
      }).always(function () {
        $("#btn-segment-form-save").prop("disabled", false).removeClass("opacity-60");
      });
    } catch (error) {
      $error.removeClass("hidden").text(error.message || "Please check the audience rules.");
    }
  }

  function segmentRowVm(s) {
    return $.extend({}, s, {
      domainLabel: fmt.domainLabel(s.domain),
      domainIcon: fmt.domainIcon(s.domain),
      domainIconBg: fmt.domainIconBg(s.domain),
      processedByLabel: processedByLabel(s.processed_by),
      processedByBadgeClass: processedByBadgeClass(s.processed_by),
      activeLabel: activeLabel(s.is_active),
      activeBadgeClass: activeBadgeClass(s.is_active),
      memberCountLabel: fmt.int(s.member_count),
      createdLabel: fmt.date(s.created_at)
    });
  }

  // Shared data-table component instance backing the segments list (see
  // static/js/data-table-view.js + list-view.js for the same pattern).
  var listDtv = C360.DataTableView.create({
    columns: [
      {
        label: "Segment", type: "identity", nameField: "segment_name", subField: "segment_tag", subStyle: "tag",
        avatarField: "domainIcon", avatarBgField: "domainIconBg", avatarTextClass: "text-base"
      },
      { label: "Business Domain", field: "domainLabel", capitalize: true },
      { label: "Created By", type: "badge", field: "processedByLabel", classField: "processedByBadgeClass" },
      { label: "Matched Profiles", field: "memberCountLabel" },
      { label: "Lifecycle", type: "badge", field: "activeLabel", classField: "activeBadgeClass" },
      { label: "Created On", field: "createdLabel", muted: true }
    ],
    rowVm: segmentRowVm,
    rowId: function (vm) { return vm.segment_id; },
    rowSelectorClass: "segment-row",
    resourceLabel: "segment",
    clientSide: true,
    clientSideLimit: 500,
    clientFilters: {
      q: function (vm, value) {
        var needle = String(value || "").toLowerCase().trim();
        if (!needle) return true;
        return (vm.segment_name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.segment_tag || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.description || "").toLowerCase().indexOf(needle) !== -1;
      },
      domain: function (vm, value) { return vm.domain === value; },
      status: function (vm, value) {
        return value === "active" ? !!vm.is_active : !vm.is_active;
      },
      owner: function (vm, value) { return vm.processed_by === value; },
      members: function (vm, value) {
        var memberCount = Number(vm.member_count) || 0;
        return value === "empty" ? memberCount === 0 : memberCount > 0;
      }
    },
    fetch: function (params) {
      return api("/segments/", params).done(function (segments) {
        (segments || []).forEach(function (segment) { segmentsById[segment.segment_id] = segment; });
      });
    },
    onRowClick: function (id) { C360.router.navigate("/segments/" + id); },
    onEdit: function (id) { openSegmentForm(segmentsById[id]); },
    editLabel: "Edit",
    onError: function (xhr) { showApiError("loading segments", xhr); },
    el: {
      thead: "#segments-thead",
      tbody: "#segments-tbody",
      loading: "#segments-list-loading",
      empty: "#segments-list-empty",
      countLabel: "#segments-count-label",
      loadMoreBtn: "#btn-segments-load-more"
    }
  });

  // Matched-profiles sub-table on the segment detail page renders plain
  // profile rows -- reuse list-view.js's columns/rowVm instead of
  // duplicating that config. Re-created per segment-detail render since
  // segment-details.html (and its #segment-matched-* ids) is itself
  // re-rendered on every loadDetail() call.
  var matchedDtv = null;
  function createMatchedDtv() {
    return C360.DataTableView.create({
      // The matched-profile table is a dense drill-down inside a tab panel.
      // Keep the main profile list's comfortable spacing, but render its
      // Profile identity with the same compact treatment as Domain.
      columns: C360.profileListView.columns.map(function (column, index) {
        return index === 0 ? $.extend({}, column, { compact: true }) : column;
      }),
      compactTable: true,
      rowVm: C360.profileListView.rowVm,
      rowId: function (vm) { return vm.master_profile_id; },
      rowSelectorClass: "profile-row",
      resourceLabel: "matched profile",
      fetch: function (params) { return api("/segments/" + currentSegmentId + "/matched-profiles", params); },
      onRowClick: function (id) { C360.router.navigate("/profiles/" + id); },
      onError: function (xhr) { showApiError("loading matched profiles", xhr); },
      el: {
        thead: "#segment-matched-thead",
        tbody: "#segment-matched-tbody",
        loading: "#segment-matched-loading",
        empty: "#segment-matched-empty",
        countLabel: "#segment-matched-count-label",
        loadMoreBtn: "#btn-segment-matched-load-more"
      }
    });
  }

  function segmentDetailVm(s) {
    var displaySql = s.final_generated_sql || s.sql_rules || "";
    return $.extend({}, s, {
      domainLabel: fmt.domainLabel(s.domain),
      processedByLabel: processedByLabel(s.processed_by) + (s.processed_by === "ai_agent" ? "" : " (SQL Query Builder)"),
      processedByBadgeClass: processedByBadgeClass(s.processed_by),
      activeLabel: activeLabel(s.is_active),
      activeBadgeClass: activeBadgeClass(s.is_active),
      memberCountLabel: fmt.int(s.member_count),
      lastComputedLabel: fmt.dateTime(s.last_computed_at),
      createdLabel: fmt.dateTime(s.created_at),
      updatedLabel: fmt.dateTime(s.updated_at),
      display_sql: fmt.formatSqlForDisplay(displaySql),
      hasSqlRules: !!displaySql,
      hasJsonRules: !!(s.json_rules && Object.keys(s.json_rules).length)
    });
  }

  function loadList(append, forceReload) {
    if (forceReload && listDtv.resetClientCache) listDtv.resetClientCache();
    return listDtv.load(append, forceReload);
  }

  function loadMatchedProfiles(segmentId, append) {
    if (!matchedDtv) return;
    return matchedDtv.load(append);
  }

  function activateSegmentInsightPanel(panelId, moveFocus) {
    var $tabs = $(".segment-insight-tab");
    var $panels = $(".segment-insight-panel");
    var $selected = $tabs.filter("[data-segment-panel='" + panelId + "']");
    if (!$selected.length) return;

    $tabs.each(function () {
      var selected = $(this).attr("data-segment-panel") === panelId;
      $(this)
        .attr("aria-selected", selected ? "true" : "false")
        .toggleClass("border-indigo-200 bg-indigo-50 text-indigo-700 shadow-sm", selected)
        .toggleClass("border-transparent text-slate-600", !selected);
    });
    $panels.each(function () {
      $(this).toggleClass("hidden", $(this).attr("id") !== panelId);
    });
    if (moveFocus) $selected.trigger("focus");
  }

  function bindSegmentInsightTabs() {
    var $tabs = $(".segment-insight-tab");
    $tabs.off(".segmentInsightTabs");
    $tabs.on("click.segmentInsightTabs", function () {
      activateSegmentInsightPanel($(this).attr("data-segment-panel"), false);
    });
    $tabs.on("keydown.segmentInsightTabs", function (event) {
      var $current = $(this);
      var $allTabs = $(".segment-insight-tab");
      var index = $allTabs.index($current);
      var nextIndex = index;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") nextIndex = (index + 1) % $allTabs.length;
      if (event.key === "ArrowUp" || event.key === "ArrowLeft") nextIndex = (index - 1 + $allTabs.length) % $allTabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = $allTabs.length - 1;
      if (nextIndex === index) return;
      event.preventDefault();
      activateSegmentInsightPanel($allTabs.eq(nextIndex).attr("data-segment-panel"), true);
    });
    activateSegmentInsightPanel("segment-panel-sql", false);
  }

  function loadDetail(segmentId) {
    currentSegmentId = segmentId;
    $("#segment-detail-content").empty();
    $("#segment-detail-loading").removeClass("hidden");

    api("/segments/" + segmentId)
      .done(function (segment) {
        segmentsById[segment.segment_id] = segment;
        $("#segment-detail-loading").addClass("hidden");
        $("#segment-detail-content").html(C360.templates.render("segment-details", segmentDetailVm(segment)));
        bindSegmentInsightTabs();
        workflowController.load(segmentId);
        matchedDtv = createMatchedDtv();
        matchedDtv.bindLoadMore();
        loadMatchedProfiles(segmentId, false);
      })
      .fail(function (xhr) {
        $("#segment-detail-loading").addClass("hidden");
        showApiError("loading segment detail", xhr);
      });
  }

  function showList() {
    $("#segment-view-detail").addClass("hidden");
    $("#segment-view-list").removeClass("hidden");
  }

  function showDetail(segmentId) {
    $("#segment-view-list").addClass("hidden");
    $("#segment-view-detail").removeClass("hidden");
    loadDetail(segmentId);
  }

  function load() {
    showList();
    loadList(false, true);
  }

  // Polling config for the async recompute-all job (see
  // POST /segments/admin/recompute-all + GET /segments/admin/recompute-status/{run_id}).
  // Dagster runs a full-table scan per active segment out-of-process, so
  // completion time depends on cdp_master_profiles size (could be 1M+ rows
  // in production) -- poll instead of blocking, and give up gracefully
  // after REFRESH_POLL_MAX_ATTEMPTS rather than polling forever.
  var REFRESH_POLL_INTERVAL_MS = 2000;
  var REFRESH_POLL_MAX_ATTEMPTS = 30; // ~1 minute at 2s/attempt


  // Builds a short " (N of M steps failed, ran Xs)" / " (ran Xs)" suffix
  // from the recompute-status response (see
  // core.utils.dagster_client.DagsterService.get_status) so the toast shows
  // more than a bare "success"/"failure" -- duration and, on failure, how
  // many steps failed (point the user at the Dagster UI for the full stack
  // trace rather than trying to surface it here).
  function formatRunDetail(result) {
    var parts = [];
    if (result.steps_failed) {
      var total = (result.steps_succeeded || 0) + result.steps_failed;
      parts.push(result.steps_failed + " of " + total + " steps failed");
    }
    if (typeof result.duration_seconds === "number") {
      parts.push("ran " + Math.round(result.duration_seconds) + "s");
    }
    return parts.length ? " (" + parts.join(", ") + ")" : "";
  }

  function setRefreshButtonBusy(selector, busy, label) {
    var $btn = $(selector);
    if (!$btn.length) return;
    var $label = $btn.children("span").last();
    if (busy) {
      if (!$btn.data("original-class")) { $btn.data("original-class", $btn.attr("class")); }
      if (!$btn.data("original-label")) { $btn.data("original-label", $label.length ? $label.text() : $btn.text()); }
      $btn.prop("disabled", true).attr("aria-busy", "true")
        .addClass("bg-slate-300 cursor-wait")
        .removeClass("hover:bg-slate-100 hover:bg-slate-50");
      if ($label.length) $label.text(label || "Refreshing...");
      else $btn.text(label || "Refreshing...");
    } else {
      $btn.prop("disabled", false).removeAttr("aria-busy")
        .attr("class", $btn.data("original-class") || $btn.attr("class"));
      if ($label.length) $label.text($btn.data("original-label") || "Refresh");
      else $btn.text($btn.data("original-label") || "Refresh");
    }
  }

  function pollRecomputeStatus(runId, attempt, options) {
    options = options || {};
    var buttonSelector = options.buttonSelector || "#btn-segments-refresh";
    api("/segments/admin/recompute-status/" + runId)
      .done(function (result) {
        if (result.status === "success") {
          setRefreshButtonBusy(buttonSelector, false);
          showToast("\u2713 Segment refresh completed" + formatRunDetail(result), "success");
          if (options.onSuccess) options.onSuccess();
          return;
        }
        if (result.status === "failure") {
          setRefreshButtonBusy(buttonSelector, false);
          showToast("\u2717 Segment refresh job failed" + formatRunDetail(result), "error");
          return;
        }
        // Still running: keep polling until REFRESH_POLL_MAX_ATTEMPTS is hit.
        if (attempt >= REFRESH_POLL_MAX_ATTEMPTS) {
          setRefreshButtonBusy(buttonSelector, false);
          showToast("Segment refresh is still running in the background; check back shortly.", "info");
          return;
        }
        setTimeout(function () { pollRecomputeStatus(runId, attempt + 1, options); }, REFRESH_POLL_INTERVAL_MS);
      })
      .fail(function (xhr) {
        setRefreshButtonBusy(buttonSelector, false);
        showApiError("checking segment refresh status", xhr);
      });
  }

  function refreshAllSegments() {
    setRefreshButtonBusy("#btn-segments-refresh", true, "Submitting...");

    // Fire-and-return: this only submits a Dagster run and gets a run_id
    // back immediately (see backend docstring on the endpoint) -- the
    // actual recompute happens out-of-process, so this call never blocks
    // on cdp_master_profiles size.
    api("/segments/admin/recompute-all", {}, "POST")
      .done(function (response) {
        setRefreshButtonBusy("#btn-segments-refresh", true, "Refreshing...");
        showToast("Segment refresh job submitted (run " + response.run_id + ")...", "info");
        pollRecomputeStatus(response.run_id, 1, {
          buttonSelector: "#btn-segments-refresh",
          onSuccess: function () { loadList(false, true); }
        });
      })
      .fail(function (xhr) {
        setRefreshButtonBusy("#btn-segments-refresh", false);
        showApiError("submitting segment refresh job", xhr);
      });
  }

  function clearListFilters() {
    [
      "#segments-search-input",
      "#segments-domain-filter",
      "#segments-status-filter",
      "#segments-owner-filter",
      "#segments-members-filter"
    ].forEach(function (selector) { $(selector).val(""); });
    listDtv.clearFilters();
  }

  function refreshSegmentDetail() {
    var segmentId = currentSegmentId;
    if (!segmentId) return;

    var buttonSelector = "#btn-segment-detail-refresh";
    setRefreshButtonBusy(buttonSelector, true, "Submitting...");
    api("/segments/" + encodeURIComponent(segmentId) + "/recompute", {}, "POST")
      .done(function (response) {
        setRefreshButtonBusy(buttonSelector, true, "Refreshing...");
        showToast("Segment refresh job submitted (run " + response.run_id + ")...", "info");
        pollRecomputeStatus(response.run_id, 1, {
          buttonSelector: buttonSelector,
          onSuccess: function () {
            if (currentSegmentId === segmentId && !$("#segment-view-detail").hasClass("hidden")) {
              loadDetail(segmentId);
            } else {
              loadList(false, true);
            }
          }
        });
      })
      .fail(function (xhr) {
        setRefreshButtonBusy(buttonSelector, false);
        showApiError("submitting segment refresh job", xhr);
      });
  }

  function bindEvents() {
    listDtv.bindRowClick();
    listDtv.bindLoadMore();
    listDtv.bindSearch("#segments-search-input", "q", 300);
    listDtv.bindSelect("#segments-domain-filter", "domain");
    listDtv.bindSelect("#segments-status-filter", "status");
    listDtv.bindSelect("#segments-owner-filter", "owner");
    listDtv.bindSelect("#segments-members-filter", "members");
    // Matched-profiles rows share the ".profile-row" click delegation
    // already bound once by C360.profileListView.bindEvents() (both tables render
    // the same profile columns/rowVm) -- only "load more" needs re-binding
    // here since #segment-matched-* is fresh DOM on every loadDetail().
    $(document).on("click", "#btn-back-to-segments", function () { C360.router.navigate("/segments"); });
    $(document).on("click", "#btn-segments-refresh", function () { refreshAllSegments(); });
    $(document).on("click", "#btn-segments-clear-filters", clearListFilters);
    $(document).on("click", "#btn-segment-detail-refresh", function () { refreshSegmentDetail(); });
    $(document).on("click", "#btn-copy-sql", function () {
      var sql = $("#segment-sql-content").text().trim();
      if (!sql) return;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(sql);
      }
      var $btn = $(this);
      var $text = $btn.find(".copy-sql-text");
      $text.text("Copied!");
      setTimeout(function () {
        $text.text("Copy SQL");
      }, 1500);
    });
    $(document).on("click", "#btn-segments-create", function () { openSegmentForm(null); });
    $(document).on("click", "#btn-segment-detail-edit", function () { openSegmentForm(segmentsById[currentSegmentId]); });
    $(document).on("click", "#btn-segment-form-save", submitSegmentForm);
    $(document).on("click", "#btn-segment-form-ai", generateRulesFromText);
    $(document).on("click", "#btn-segment-form-ai-reset", resetAiState);
    $(document).on("click", "#btn-segment-form-cancel, #btn-segment-form-close", closeSegmentForm);
    $(document).on("click", "#segment-form-modal", function (e) {
      if (e.target === this) closeSegmentForm();
    });
    $(document).on("change", "#segment-form-domain", function () {
      if (!$("#segment-form-modal").hasClass("hidden")) {
        rulesFromAi = false;  // the builder reloads empty; any AI rules are gone
        resetAiState();
        loadSegmentAttributes($(this).val(), null);
      }
    });
  }

  // Owns the "/segments" (list) and "/segments/:id" (detail) routes (see
  // router.js). Both share the single "view-segments" section; showList()/
  // showDetail() toggle the two sub-panels nested inside it, the same way a
  // React Router layout route renders a child <Outlet/>.
  C360.router.define("/segments", {
    section: "view-segments",
    tab: "segments",
    mount: function () { load(); }
  });
  C360.router.define("/segments/:id", {
    section: "view-segments",
    tab: "segments",
    mount: function (params) { showDetail(params.id); }
  });

  C360.segmentsView = { load: load, bindEvents: bindEvents };
})(window.C360);