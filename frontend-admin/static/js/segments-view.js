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
  var queryBuilderReady = false;
  var segmentAttributes = [];
  var attributeLoadSequence = 0;

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
        operators: operators
      });
      if (attribute.field === "status_code") {
        filter.type = "integer";
        filter.input = "select";
        filter.values = { 1: "Active", 0: "Inactive" };
        filter.operators = ["equal", "not_equal", "is_null", "is_not_null"];
      }
      return filter;
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
            $builder.queryBuilder("setRules", rules);
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
    $("#segment-form-modal").removeClass("hidden");
    loadSegmentAttributes(segment ? segment.domain : "all", segment ? segment.json_rules : null);
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
        processed_by: "human",
        is_active: true
      };
      if (!editingSegmentId) payload.tenant_id = C360.config.current.tenantId;
      var request = editingSegmentId
        ? api("/segments/" + editingSegmentId, payload, "PATCH")
        : api("/segments/", payload, "POST");
      $("#btn-segment-form-save").prop("disabled", true).addClass("opacity-60");
      request.done(function () {
        closeSegmentForm();
        loadList(false);
        showToast(editingSegmentId ? "Segment updated" : "Segment created", "success");
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
      { label: "Domain", field: "domainLabel", capitalize: true },
      { label: "Processed By", type: "badge", field: "processedByLabel", classField: "processedByBadgeClass" },
      { label: "Members", field: "memberCountLabel" },
      { label: "Status", type: "badge", field: "activeLabel", classField: "activeBadgeClass" },
      { label: "Created", field: "createdLabel", muted: true }
    ],
    rowVm: segmentRowVm,
    rowId: function (vm) { return vm.segment_id; },
    rowSelectorClass: "segment-row",
    resourceLabel: "segment",
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
      columns: C360.profileListView.columns,
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
      hasSqlRules: !!s.sql_rules,
      hasJsonRules: !!(s.json_rules && Object.keys(s.json_rules).length)
    });
  }

  function loadList(append) { return listDtv.load(append); }

  function loadMatchedProfiles(segmentId, append) {
    if (!matchedDtv) return;
    return matchedDtv.load(append);
  }

  function loadDetail(segmentId) {
    currentSegmentId = segmentId;
    $("#segment-detail-content").empty();
    $("#segment-detail-loading").removeClass("hidden");

    api("/segments/" + segmentId)
      .done(function (segment) {
        $("#segment-detail-loading").addClass("hidden");
        $("#segment-detail-content").html(C360.templates.render("segment-details", segmentDetailVm(segment)));
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
    loadList(false);
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

  function setRefreshButtonBusy(busy, label) {
    var $btn = $("#btn-segments-refresh");
    if (busy) {
      if (!$btn.data("original-class")) { $btn.data("original-class", $btn.attr("class")); }
      if (!$btn.data("original-text")) { $btn.data("original-text", $btn.text()); }
      $btn.attr("disabled", "disabled")
        .removeClass("bg-slate-100 hover:bg-slate-200")
        .addClass("bg-slate-300 cursor-wait")
        .text(label || "Refreshing...");
    } else {
      $btn.removeAttr("disabled").attr("class", $btn.data("original-class") || $btn.attr("class"));
      $btn.text($btn.data("original-text") || "Refresh");
    }
  }

  function pollRecomputeStatus(runId, attempt) {
    api("/segments/admin/recompute-status/" + runId)
      .done(function (result) {
        if (result.status === "success") {
          setRefreshButtonBusy(false);
          showToast("\u2713 Segment refresh completed" + formatRunDetail(result), "success");
          loadList(false); // reload to show updated member_count values
          return;
        }
        if (result.status === "failure") {
          setRefreshButtonBusy(false);
          showToast("\u2717 Segment refresh job failed" + formatRunDetail(result), "error");
          return;
        }
        // Still running: keep polling until REFRESH_POLL_MAX_ATTEMPTS is hit.
        if (attempt >= REFRESH_POLL_MAX_ATTEMPTS) {
          setRefreshButtonBusy(false);
          showToast("Segment refresh is still running in the background; check back shortly.", "info");
          return;
        }
        setTimeout(function () { pollRecomputeStatus(runId, attempt + 1); }, REFRESH_POLL_INTERVAL_MS);
      })
      .fail(function (xhr) {
        setRefreshButtonBusy(false);
        showApiError("checking segment refresh status", xhr);
      });
  }

  function refreshAllSegments() {
    setRefreshButtonBusy(true, "Submitting...");

    // Fire-and-return: this only submits a Dagster run and gets a run_id
    // back immediately (see backend docstring on the endpoint) -- the
    // actual recompute happens out-of-process, so this call never blocks
    // on cdp_master_profiles size.
    api("/segments/admin/recompute-all", {}, "POST")
      .done(function (response) {
        setRefreshButtonBusy(true, "Refreshing...");
        showToast("Segment refresh job submitted (run " + response.run_id + ")...", "info");
        pollRecomputeStatus(response.run_id, 1);
      })
      .fail(function (xhr) {
        setRefreshButtonBusy(false);
        showApiError("submitting segment refresh job", xhr);
      });
  }

  function bindEvents() {
    listDtv.bindRowClick();
    listDtv.bindLoadMore();
    // Matched-profiles rows share the ".profile-row" click delegation
    // already bound once by C360.profileListView.bindEvents() (both tables render
    // the same profile columns/rowVm) -- only "load more" needs re-binding
    // here since #segment-matched-* is fresh DOM on every loadDetail().
    $(document).on("click", "#btn-back-to-segments", function () { C360.router.navigate("/segments"); });
    $(document).on("click", "#btn-segments-refresh", function () { refreshAllSegments(); });
    $(document).on("click", "#btn-segments-create", function () { openSegmentForm(null); });
    $(document).on("click", "#btn-segment-form-save", submitSegmentForm);
    $(document).on("click", "#btn-segment-form-cancel, #btn-segment-form-close", closeSegmentForm);
    $(document).on("click", "#segment-form-modal", function (e) {
      if (e.target === this) closeSegmentForm();
    });
    $(document).on("change", "#segment-form-domain", function () {
      if (!$("#segment-form-modal").hasClass("hidden")) loadSegmentAttributes($(this).val(), null);
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