/* Customer 360 Admin -- Segments (Audience Builder) list + detail view. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var listState = { skip: 0, limit: 20 };
  var matchedState = { skip: 0, limit: 20 };
  var currentSegmentId = null;

  function processedByLabel(v) { return v === "ai_agent" ? "AI Agent" : "Human"; }
  function processedByBadgeClass(v) { return v === "ai_agent" ? "bg-purple-100 text-purple-700" : "bg-slate-100 text-slate-600"; }
  function activeLabel(v) { return v ? "Active" : "Inactive"; }
  function activeBadgeClass(v) { return v ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500"; }
  function domainLabel(domain) { return fmt.DOMAIN_LABELS[domain] || (domain === "all" ? "All domains" : fmt.titleCase(domain)); }

  function segmentRowVm(s) {
    return $.extend({}, s, {
      domainLabel: domainLabel(s.domain),
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

  function segmentDetailVm(s) {
    return $.extend({}, s, {
      domainLabel: domainLabel(s.domain),
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

  function loadList(append) {
    if (!append) { listState.skip = 0; $("#segments-tbody").empty(); }
    $("#segments-list-loading").removeClass("hidden");
    $("#segments-list-empty").addClass("hidden");

    api("/segments/", { skip: listState.skip, limit: listState.limit })
      .done(function (segments) {
        $("#segments-list-loading").addClass("hidden");
        var vms = segments.map(segmentRowVm);
        $("#segments-tbody").append(C360.templates.render("segments-rows", { segments: vms }));
        var total = $("#segments-tbody tr").length;
        $("#segments-count-label").text(total + " segment" + (total === 1 ? "" : "s") + " shown");
        $("#segments-list-empty").toggleClass("hidden", total > 0);
        $("#btn-segments-load-more").toggleClass("hidden", segments.length < listState.limit);
        listState.skip += segments.length;
      })
      .fail(function (xhr) { $("#segments-list-loading").addClass("hidden"); showApiError("loading segments", xhr); });
  }

  function loadMatchedProfiles(segmentId, append) {
    if (!append) { matchedState.skip = 0; $("#segment-matched-tbody").empty(); }
    $("#segment-matched-loading").removeClass("hidden");
    $("#segment-matched-empty").addClass("hidden");

    api("/segments/" + segmentId + "/matched-profiles", { skip: matchedState.skip, limit: matchedState.limit })
      .done(function (profiles) {
        $("#segment-matched-loading").addClass("hidden");
        var vms = profiles.map(C360.listView.rowVm);
        $("#segment-matched-tbody").append(C360.templates.render("profiles-rows", { profiles: vms }));
        var total = $("#segment-matched-tbody tr").length;
        $("#segment-matched-count-label").text(total + " matched profile" + (total === 1 ? "" : "s") + " shown");
        $("#segment-matched-empty").toggleClass("hidden", total > 0);
        $("#btn-segment-matched-load-more").toggleClass("hidden", profiles.length < matchedState.limit);
        matchedState.skip += profiles.length;
      })
      .fail(function (xhr) { $("#segment-matched-loading").addClass("hidden"); showApiError("loading matched profiles", xhr); });
  }

  function loadDetail(segmentId) {
    currentSegmentId = segmentId;
    matchedState.skip = 0;
    $("#segment-detail-content").empty();
    $("#segment-detail-loading").removeClass("hidden");

    api("/segments/" + segmentId)
      .done(function (segment) {
        $("#segment-detail-loading").addClass("hidden");
        $("#segment-detail-content").html(C360.templates.render("segment-details", segmentDetailVm(segment)));
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

  function showToast(message, kind) {
    var classes = {
      success: "bg-green-100 border-green-300 text-green-700",
      error: "bg-red-100 border-red-300 text-red-700",
      info: "bg-blue-100 border-blue-300 text-blue-700"
    };
    var $notification = $("<div></div>")
      .addClass("fixed top-4 right-4 border px-4 py-2 rounded-lg shadow-md z-50")
      .addClass(classes[kind] || classes.info)
      .text(message);
    $("body").append($notification);
    setTimeout(function () { $notification.fadeOut(300, function () { $(this).remove(); }); }, 4000);
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
          showToast("✓ Segment refresh completed", "success");
          loadList(false); // reload to show updated member_count values
          return;
        }
        if (result.status === "failure") {
          setRefreshButtonBusy(false);
          showToast("✗ Segment refresh job failed (raw status: " + result.raw_status + ")", "error");
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
    $(document).on("click", ".segment-row", function () { C360.router.navigate("/segments/" + $(this).data("id")); });
    $(document).on("click", "#btn-segments-load-more", function () { loadList(true); });
    $(document).on("click", "#btn-segment-matched-load-more", function () { loadMatchedProfiles(currentSegmentId, true); });
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