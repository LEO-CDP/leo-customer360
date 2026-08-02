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
    fetch: function (params) { return api("/segments/", params); },
    onRowClick: function (id) { C360.router.navigate("/segments/" + id); },
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
      columns: C360.listView.columns,
      rowVm: C360.listView.rowVm,
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
    // already bound once by C360.listView.bindEvents() (both tables render
    // the same profile columns/rowVm) -- only "load more" needs re-binding
    // here since #segment-matched-* is fresh DOM on every loadDetail().
    $(document).on("click", "#btn-back-to-segments", function () { C360.router.navigate("/segments"); });
    $(document).on("click", "#btn-segments-refresh", function () { refreshAllSegments(); });
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