/* Customer 360 Admin -- Campaign Performance Dashboard view.
 *
 * Thin config layer on top of C360.DataTableView (same pattern as list-view.js):
 *   - COLUMNS / campaignRowVm describe *what* each row looks like
 *   - DataTableView owns loading, empty state, count label, "load more"
 *   - Charts (Spend Trend + Top Campaigns) are rendered directly via Chart.js
 *   - KPI cards are rendered via the /analytics/summary endpoint
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var charts = {};
  var dtv = null;   // created inside mount() after template is injected
  var _topData = null;

  var ICONS = {
    sortDesc: '<path stroke-linecap="round" stroke-linejoin="round" d="M3 4.5h14.25M3 9h9.75M3 13.5h9.75m4.5-4.5v12m0 0l-3.75-3.75M17.25 21L21 17.25" />',
    sortAsc:  '<path stroke-linecap="round" stroke-linejoin="round" d="M3 4.5h14.25M3 9h9.75M3 13.5h5.25m5.25-.75V21m0 0l-3.75-3.75M17.25 21L21 17.25" />'
  };

  var PLATFORM_ICONS = {
    Google: "🔍", Meta: "📘", TikTok: "🎵",
    google_ads: "🔍", meta_ads: "📘", tiktok_ads: "🎵",
    email: "✉️", sms: "💬", notification: "💬", push: "🔔",
    Zalo: "💬", Adjust: "📱", YouTube: "▶️"
  };

  var CHANNEL_ICONS = {
    "Paid Search":       "🔍",
    "Paid Social":       "📲",
    "Push Notification": "🔔",
    "Email":             "✉️",
    email:               "✉️",
    sms:                 "💬",
    notification:        "💬",
    push:                "🔔",
    audience_sync:       "🎯",
    "Video":             "▶️"
  };

  var CAMPAIGN_CHANNELS = [
    { value: "email", label: "Email" },
    { value: "notification", label: "Notification" },
    { value: "push", label: "Web push" },
    { value: "audience_sync", label: "Custom audience sync" }
  ];

  var CAMPAIGN_PLATFORMS = {
    email: [{ value: "email", label: "Email provider" }],
    notification: [
      { value: "sms", label: "SMS provider" },
      { value: "zalo_oa", label: "Zalo OA" },
      { value: "whatsapp", label: "WhatsApp" },
      { value: "mobile_native", label: "Mobile native push" }
    ],
    sms: [
      { value: "sms", label: "SMS provider" },
      { value: "zalo_zns", label: "Zalo ZNS (legacy)" }
    ],
    push: [{ value: "push", label: "Web push provider" }],
    audience_sync: [
      { value: "meta_ads", label: "Meta / Facebook Ads" },
      { value: "google_ads", label: "Google Ads" },
      { value: "tiktok_ads", label: "TikTok Ads" }
    ]
  };

  var CAMPAIGN_LANGUAGES = [
    { value: "en", label: "English" },
    { value: "vi", label: "Vietnamese" },
    { value: "th", label: "Thai" },
    { value: "id", label: "Indonesian" },
    { value: "zh-CN", label: "Chinese (Simplified)" },
    { value: "ja", label: "Japanese" },
    { value: "ko", label: "Korean" }
  ];

  var STATUS_BADGE = {
    Active:    "bg-emerald-100 text-emerald-700",
    Running:   "bg-emerald-100 text-emerald-700",
    Failed:    "bg-rose-100 text-rose-700",
    Paused:    "bg-amber-100 text-amber-700",
    Draft:     "bg-slate-100 text-slate-600",
    Completed: "bg-indigo-100 text-indigo-700"
  };

  // ---- page state (sort/trend only; filter state lives inside DataTableView) ----
  var state = {
    sortBy: "total_spend",
    sortOrder: "desc",
    trendDays: 30,
    topMetric: "conversions"
  };

  var segmentPickerState = {
    items: [],
    byId: {},
    loaded: false,
    tenantId: null
  };
  var campaignWorkflowState = { segment: null, ready: false, workflow: null };
  var campaignWorkflowGeneration = 0;
  var campaignSegmentRequest = 0;
  var campaignDetailState = { campaign: null, workflow: null, history: [], actions: [], busy: false };
  var campaignStateReturnFocus = null;
  var campaignDetailGeneration = 0;
  var campaignReportRequest = 0;

  function campaignStateActions(campaign, workflow, admin) {
    var actions = [];
    var approval = campaignApprovalStatus(campaign);
    var status = String(campaign.status || "").toLowerCase();
    var base = "/campaigns/" + encodeURIComponent(campaign.campaign_id);
    function add(key, label, path, description, payload, reason) {
      actions.push({ key: key, label: label, path: path, description: description, payload: payload || {}, reason: !!reason });
    }
    if (status === "running") return actions;
    if (approval === "Draft") add("submit", "Submit for approval", base + "/submit-for-approval", "Moves campaign approval from Draft to InReview. A tenant admin must review it.");
    if (admin && workflowApprovalStatus(workflow) === "InReview" && workflow.workflow_id && workflow.workflow_version != null) {
      var review = "/workflows/" + encodeURIComponent(workflow.workflow_id) + "/versions/" + encodeURIComponent(workflow.workflow_version) + "/review";
      add("approve-workflow", "Approve workflow", review, "Approves only this pinned workflow version. The initiating user cannot review their own agent proposal.", { decision: "approve" }, true);
      add("reject-workflow", "Reject workflow", review, "Rejects this workflow version. Save a revised version before seeking approval again.", { decision: "reject" }, true);
    }
    if (admin && approval === "InReview") {
      if (isWorkflowApproved(workflow)) add("approve", "Approve campaign", base + "/approve", "Moves campaign approval from InReview to Approved. Delivery does not start until you choose Start campaign.");
      add("reject", "Reject campaign", base + "/reject", "Moves campaign approval from InReview to Rejected. Edit and save the campaign to return it to review.", {}, true);
    }
    if (admin && approval === "Approved" && isWorkflowApproved(workflow) &&
        campaign.segment_id && workflow.workflow_id && workflow.workflow_version != null &&
        status === "completed") {
      add("restart", "Restart campaign", "/admin/campaigns/" + encodeURIComponent(campaign.campaign_id) + "/activate",
        "Starts a full replay for the current segment. Previously successful messages may be sent again. Configure providers and review consent before restarting; there is no restart limit.",
        { restart: true });
    } else if (admin && approval === "Approved" && isWorkflowApproved(workflow) &&
        status !== "running" && campaign.segment_id && workflow.workflow_id && workflow.workflow_version != null) {
      add("start", "Start campaign", "/admin/campaigns/" + encodeURIComponent(campaign.campaign_id) + "/activate",
        "Submits the pinned workflow to Dagster. A failed execution retries within its existing idempotency scope; previously successful actions are not replayed.");
    }
    return actions;
  }

  function updateCampaignStateAction() {
    var action = campaignDetailState.actions.find(function (item) { return item.key === $("#campaign-state-action").val(); });
    $("#campaign-state-action-description span").text(action ? action.description : "No transition is available. Review the current states and your permissions. Rejected campaigns or workflows need an edit and a new review. A completed campaign can be fully replayed by an admin.");
    $("#campaign-state-reason-field").toggleClass("hidden", !action || !action.reason);
    $("#campaign-state-restart-confirm").toggleClass("hidden", !action || !action.payload.restart);
    $("#campaign-state-confirm").prop(
      "disabled",
      !action || campaignDetailState.busy ||
        (action.payload.restart && !$("#campaign-state-restart-ack").prop("checked"))
    ).text(action ? action.label : "No action available");
    setCampaignMessage("#campaign-state-error", "");
  }

  function openCampaignStateModal(actionKey) {
    var modal = document.getElementById("campaign-state-modal");
    if (!modal || !campaignDetailState.campaign || campaignDetailState.busy) return;
    campaignStateReturnFocus = document.activeElement;
    $("#campaign-state-modal-name").text(campaignDetailState.campaign.name || "Campaign");
    $("#campaign-state-modal-approval").text(campaignApprovalStatus(campaignDetailState.campaign));
    $("#campaign-state-modal-workflow").text(
      (campaignDetailState.workflow && campaignDetailState.workflow.workflow_version != null
        ? "v" + campaignDetailState.workflow.workflow_version + " · "
        : "") + (workflowApprovalStatus(campaignDetailState.workflow) || "Not saved")
    );
    $("#campaign-state-modal-execution").text(campaignDetailState.campaign.status || "Draft");
    var $select = $("#campaign-state-action").empty();
    campaignDetailState.actions.forEach(function (action) {
      $("<option>").val(action.key).text(action.label).appendTo($select);
    });
    if (actionKey) $select.val(actionKey);
    $select.prop("disabled", !campaignDetailState.actions.length);
    $("#campaign-state-reason").val("");
    $("#campaign-state-restart-ack").prop("checked", false);
    updateCampaignStateAction();
    if (!modal.open) modal.showModal();
    $select.trigger("focus");
  }

  function closeCampaignStateModal() {
    if (campaignDetailState.busy) return;
    var modal = document.getElementById("campaign-state-modal");
    if (modal && modal.open) modal.close();
    if (campaignStateReturnFocus && campaignStateReturnFocus.isConnected) campaignStateReturnFocus.focus();
  }

  function submitCampaignState(event) {
    event.preventDefault();
    if (campaignDetailState.busy) return;
    var action = campaignDetailState.actions.find(function (item) { return item.key === $("#campaign-state-action").val(); });
    if (!action) return;
    if (action.payload.restart && !$("#campaign-state-restart-ack").prop("checked")) {
      setCampaignMessage("#campaign-state-error", "Confirm that you understand this will replay successful campaign actions.");
      return;
    }
    var reason = String($("#campaign-state-reason").val() || "").trim();
    if (action.reason && reason.length > 2000) {
      setCampaignMessage("#campaign-state-error", "Review reason must be at most 2,000 characters.");
      return;
    }
    var payload = $.extend({}, action.payload);
    if (action.reason && reason) payload.reason = reason;
    var campaignId = campaignDetailState.campaign.campaign_id;
    campaignDetailState.busy = true;
    $("#campaign-state-form").attr("aria-busy", "true").find("button, select, textarea, input").prop("disabled", true);
    $("#campaign-state-confirm").text("Processing...");
    setCampaignMessage("#campaign-state-error", "");
    api(action.path, payload, "POST")
      .done(function (response) {
        campaignDetailState.busy = false;
        closeCampaignStateModal();
        loadCampaignDetails(campaignId, action.label + " succeeded." + (response.run_id ? " Dagster run ID: " + response.run_id : ""));
      })
      .fail(function (xhr) {
        setCampaignMessage("#campaign-state-error", campaignError(xhr) + " Refresh campaign details if its state changed.");
        $("#campaign-state-error").trigger("focus");
      })
      .always(function () {
        campaignDetailState.busy = false;
        $("#campaign-state-form").attr("aria-busy", "false").find("button, select, textarea, input").prop("disabled", false);
        $("#campaign-state-confirm").text(action.label);
      });
  }

  // ---- helpers ----

  function tenantId() { return C360.config.current.tenantId; }

  function fmtVnd(v) {
    if (v == null) return "—";
    var n = Number(v);
    if (n >= 1e9) return (n / 1e9).toFixed(1) + " tỷ";
    if (n >= 1e6) return (n / 1e6).toFixed(0) + " tr";
    return fmt.int(n);
  }

  function roasBadgeClass(v) {
    var r = parseFloat(v) || 0;
    if (r >= 3)   return "bg-emerald-100 text-emerald-700";
    if (r >= 1.5) return "bg-amber-100 text-amber-700";
    return "bg-rose-100 text-rose-700";
  }

  function renderChart(id, config) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    var el = document.getElementById(id);
    if (el) charts[id] = new Chart(el.getContext("2d"), config);
  }

  function destroyCharts() {
    Object.keys(charts).forEach(function (id) { if (charts[id]) charts[id].destroy(); delete charts[id]; });
  }

  function trendDateRange(days) {
    var end = new Date(), start = new Date(Date.now() - (days - 1) * 86400000);
    function iso(d) { return d.toISOString().split("T")[0]; }
    return { start_date: iso(start), end_date: iso(end) };
  }

  // ---- DataTableView column config ----

  var COLUMNS = [
    {
      label: "Campaign", type: "identity",
      nameField: "name", subField: "campaign_code", subStyle: "tag",
      avatarField: "platformIcon", avatarBg: "bg-violet-100", avatarColor: "text-violet-700",
      avatarTextClass: "text-base"
    },
    { label: "Status",   type: "badge",  field: "status",          classField: "statusBadgeClass" },
    { label: "Channel",  type: "identity", nameField: "channel",   subField: "platform",
      avatarField: "channelIcon", avatarBg: "bg-slate-100", avatarColor: "text-slate-600", avatarTextClass: "text-base" },
    { label: "Spend",    field: "spendLabel",       cellClass: "text-right" },
    { label: "Impr.",    field: "impressionsLabel",  cellClass: "text-right" },
    { label: "CTR",      field: "ctrLabel",          cellClass: "text-right" },
    { label: "Conv.",    field: "conversionsLabel",  cellClass: "text-right" },
    { label: "CPA",      field: "cpaLabel",          cellClass: "text-right" },
    { label: "ROAS",     type: "badge",  field: "roasLabel",       classField: "roasBadgeClass" }
  ];

  function campaignRowVm(c) {
    return $.extend({}, c, {
      platformIcon:      PLATFORM_ICONS[c.platform] || "📣",
      channelIcon:       CHANNEL_ICONS[c.channel]   || "📢",
      statusBadgeClass:  STATUS_BADGE[c.status] || "bg-slate-100 text-slate-500",
      spendLabel:        fmtVnd(c.total_spend),
      impressionsLabel:  fmt.int(c.total_impressions),
      ctrLabel:          (parseFloat(c.ctr_percentage) || 0).toFixed(2) + "%",
      conversionsLabel:  fmt.int(c.total_conversions),
      cpaLabel:          fmtVnd(c.cpa),
      roasLabel:         (parseFloat(c.roas) || 0).toFixed(2) + "×",
      roasBadgeClass:    roasBadgeClass(c.roas)
    });
  }

  // ---- KPI cards ----

  function loadKPIs() {
    return api("/campaigns/analytics/summary", { tenant_id: tenantId() })
      .done(function (data) {
        $("#kpi-campaign-total").text(fmt.int(data.total_campaigns));
        $("#kpi-campaign-spend").text(fmtVnd(data.total_spend));
        $("#kpi-campaign-impressions").text(fmt.int(data.total_impressions));
        $("#kpi-campaign-clicks").text(fmt.int(data.total_clicks));
        $("#kpi-campaign-ctr").text((parseFloat(data.overall_ctr) || 0).toFixed(2) + "%");
        $("#kpi-campaign-conversions").text(fmt.int(data.total_conversions));
        $("#kpi-campaign-cvr").text((parseFloat(data.overall_cvr) || 0).toFixed(2) + "%");
        $("#kpi-campaign-revenue").text(fmtVnd(data.total_revenue));
        $("#kpi-campaign-roas").text((parseFloat(data.overall_roas) || 0).toFixed(2) + "×");
      })
      .fail(function (xhr) { showApiError("loading campaign KPIs", xhr); });
  }

  // ---- spend trend chart ----

  function loadSpendTrend() {
    var range = trendDateRange(state.trendDays);
    return api("/campaigns/analytics/spend-trend", $.extend({ tenant_id: tenantId() }, range))
      .done(function (data) {
        renderChart("campaign-spend-trend-chart", {
          type: "line",
          data: {
            labels: data.map(function (d) { return d.report_date; }),
            datasets: [{
              label: "Spend (triệu VND)",
              data: data.map(function (d) { return parseFloat(d.spend) / 1e6; }),
              borderColor: "#7c3aed",
              backgroundColor: "rgba(124,58,237,0.10)",
              fill: true, tension: 0.3, pointRadius: 2, pointHoverRadius: 5
            }]
          },
          options: {
            maintainAspectRatio: false,
            scales: {
              x: { ticks: { maxTicksLimit: 8, font: { size: 10 } } },
              y: { beginAtZero: true, ticks: { font: { size: 10 }, callback: function (v) { return v + "M"; } } }
            },
            plugins: {
              legend: { display: false },
              tooltip: { callbacks: { label: function (ctx) { return fmt.int(ctx.parsed.y * 1e6) + " VND"; } } }
            }
          }
        });
      })
      .fail(function (xhr) { showApiError("loading spend trend", xhr); });
  }

  // ---- top campaigns chart ----

  function loadTopCampaigns() {
    return api("/campaigns/analytics/top", { tenant_id: tenantId(), limit: 5 })
      .done(function (data) { _topData = data; renderTopChart(data); })
      .fail(function (xhr) { showApiError("loading top campaigns", xhr); });
  }

  function renderTopChart(data) {
    var isRoas = state.topMetric === "roas";
    renderChart("campaign-top-chart", {
      type: "bar",
      data: {
        labels: data.map(function (d) { var n = d.name || ""; return n.length > 22 ? n.substring(0, 22) + "…" : n; }),
        datasets: [{ label: isRoas ? "ROAS" : "Conversions",
          data: data.map(function (d) { return isRoas ? (parseFloat(d.roas) || 0) : d.conversions; }),
          backgroundColor: isRoas ? "#059669" : "#7c3aed", borderRadius: 4 }]
      },
      options: {
        indexAxis: "y", maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) {
            return isRoas ? ctx.parsed.x.toFixed(2) + "× ROAS" : fmt.int(ctx.parsed.x) + " conversions";
          }}}
        },
        scales: { x: { beginAtZero: true, ticks: { font: { size: 10 } } }, y: { ticks: { font: { size: 10 } } } }
      }
    });
  }

  // ---- DataTableView factory (called after template is mounted) ----

  function createDtv() {
    return C360.DataTableView.create({
      columns: COLUMNS,
      rowVm: campaignRowVm,
      rowId: function (vm) { return vm.campaign_id; },
      rowSelectorClass: "campaign-row",
      limit: 10,
      rowClickable: true,
      resourceLabel: "campaign",
      actionLabel: "Details",
      onRowClick: function (id) { C360.router.navigate("/campaigns/" + encodeURIComponent(id)); },
      onEdit: function (id) { C360.router.navigate("/campaigns/" + encodeURIComponent(id) + "/edit"); },
      extraParams: function () {
        return { tenant_id: tenantId(), sort_by: state.sortBy, sort_order: state.sortOrder };
      },
      // Translate skip/limit → page/page_size and unwrap the items array.
      fetch: function (params) {
        var page = params.limit > 0 ? Math.floor(params.skip / params.limit) + 1 : 1;
        var p = $.extend({}, params, { page: page, page_size: params.limit });
        delete p.skip;
        delete p.limit;
        return api("/campaigns/analytics", p).then(function (data) {
          return (data && data.items) ? data.items : [];
        });
      },
      onError: function (xhr) { showApiError("loading campaign table", xhr); },
      el: {
        thead:       "#campaign-table-head",
        tbody:       "#campaign-table-body",
        loading:     "#campaign-table-loading",
        empty:       "#campaign-table-empty",
        countLabel:  "#campaign-table-count",
        loadMoreBtn: "#btn-campaign-load-more"
      }
    });
  }

  function campaignError(xhr) {
    var detail = xhr && xhr.responseJSON ? xhr.responseJSON.detail : null;
    if (Array.isArray(detail)) return detail.map(function (item) { return item.msg || JSON.stringify(item); }).join("; ");
    return typeof detail === "string" ? detail : "Request failed. Please try again.";
  }

  function setCampaignMessage(selector, message) {
    $(selector).toggleClass("hidden", !message).text(message || "");
  }

  function canReviewCampaign() {
    var user = C360.config.currentUser();
    if (user.isRoot) return true;
    var roles = user.roles || [];
    var reviewerRoles = ["platform_admin", "super_admin", "system_admin", "tenant_admin", "admin", "root"];
    return roles.some(function (role) { return reviewerRoles.indexOf(String(role).toLowerCase()) !== -1; });
  }

  function campaignApprovalStatus(campaign) {
    return String(campaign && campaign.approval_status || "Draft").trim();
  }

  function workflowApprovalStatus(workflowResponse) {
    return String(workflowResponse && workflowResponse.approval_status || "").trim();
  }

  function isWorkflowApproved(workflowResponse) {
    return workflowApprovalStatus(workflowResponse).toLowerCase() === "approved";
  }

  function setCampaignActionHint(message) {
    $("#campaign-details-action-hint").toggleClass("hidden", !message).text(message || "");
  }

  function campaignStatusPresentation(status) {
    var key = String(status || "Draft").trim().toLowerCase();
    var presentations = {
      draft: { label: "Draft", icon: "bi-file-earmark-text", classes: "bg-slate-100 text-slate-700 ring-1 ring-inset ring-slate-200" },
      active: { label: "Active", icon: "bi-play-circle-fill", classes: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200" },
      running: { label: "Running", icon: "bi-play-circle-fill", classes: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200" },
      failed: { label: "Failed", icon: "bi-exclamation-circle-fill", classes: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200" },
      paused: { label: "Paused", icon: "bi-pause-circle-fill", classes: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200" },
      completed: { label: "Completed", icon: "bi-check-circle-fill", classes: "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200" },
      cancelled: { label: "Cancelled", icon: "bi-x-circle-fill", classes: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200" }
    };
    return presentations[key] || { label: status || "Unknown", icon: "bi-question-circle", classes: "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200" };
  }

  function fieldValue(selector) {
    return $(selector).val() || "";
  }

  function segmentIsReady(segment) {
    return !!segment && segment.is_active === true && Number(segment.status_code) === 1;
  }

  function segmentMeta(segment) {
    var parts = [];
    if (segment.segment_tag) parts.push("#" + segment.segment_tag);
    if (segment.domain && segment.domain !== "all") parts.push(segment.domain);
    parts.push(fmt.int(segment.member_count || 0) + " profiles");
    return parts.join("  ·  ");
  }

  function workflowProfileValue(value) {
    if (value == null || value === "") return null;
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function workflowProfile(profile, index) {
    var attributes = profile.attributes && typeof profile.attributes === "object"
      ? profile.attributes
      : {};
    var pushTokens = profile.push_tokens && typeof profile.push_tokens === "object"
      ? profile.push_tokens
      : {};
    var preferences = profile.communication_preferences || {};
    var lastActivity = profile.last_activity_at ? Date.parse(profile.last_activity_at) : NaN;
    var recency = Number.isFinite(lastActivity)
      ? Math.max(0, Math.floor((Date.now() - lastActivity) / 86400000))
      : 0;
    var displayName = fmt.realName(profile) || profile.persona_name ||
      ("Profile " + fmt.shortId(profile.master_profile_id));

    return {
      id: index + 1,
      name: displayName,
      engagement_score: workflowProfileValue(profile.engagement_score),
      churn_probability: workflowProfileValue(profile.churn_probability),
      lead_conversion_probability: workflowProfileValue(profile.lead_conversion_probability),
      historical_clv: workflowProfileValue(profile.historical_clv),
      predictive_clv: workflowProfileValue(profile.predictive_clv),
      latest_nps_score: workflowProfileValue(profile.latest_nps_score),
      average_csat: workflowProfileValue(profile.average_csat),
      overall_sentiment_score: workflowProfileValue(profile.overall_sentiment_score),
      profile_completeness_score: workflowProfileValue(profile.profile_completeness_score),
      identity_confidence_score: workflowProfileValue(profile.identity_confidence_score),
      cart: workflowProfileValue(attributes.cart_value),
      recency: recency,
      push: Object.keys(pushTokens).length && preferences.push_opt_in === true ? 1 : 0,
      sms: profile.phone_number && preferences.sms_opt_in === true ? 1 : 0,
      email: profile.email && preferences.email_opt_in === true ? 1 : 0,
      tags: Array.isArray(profile.segmentation_tags) ? profile.segmentation_tags.slice() : []
    };
  }

  function loadCampaignSegmentProfiles(segmentId) {
    return api("/segments/" + encodeURIComponent(segmentId) + "/matched-profiles", {
      skip: 0,
      limit: 32
    }).then(function (profiles) {
      if (!Array.isArray(profiles)) throw new Error("The segment profile response was invalid.");
      return profiles.map(workflowProfile);
    }, function (xhr) {
      throw new Error(campaignError(xhr));
    });
  }

  function workflowSegmentConfig(segment) {
    return {
      segment_id: String(segment.segment_id),
      segment_name: String(segment.segment_name || "").trim(),
      description: String(segment.description || "")
    };
  }

  function showWorkflowPlaceholder(message, isError) {
    var $message = $("<div>")
      .attr("role", isError ? "alert" : "status")
      .addClass("flex min-h-[180px] items-center justify-center p-6 text-center");
    var $copy = $("<div>").addClass("max-w-lg");
    $("<span>").addClass("mb-3 inline-flex h-11 w-11 items-center justify-center rounded-full bg-indigo-100 text-indigo-700")
      .append($("<i>").addClass("bi bi-diagram-3").attr("aria-hidden", "true"))
      .appendTo($copy);
    $("<p>").addClass("text-sm font-semibold text-slate-800").text(message).appendTo($copy);
    $("<p>").addClass("mt-1 text-xs text-slate-500")
      .text("Choose an active, computed audience segment above to load its profiles.")
      .appendTo($copy);
    $("#campaign-workflow-studio").empty().append($message.append($copy));
  }

  function syncCampaignWorkflow(segment) {
    var selectedSegment = segmentIsReady(segment) ? segment : null;
    var $studio = $("#campaign-workflow-studio");
    var studioApi = $studio.data("workflowStudio");
    campaignWorkflowState.segment = selectedSegment;

    if (!selectedSegment) {
      $("#campaign-workflow-audience-status").text(
        segment ? "Audience needs attention" : "Select an audience"
      );
      if (studioApi) {
        if (campaignWorkflowState.ready && studioApi.getSegmentId() !== null) {
          studioApi.setSegment(null);
        }
      } else {
        showWorkflowPlaceholder(
          segment ? "This audience is not ready for a workflow." : "Start by selecting an audience segment.",
          !!segment
        );
      }
      return;
    }

    $("#campaign-workflow-audience-status").text(
      (selectedSegment.segment_name || "Selected audience") +
        " · " + fmt.int(selectedSegment.member_count || 0) + " profiles"
    );
    if (studioApi) {
      if (campaignWorkflowState.ready) {
        var currentSegment = studioApi.getSegments()[0];
        if (
          !currentSegment ||
          String(currentSegment.segment_id) !== String(selectedSegment.segment_id) ||
          currentSegment.segment_name !== selectedSegment.segment_name
        ) {
          studioApi.setSegment(workflowSegmentConfig(selectedSegment));
        }
      }
      return;
    }

    var generation = ++campaignWorkflowGeneration;
    campaignWorkflowState.ready = false;
    $studio.workflowStudio({
      title: "Customer Engagement Workflow",
      segmentId: String(selectedSegment.segment_id),
      segmentName: selectedSegment.segment_name,
      segments: [workflowSegmentConfig(selectedSegment)],
      workflow: campaignWorkflowState.workflow,
      profileCount: 32,
      loadProfiles: loadCampaignSegmentProfiles,
      onReady: function (initialSegmentId, apiInstance) {
        if (generation !== campaignWorkflowGeneration) return;
        campaignWorkflowState.ready = true;
        var latestSegment = campaignWorkflowState.segment
          ? workflowSegmentConfig(campaignWorkflowState.segment)
          : null;
        var mountedSegment = apiInstance.getSegments()[0];
        if (
          !latestSegment ||
          !mountedSegment ||
          String(initialSegmentId) !== String(latestSegment.segment_id) ||
          mountedSegment.segment_name !== latestSegment.segment_name
        ) {
          apiInstance.setSegment(latestSegment);
        }
      }
    });
  }

  function renderSegmentSelection(segment, options) {
    options = options || {};
    var selected = !!segment;
    $("#campaign-field-segment").val(selected ? segment.segment_id : "");
    $("#campaign-segment-empty").toggleClass("hidden", selected);
    $("#campaign-segment-selected").toggleClass("hidden", !selected);
    $("#campaign-segment-selected-name").text(selected ? (segment.segment_name || "Unnamed segment") : "");
    $("#campaign-segment-selected-meta").text(selected ? segmentMeta(segment) : "");
    $("#campaign-segment-warning").toggleClass("hidden", !options.warning).text(options.warning || "");
    $("#btn-campaign-select-segment, #btn-campaign-change-segment").prop("disabled", !!options.locked).toggleClass("opacity-50 cursor-not-allowed", !!options.locked);
    $("#btn-campaign-clear-segment").toggleClass("hidden", !selected || options.allowClear === false || !!options.locked);
    syncCampaignWorkflow(segment);
  }

  function segmentMatches(segment, query) {
    if (!segmentIsReady(segment)) return false;
    var needle = String(query || "").toLowerCase().trim();
    if (!needle) return true;
    return [segment.segment_name, segment.segment_tag, segment.description].some(function (value) {
      return String(value || "").toLowerCase().indexOf(needle) !== -1;
    });
  }

  function renderSegmentResults(query) {
    var $results = $("#campaign-segment-results").empty();
    var matches = segmentPickerState.items.filter(function (segment) { return segmentMatches(segment, query); });
    $("#campaign-segment-modal-empty").toggleClass("hidden", matches.length > 0);
    if (!matches.length) return;
    matches.forEach(function (segment) {
      var $button = $("<button>").attr({ type: "button", role: "option", "aria-selected": "false" }).addClass("campaign-segment-option flex w-full items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white p-3 text-left transition hover:border-indigo-300 hover:bg-indigo-50/50");
      var $copy = $("<span>").addClass("min-w-0");
      $("<span>").addClass("block truncate text-sm font-semibold text-slate-800").text(segment.segment_name || "Unnamed segment").appendTo($copy);
      $("<span>").addClass("mt-1 block truncate text-xs font-normal text-slate-500").text(segmentMeta(segment)).appendTo($copy);
      $("<span>").addClass("shrink-0 rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700").text("Ready").appendTo($button);
      $button.attr("data-segment-id", segment.segment_id).append($copy).appendTo($results);
    });
  }

  function loadSegmentsForPicker() {
    var activeTenantId = tenantId();
    if (segmentPickerState.loaded && segmentPickerState.tenantId === activeTenantId) {
      renderSegmentResults($("#campaign-segment-search").val());
      return;
    }
    segmentPickerState.loaded = false;
    segmentPickerState.tenantId = activeTenantId;
    $("#campaign-segment-modal-loading").removeClass("hidden");
    $("#campaign-segment-modal-error, #campaign-segment-modal-empty").addClass("hidden");
    api("/segments/", { skip: 0, limit: 100 })
      .done(function (segments) {
        segmentPickerState.items = (segments || []).filter(function (segment) { return segmentIsReady(segment); });
        segmentPickerState.byId = {};
        segmentPickerState.items.forEach(function (segment) { segmentPickerState.byId[segment.segment_id] = segment; });
        segmentPickerState.loaded = true;
        renderSegmentResults($("#campaign-segment-search").val());
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        $("#campaign-segment-modal-error").removeClass("hidden").text(typeof detail === "string" ? detail : "Could not load audience segments.");
      })
      .always(function () { $("#campaign-segment-modal-loading").addClass("hidden"); });
  }

  function openSegmentPicker() {
    $("#campaign-segment-modal").removeClass("hidden");
    $("#campaign-segment-search").val("").trigger("input").trigger("focus");
    loadSegmentsForPicker();
  }

  function closeSegmentPicker() {
    $("#campaign-segment-modal").addClass("hidden");
  }

  function loadSelectedSegment(segmentId, locked, allowClear) {
    var request = ++campaignSegmentRequest;
    if (!segmentId) {
      renderSegmentSelection(null, { locked: locked, allowClear: allowClear });
      return;
    }
    api("/segments/" + encodeURIComponent(segmentId))
      .done(function (segment) {
        if (request !== campaignSegmentRequest) return;
        renderSegmentSelection(segment, {
          locked: locked,
          allowClear: allowClear,
          warning: segmentIsReady(segment) ? "" : "This segment is no longer active or has no computed membership snapshot. Choose another segment before saving."
        });
      })
      .fail(function () {
        if (request !== campaignSegmentRequest) return;
        renderSegmentSelection({ segment_id: segmentId, segment_name: "Unavailable segment", segment_tag: "" }, {
          locked: locked,
          allowClear: allowClear,
          warning: "The saved segment could not be loaded. Choose another segment before saving."
        });
      });
  }

  function campaignFormPayload() {
    var payload = {
      name: fieldValue("#campaign-field-name").trim(),
      campaign_code: fieldValue("#campaign-field-code").trim() || null,
      status: fieldValue("#campaign-field-status") || "Draft",
      channel: fieldValue("#campaign-field-channel").trim() || null,
      platform: fieldValue("#campaign-field-platform").trim() || null,
      description: fieldValue("#campaign-field-description").trim() || null,
      keywords: fieldValue("#campaign-field-keywords").split(",").map(function (item) { return item.trim(); }).filter(Boolean),
      lang: fieldValue("#campaign-field-lang").trim() || "en",
      start_date: fieldValue("#campaign-field-start-date") || null,
      end_date: fieldValue("#campaign-field-end-date") || null,
      budget_amount: fieldValue("#campaign-field-budget") === "" ? null : Number(fieldValue("#campaign-field-budget")),
      currency: fieldValue("#campaign-field-currency").trim().toUpperCase() || "VND",
      segment_id: fieldValue("#campaign-field-segment").trim() || null,
      objective: fieldValue("#campaign-field-objective").trim() || null,
      journey_stage: fieldValue("#campaign-field-journey-stage").trim() || null,
      strategy_summary: fieldValue("#campaign-field-strategy").trim() || null,
      planning_constraints: fieldValue("#campaign-field-constraints").trim() || null
    };
    payload.tenant_id = tenantId();
    return payload;
  }

  var OBJECTIVE_JOURNEY_STAGE_MAP = {
    awareness: "awareness",
    leads: "acquisition",
    "app install": "activation",
    "visit store": "engagement",
    conversions: "conversion",
    conversion: "conversion",
    retention: "retention",
    loyalty: "loyalty"
  };

  function inferJourneyStageFromObjective(objective) {
    var key = String(objective || "").trim().toLowerCase();
    return OBJECTIVE_JOURNEY_STAGE_MAP[key] || "";
  }

  var pendingCampaignDraftJourneyStage = "";

  function populateCampaignObjectiveOptions(selectedObjective) {
    var $objective = $("#campaign-field-objective");
    var options = JSON.parse($objective.attr("data-objectives"));
    if (!Array.isArray(options) || options.some(function (option) {
      return typeof option !== "string" || !option.trim();
    })) {
      throw new TypeError("Campaign objective options must be a JSON array of non-empty strings.");
    }

    $objective.find("option:not([value=''])").remove();
    options.forEach(function (option) {
      $("<option>").val(option).text(option).appendTo($objective);
    });

    var selected = selectedObjective == null ? "" : String(selectedObjective);
    if (selected && options.indexOf(selected) === -1) {
      $("<option>").val(selected).text(selected).appendTo($objective);
    }
    $objective.val(selected);
  }

  function populateSelectOptions(selector, options, selectedValue, emptyLabel, allowLegacyValue) {
    var $select = $(selector);
    if (!$select.length || !Array.isArray(options)) return;
    $select.empty().append($("<option>").val("").text(emptyLabel));
    options.forEach(function (option) {
      $("<option>").val(option.value).text(option.label).appendTo($select);
    });
    var selected = selectedValue == null ? "" : String(selectedValue);
    if (allowLegacyValue && selected && !options.some(function (option) { return option.value === selected; })) {
      $("<option>").val(selected).text(selected + " (existing value)").appendTo($select);
    }
    $select.val(selected);
  }

  function populateCampaignMetadataOptions(selectedChannel, selectedPlatform, selectedLanguage) {
    populateSelectOptions(
      "#campaign-field-channel",
      CAMPAIGN_CHANNELS,
      selectedChannel,
      "Select a workflow channel",
      true
    );
    var channel = selectedChannel == null ? "" : String(selectedChannel);
    populateSelectOptions(
      "#campaign-field-platform",
      CAMPAIGN_PLATFORMS[channel] || [],
      selectedPlatform,
      channel ? "Select a platform" : "Select a channel first",
      true
    );
    populateSelectOptions(
      "#campaign-field-lang",
      CAMPAIGN_LANGUAGES,
      selectedLanguage || "en",
      "Select a language",
      true
    );
    $("#campaign-platform-help").text(
      channel === "audience_sync"
        ? "The workflow will export matched, consented profiles to this custom-audience platform."
        : "Choose the provider used by the selected workflow channel."
    );
  }

  function fillCampaignEditor(campaign) {
    $("#campaign-editor-id").val(campaign.campaign_id || "");
    $("#campaign-editor-updated-at").val(campaign.updated_at || "");
    $("#campaign-editor-version").text(campaign.updated_at ? "Version " + campaign.updated_at : "New campaign");
    $("#campaign-editor-title").text(campaign.campaign_id ? "Edit campaign" : "Create campaign");
    $("#campaign-editor-eyebrow").text(campaign.campaign_id ? "Campaign editor" : "Campaign workspace");
    $("#campaign-editor-approval").toggleClass("hidden", !campaign.approval_status).text(campaign.approval_status || "");
    $("#campaign-field-code").val(campaign.campaign_code || "");
    $("#campaign-field-name").val(campaign.name || "");
    $("#campaign-field-status").val(campaign.status || "Draft");
    populateCampaignMetadataOptions(campaign.channel, campaign.platform, campaign.lang);
    $("#campaign-field-description").val(campaign.description || "");
    $("#campaign-field-keywords").val((campaign.keywords || []).join(", "));
    $("#campaign-field-start-date").val(campaign.start_date || "");
    $("#campaign-field-end-date").val(campaign.end_date || "");
    $("#campaign-field-budget").val(campaign.budget_amount == null ? "" : campaign.budget_amount);
    var currency = String(campaign.currency || "VND").toUpperCase();
    var $currency = $("#campaign-field-currency");
    if (currency && !$currency.find("option").filter(function () { return $(this).val() === currency; }).length) {
      $("<option>").val(currency).text(currency + " (existing value)").appendTo($currency);
    }
    $currency.val(currency);
    $("#campaign-field-segment").val(campaign.segment_id || "");
    populateCampaignObjectiveOptions(campaign.objective);
    var defaultStage = campaign.journey_stage || pendingCampaignDraftJourneyStage || inferJourneyStageFromObjective(campaign.objective);
    pendingCampaignDraftJourneyStage = "";
    $("#campaign-field-journey-stage").val(defaultStage || "");
    $("#campaign-field-strategy").val(campaign.strategy_summary || "");
    $("#campaign-field-constraints").val(campaign.planning_constraints || "");
    loadSelectedSegment(campaign.segment_id, false, !campaign.campaign_id);
  }

  function loadCampaignEditor(campaignId) {
    campaignWorkflowGeneration += 1;
    campaignWorkflowState = { segment: null, ready: false, workflow: null };
    campaignSegmentRequest += 1;
    setCampaignMessage("#campaign-editor-error", "");
    setCampaignMessage("#campaign-editor-success", "");
    $("#campaign-editor-loading").toggleClass("hidden", !campaignId);
    $("#campaign-editor-form").toggleClass("hidden", !!campaignId);
    if (!campaignId) {
      fillCampaignEditor({});
      return;
    }
    api("/campaigns/" + encodeURIComponent(campaignId))
      .done(function (campaign) {
        api("/campaigns/" + encodeURIComponent(campaignId) + "/workflow")
          .done(function (workflowResponse) {
            campaignWorkflowState.workflow = workflowResponse.workflow;
            $("#campaign-editor-loading").addClass("hidden");
            $("#campaign-editor-form").removeClass("hidden");
            fillCampaignEditor(campaign);
          })
          .fail(function (xhr) {
            $("#campaign-editor-loading").addClass("hidden");
            setCampaignMessage("#campaign-editor-error", campaignError(xhr));
          });
      })
      .fail(function (xhr) {
        $("#campaign-editor-loading").addClass("hidden");
        setCampaignMessage("#campaign-editor-error", campaignError(xhr));
      });

  }

  function saveCampaignEditor(event) {
    event.preventDefault();
    setCampaignMessage("#campaign-editor-error", "");
    setCampaignMessage("#campaign-editor-success", "");
    var campaignId = fieldValue("#campaign-editor-id");
    var payload = campaignFormPayload();
    if (!payload.name) {
      setCampaignMessage("#campaign-editor-error", "Campaign name is required.");
      return;
    }
    if (!payload.segment_id) {
      setCampaignMessage("#campaign-editor-error", "Choose an active audience segment before saving the campaign.");
      return;
    }
    if (payload.start_date && payload.end_date && payload.end_date < payload.start_date) {
      setCampaignMessage("#campaign-editor-error", "End date cannot be before start date.");
      return;
    }
    var $studio = $("#campaign-workflow-studio");
    var studioApi = $studio.data("workflowStudio");
    if (!studioApi || !campaignWorkflowState.ready) {
      setCampaignMessage("#campaign-editor-error", "Wait for the customer journey workflow to finish loading before saving.");
      return;
    }
    var workflow;
    try {
      workflow = studioApi.getWorkflow();
      var problems = studioApi.validate().filter(function (issue) {
        return issue.level === "error";
      });
      if (problems.length) {
        setCampaignMessage("#campaign-editor-error", problems.map(function (issue) { return issue.msg; }).join("; "));
        return;
      }
    } catch (error) {
      setCampaignMessage("#campaign-editor-error", "The customer journey workflow is not ready: " + error.message);
      return;
    }
    if (!workflow || !workflow.root) {
      setCampaignMessage("#campaign-editor-error", "Add an Event trigger to the customer journey before saving.");
      return;
    }
    var request = campaignId
      ? api("/campaigns/" + encodeURIComponent(campaignId) + "/with-workflow", {
          campaign: payload,
          workflow: workflow
        }, "PATCH")
      : api("/campaigns/with-workflow", {
          campaign: payload,
          workflow: workflow
        }, "POST");
    $("#btn-campaign-editor-save").prop("disabled", true).text("Saving...");
    request.done(function (response) {
      var id = response.campaign.campaign_id || campaignId;
      C360.router.navigate("/campaigns/" + encodeURIComponent(id));
    }).fail(function (xhr) {
      setCampaignMessage("#campaign-editor-error", campaignError(xhr));
    }).always(function () {
      $("#btn-campaign-editor-save").prop("disabled", false).text("Save campaign");
    });
  }

  function renderDetailMetadata(campaign) {
    var journeyStageLabels = {
      awareness: "Awareness",
      acquisition: "Acquisition",
      activation: "Activation",
      engagement: "Engagement",
      conversion: "Conversion",
      retention: "Retention",
      loyalty: "Loyalty"
    };
    var fields = [
      { label: "Schedule", value: [campaign.start_date, campaign.end_date].filter(Boolean).join(" - ") || "Not scheduled", icon: "bi-calendar3" },
      { label: "Objective", value: campaign.objective, icon: "bi-bullseye" },
      { label: "Journey stage", value: journeyStageLabels[campaign.journey_stage] || "Not targeted", icon: "bi-signpost-2" },
      { label: "Description", value: campaign.description, icon: "bi-card-text" },
      { label: "Campaign code", value: campaign.campaign_code, icon: "bi-hash", technical: true },
      { label: "Owner", value: campaign.user_id || "System / unassigned", icon: "bi-person" },
      { label: "Channel", value: campaign.channel, icon: "bi-broadcast" },
      { label: "Platform", value: campaign.platform, icon: "bi-grid-1x2" },
      { label: "Language", value: campaign.lang, icon: "bi-translate" },
      { label: "Budget", value: campaign.budget_amount == null ? "No budget set" : String(campaign.budget_amount) + " " + (campaign.currency || ""), icon: "bi-wallet2" },
      { label: "Keywords", value: (campaign.keywords || []).join(", "), icon: "bi-tags" },
      { label: "Created", value: fmt.dateTime(campaign.created_at), icon: "bi-clock-history" },
      { label: "Updated", value: fmt.dateTime(campaign.updated_at), icon: "bi-arrow-repeat" }
    ];
    var $metadata = $("#campaign-details-metadata").empty();
    fields.forEach(function (field) {
      var $item = $("<div>").addClass("min-w-0 rounded-xl border border-slate-200 bg-slate-50/50 p-3 transition hover:border-slate-300 hover:bg-white");
      var $heading = $("<div>").addClass("flex items-center gap-2");
      $("<i>").addClass("bi " + field.icon + " text-slate-400").attr("aria-hidden", "true").appendTo($heading);
      $("<dt>").addClass("text-[11px] font-semibold uppercase tracking-wider text-slate-500").text(field.label).appendTo($heading);
      $heading.appendTo($item);
      $("<dd>").addClass("mt-1 line-clamp-2 break-words text-sm font-semibold text-slate-800 " + (field.technical ? "font-mono text-xs" : "")).text(field.value || "-").attr("title", field.value || "").appendTo($item);
      $metadata.append($item);
    });
    $("#campaign-details-strategy").text(campaign.strategy_summary || "No strategy summary is available.");
    $("#campaign-details-ai-plan").text(JSON.stringify(campaign.ai_plan || {}, null, 2));
    $("#campaign-details-agent").text(JSON.stringify((campaign.metadata_ || {}).agent_provenance || {}, null, 2));
    $("#campaign-details-content-items").empty();
    (campaign.content_items || []).forEach(function (item) {
      var $row = $("<tr>");
      [item.position, item.title || item.content_item_id, item.item_type, item.role].forEach(function (value) { $("<td>").addClass("py-3 pr-4 text-slate-700").text(value || "-").appendTo($row); });
      $("#campaign-details-content-items").append($row);
    });
  }

  function renderDetailAudience(segmentId, segment, loading) {
    $("#campaign-details-audience-id").text(segmentId || "No segment linked");
    $("#campaign-details-audience-status").removeClass("hidden");
    if (!segmentId) {
      $("#campaign-details-audience-name").text("No target audience");
      $("#campaign-details-audience-meta").text("This campaign is not linked to a saved segment.");
      $("#campaign-details-audience-status").attr("class", "shrink-0 rounded-md bg-slate-100 px-2.5 py-1.5 text-xs font-semibold text-slate-600").text("Unassigned");
      $("#btn-campaign-copy-segment-id").addClass("hidden");
      return;
    }
    $("#btn-campaign-copy-segment-id").removeClass("hidden").data("segment-id", segmentId);
    if (loading) {
      $("#campaign-details-audience-name").text("Loading audience...");
      $("#campaign-details-audience-meta").text("Fetching segment details");
      $("#campaign-details-audience-status").attr("class", "shrink-0 rounded-md bg-slate-100 px-2.5 py-1.5 text-xs font-semibold text-slate-600").text("Loading");
      return;
    }
    if (!segment) {
      $("#campaign-details-audience-name").text("Segment unavailable");
      $("#campaign-details-audience-meta").text("The saved segment could not be loaded for this tenant.");
      $("#campaign-details-audience-status").attr("class", "shrink-0 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs font-semibold text-amber-700").text("Unavailable");
      return;
    }
    var meta = [
      segment.segment_tag ? "#" + segment.segment_tag : null,
      segment.domain && segment.domain !== "all" ? segment.domain : null,
      fmt.int(segment.member_count || 0) + " profiles"
    ].filter(Boolean).join("  ·  ");
    $("#campaign-details-audience-name").text(segment.segment_name || "Unnamed segment");
    $("#campaign-details-audience-meta").text(meta);
    var ready = segmentIsReady(segment);
    $("#campaign-details-audience-status").attr("class", ready ? "shrink-0 rounded-md bg-emerald-50 px-2.5 py-1.5 text-xs font-semibold text-emerald-700" : "shrink-0 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs font-semibold text-amber-700").text(ready ? "Ready" : "Needs attention");
  }

  function loadDetailAudience(segmentId) {
    renderDetailAudience(segmentId, null, true);
    if (!segmentId) return;
    api("/segments/" + encodeURIComponent(segmentId))
      .done(function (segment) { renderDetailAudience(segmentId, segment); })
      .fail(function () { renderDetailAudience(segmentId, null); });
  }

  function renderDetailHistory(history) {
    campaignDetailState.history = history || [];
    var $history = $("#campaign-details-history").empty();
    var filter = $("#campaign-history-filter").val() || "all";
    var entries = campaignDetailState.history.slice().sort(function (a, b) {
      return (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0);
    }).filter(function (entry) {
      return filter === "all" || (filter === "review" ? entry.type === "review" : campaignHistoryTransitions(entry).length > 0);
    });
    entries.forEach(function (entry) {
      var $item = $("<li>").addClass("rounded-xl border border-slate-200 border-l-4 border-l-indigo-400 bg-slate-50/50 p-4 text-sm text-slate-700");
      var label = entry.type === "review" ? "Campaign " + String(entry.decision || "review").toLowerCase() : String(entry.action || "Campaign updated").replace(/_/g, " ");
      $("<h3>").addClass("font-semibold text-slate-900").text(label).appendTo($item);
      campaignHistoryTransitions(entry).forEach(function (transition) {
        $("<p>").addClass("mt-2 text-sm font-medium text-indigo-800").text(transition).appendTo($item);
      });
      if (entry.reason) $("<p>").addClass("mt-2 whitespace-pre-wrap text-sm").text(entry.reason).appendTo($item);
      var actor = entry.reviewer_id || entry.user_id;
      $("<p>").addClass("mt-2 break-all text-xs text-slate-500").text(actor ? "Actor: " + actor : "Actor not recorded").appendTo($item);
      var at = new Date(entry.created_at);
      $("<time>").attr("datetime", entry.created_at || "").addClass("mt-1 block text-xs text-slate-500").text(entry.created_at && !isNaN(at.getTime()) ? at.toLocaleString() : "Time not recorded").appendTo($item);
      if (entry.before_data || entry.after_data) {
        $("<details>").addClass("mt-3 text-xs").append(
          $("<summary>").addClass("cursor-pointer font-semibold text-slate-600").text("Audit details"),
          $("<pre>").addClass("mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-3").text(JSON.stringify({ before: entry.before_data, after: entry.after_data }, null, 2))
        ).appendTo($item);
      }
      $item.appendTo($history);
    });
    $("#campaign-history-count").text(entries.length + " of " + campaignDetailState.history.length + " recorded events");
    if (!entries.length) $("<li>").addClass("rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500").text(filter === "all" ? "No campaign history has been recorded yet." : "No recorded events match this filter.").appendTo($history);
  }

  function campaignHistoryTransitions(entry) {
    if (entry.type === "review") {
      var decision = String(entry.decision || "").toLowerCase();
      if (decision === "approve") return ["Campaign approval: InReview \u2192 Approved"];
      if (decision === "reject") return ["Campaign approval: InReview \u2192 Rejected"];
      return [];
    }
    var before = entry.before_data || {};
    var after = entry.after_data || {};
    return [["approval_status", "Campaign approval"], ["status", "Execution"], ["workflow_version", "Workflow version"]].filter(function (field) {
      return Object.prototype.hasOwnProperty.call(after, field[0]) && before[field[0]] !== after[field[0]];
    }).map(function (field) {
      return field[1] + ": " + (before[field[0]] == null ? "Not recorded" : before[field[0]]) + " \u2192 " + after[field[0]];
    });
  }

  function refreshCampaignHistory() {
    if (!campaignDetailState.campaign) return;
    var id = campaignDetailState.campaign.campaign_id;
    $("#btn-campaign-history-refresh").prop("disabled", true).text("Refreshing...");
    setCampaignMessage("#campaign-history-error", "");
    api("/campaigns/" + encodeURIComponent(id) + "/history")
      .done(function (history) {
        if (campaignDetailState.campaign && campaignDetailState.campaign.campaign_id === id) renderDetailHistory(history);
      })
      .fail(function (xhr) { setCampaignMessage("#campaign-history-error", campaignError(xhr)); })
      .always(function () { $("#btn-campaign-history-refresh").prop("disabled", false).text("Refresh history"); });
  }
  function renderCampaignReport(campaign, response) {
    var summary = response.summary || response.metrics || response;
    var row = summary.campaign_id ? summary : (response.items || []).filter(function (item) { return item.campaign_id === campaign.campaign_id; })[0] || {};
    $("#campaign-report-spend").text(fmtVnd(row.total_spend));
    $("#campaign-report-impressions").text(fmt.int(row.total_impressions || 0));
    $("#campaign-report-conversions").text(fmt.int(row.total_conversions || 0));
    $("#campaign-report-roas").text((parseFloat(row.roas) || 0).toFixed(2) + "x");
    var $daily = $("#campaign-report-daily").empty();
    (response.daily || response.daily_metrics || []).forEach(function (item) {
      $("<tr>").append($("<td>").addClass("py-3 pr-4 text-slate-700").text(item.report_date || "-"), $("<td>").addClass("py-3 pr-4").text(fmtVnd(item.spend)), $("<td>").addClass("py-3 pr-4").text(fmt.int(item.impressions || 0)), $("<td>").addClass("py-3 pr-4").text(fmt.int(item.clicks || 0)), $("<td>").addClass("py-3 pr-4").text(fmt.int(item.conversions || 0)), $("<td>").addClass("py-3").text(fmtVnd(item.revenue_estimated))).appendTo($daily);
    });
    if (!$daily.children().length) $("<tr>").append($("<td>").attr("colspan", 6).addClass("py-6 text-center text-slate-500").text("No performance data recorded for this date range.")).appendTo($daily);
    var warning = row.campaign_id && !row.total_conversions ? "No conversions are recorded. CPA must not be interpreted as positive performance." : "";
    $("#campaign-report-warning").toggleClass("hidden", !warning).text(warning);
  }

  function loadCampaignReport(campaign) {
    var request = ++campaignReportRequest;
    var generation = campaignDetailGeneration;
    var params = {};
    if ($("#campaign-report-start").val()) params.start_date = $("#campaign-report-start").val();
    if ($("#campaign-report-end").val()) params.end_date = $("#campaign-report-end").val();
    if (params.start_date && params.end_date && params.end_date < params.start_date) {
      setCampaignMessage("#campaign-details-error", "End date cannot be before start date.");
      return;
    }
    api("/campaigns/" + encodeURIComponent(campaign.campaign_id) + "/report", params).done(function (response) {
      if (request !== campaignReportRequest || generation !== campaignDetailGeneration) return;
      renderCampaignReport(campaign, response);
    }).fail(function (xhr) {
      if (request !== campaignReportRequest || generation !== campaignDetailGeneration) return;
      setCampaignMessage("#campaign-details-error", campaignError(xhr));
    });
  }

  function applyCampaignWorkflowStudio($studio, options) {
    var studio = $studio.data("workflowStudio");
    studio.setSegment(options.segments[0]);
    var result = studio.setWorkflow(options.workflow);
    if (!result.ok) {
      throw new Error(
        "Could not refresh the saved workflow: " +
        result.issues.map(function (issue) { return issue.msg; }).join("; ")
      );
    }
  }

  function mountCampaignWorkflowStudio($studio, options) {
    var studio = $studio.data("workflowStudio");
    if (studio) {
      if (!$studio.data("workflowStudioReady")) {
        $studio.data("workflowStudioPendingOptions", options);
        return;
      }
      applyCampaignWorkflowStudio($studio, options);
      return;
    }
    $studio.removeData("workflowStudioPendingOptions");
    $studio.data("workflowStudioReady", false);
    var mount = {};
    $studio.data("workflowStudioMount", mount);
    var mountedOptions = $.extend({}, options, {
      onReady: function () {
        if ($studio.data("workflowStudioMount") !== mount) return;
        $studio.data("workflowStudioReady", true);
        var pendingOptions = $studio.data("workflowStudioPendingOptions");
        if (!pendingOptions) return;
        $studio.removeData("workflowStudioPendingOptions");
        try {
          applyCampaignWorkflowStudio($studio, pendingOptions);
        } catch (error) {
          $studio.removeData("workflowStudio").removeData("workflowStudioMount").empty();
          renderCampaignWorkflowError($studio, error.message);
        }
      },
    });
    $studio.workflowStudio(mountedOptions);
  }

  function renderCampaignWorkflowError($studio, message) {
    $("<div>")
      .attr("role", "alert")
      .addClass("rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700")
      .text(message)
      .appendTo($studio);
  }

  var experimentSegments = [];

  function showExperimentError(message) {
    $("#campaign-experiment-error").toggleClass("hidden", !message).text(message || "");
  }

  function loadExperimentSegments(callback) {
    if (experimentSegments.length) {
      callback(experimentSegments);
      return;
    }
    api("/segments/", { skip: 0, limit: 100 }).done(function (segments) {
      experimentSegments = (segments || []).filter(segmentIsReady);
      callback(experimentSegments);
    }).fail(function () {
      showExperimentError("Could not load active segments for the experiment.");
    });
  }

  function updateExperimentAllocation() {
    var total = 0;
    $(".campaign-experiment-allocation-input").each(function () { total += Number($(this).val()) || 0; });
    var valid = total === 100;
    $("#campaign-experiment-allocation").text(total + "% allocated").toggleClass("text-emerald-700", valid).toggleClass("text-rose-700", !valid);
    $("#campaign-experiment-form button[type=submit]").prop("disabled", !valid).toggleClass("opacity-50 cursor-not-allowed", !valid);
  }

  function addExperimentVariant(variant) {
    variant = variant || {};
    var index = $("#campaign-experiment-variants .campaign-experiment-variant").length;
    var key = variant.variant_key || String.fromCharCode(65 + index);
    var $row = $("<div>").addClass("campaign-experiment-variant rounded-xl border border-slate-200 bg-slate-50/60 p-4").attr("data-variant-key", key);
    var $top = $("<div>").addClass("grid grid-cols-1 md:grid-cols-12 gap-3 items-end");
    var $name = $("<label>").addClass("text-xs font-semibold text-slate-600 md:col-span-4").text("Variant " + key + " name");
    $("<input>").attr({ type: "text", required: true }).addClass("campaign-experiment-variant-name mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm").val(variant.name || (index === 0 ? "Control" : "Variant " + key)).appendTo($name);
    var $segment = $("<label>").addClass("text-xs font-semibold text-slate-600 md:col-span-4").text("Target segment");
    var $select = $("<select>").addClass("campaign-experiment-segment mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm");
    $("<option>").val("").text("Choose segment...").appendTo($select);
    experimentSegments.forEach(function (segment) { $("<option>").val(segment.segment_id).text(segment.segment_name + " (" + fmt.int(segment.member_count || 0) + " profiles)").appendTo($select); });
    $select.val(variant.segment_id || "").appendTo($segment);
    var $allocation = $("<label>").addClass("text-xs font-semibold text-slate-600 md:col-span-2").text("Traffic %");
    $("<input>").attr({ type: "number", min: "0", max: "100", step: "1" }).addClass("campaign-experiment-allocation-input mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm").val(variant.allocation_percentage == null ? (index < 2 ? 50 : 0) : variant.allocation_percentage).appendTo($allocation);
    var $control = $("<label>").addClass("flex items-center gap-2 pb-2 text-xs font-semibold text-slate-600 md:col-span-1");
    $("<input>").attr({ type: "checkbox" }).addClass("campaign-experiment-control h-4 w-4 rounded border-slate-300 text-indigo-600").prop("checked", variant.is_control || index === 0).appendTo($control);
    $("<span>").text("Control").appendTo($control);
    var $remove = $("<button>").attr({ type: "button", title: "Remove variant", "aria-label": "Remove variant" }).addClass("campaign-experiment-remove inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-500 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700 md:col-span-1");
    $("<i>").addClass("bi bi-trash3").attr("aria-hidden", "true").appendTo($remove);
    $top.append($name, $segment, $allocation, $control, $remove);
    $row.append($top).appendTo("#campaign-experiment-variants");
    updateExperimentAllocation();
  }

  function resetExperimentForm() {
    $("#campaign-experiment-name").val("");
    $("#campaign-experiment-metric").val("conversions");
    $("#campaign-experiment-variants").empty();
    loadExperimentSegments(function () {
      addExperimentVariant({ variant_key: "A", name: "Control", allocation_percentage: 50, is_control: true });
      addExperimentVariant({ variant_key: "B", name: "Variant B", allocation_percentage: 50, is_control: false });
    });
  }

  function createCampaignExperiment(campaignId, event) {
    event.preventDefault();
    showExperimentError("");
    var variants = [];
    var total = 0;
    var controls = 0;
    var valid = true;
    $("#campaign-experiment-variants .campaign-experiment-variant").each(function () {
      var $row = $(this);
      var allocation = Number($row.find(".campaign-experiment-allocation-input").val()) || 0;
      var segmentId = $row.find(".campaign-experiment-segment").val();
      var name = $.trim($row.find(".campaign-experiment-variant-name").val());
      var isControl = $row.find(".campaign-experiment-control").prop("checked");
      total += allocation;
      controls += isControl ? 1 : 0;
      if (!name || !segmentId) valid = false;
      variants.push({ variant_key: $row.data("variant-key"), name: name, segment_id: segmentId, allocation_percentage: allocation, is_control: isControl });
    });
    if (variants.length < 2 || total !== 100 || controls !== 1 || !valid) {
      showExperimentError("Add at least two variants, choose a segment for each, select one control, and allocate exactly 100% traffic.");
      return;
    }
    var $submit = $("#campaign-experiment-form button[type=submit]").prop("disabled", true).text("Creating...");
    api("/campaigns/" + encodeURIComponent(campaignId) + "/experiments", {
      name: $.trim($("#campaign-experiment-name").val()),
      primary_metric: $("#campaign-experiment-metric").val(),
      variants: variants
    }, "POST").done(function () {
      resetExperimentForm();
      loadCampaignExperiments(campaignId);
    }).fail(function (xhr) {
      showExperimentError(campaignError(xhr));
    }).always(function () { $submit.prop("disabled", false).text("Create experiment"); });
  }

  function renderExperimentCard(experiment) {
    var $card = $("<article>").addClass("bg-white border border-slate-200 rounded-2xl p-6");
    var $header = $("<div>").addClass("flex flex-wrap items-start justify-between gap-3");
    var $title = $("<div>");
    $("<h3>").addClass("text-base font-bold text-slate-900").text(experiment.name).appendTo($title);
    $("<p>").addClass("mt-1 text-xs text-slate-500").text("Metric: " + experiment.primary_metric + "  ·  " + experiment.variants.length + " variants").appendTo($title);
    var $status = $("<select>").addClass("campaign-experiment-status rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold").attr("data-experiment-id", experiment.experiment_id);
    ["Draft", "Running", "Paused", "Completed", "Cancelled"].forEach(function (status) { $("<option>").val(status).text(status).prop("selected", status === experiment.status).appendTo($status); });
    $header.append($title, $status);
    var $table = $("<table>").addClass("mt-5 w-full text-sm");
    var $head = $("<tr>").addClass("border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500");
    ["Variant", "Segment", "Allocation", "Conversions", "CVR", "ROAS", "Winner"].forEach(function (label) { $("<th>").addClass("py-3 pr-3").text(label).appendTo($head); });
    $("<thead>").append($head).appendTo($table);
    var $body = $("<tbody>").addClass("divide-y divide-slate-100");
    var performanceById = {};
    api("/campaign-experiments/" + encodeURIComponent(experiment.experiment_id) + "/performance").done(function (performance) {
      (performance || []).forEach(function (item) { performanceById[item.variant_id] = item; });
      $body.empty();
      experiment.variants.forEach(function (variant) {
        var item = performanceById[variant.variant_id] || {};
        var $row = $("<tr>");
        var segment = experimentSegments.filter(function (candidate) { return candidate.segment_id === variant.segment_id; })[0];
        [variant.name, segment ? segment.segment_name : variant.segment_id, variant.allocation_percentage + "%", item.conversions || 0, (Number(item.conversion_rate) || 0).toFixed(2) + "%", (Number(item.roas) || 0).toFixed(2) + "x"].forEach(function (value) { $("<td>").addClass("py-3 pr-3 text-slate-700").text(value).appendTo($row); });
        var $winner = $("<button>").attr({ type: "button", title: "Select winning variant", "aria-label": "Select " + variant.name + " as winner" }).addClass("campaign-experiment-winner rounded-lg border border-slate-300 px-2 py-1 text-xs font-semibold text-slate-600 hover:border-indigo-300 hover:text-indigo-700").attr("data-experiment-id", experiment.experiment_id).attr("data-variant-id", variant.variant_id).text(experiment.winning_variant_id === variant.variant_id ? "Winner" : "Select");
        $("<td>").addClass("py-3").append($winner).appendTo($row);
        $body.append($row);
      });
    });
    $table.append($body);
    $card.append($header, $table);
    return $card;
  }

  function loadCampaignExperiments(campaignId) {
    api("/campaigns/" + encodeURIComponent(campaignId) + "/experiments").done(function (experiments) {
      var $list = $("#campaign-experiments-list").empty();
      if (!experiments || !experiments.length) {
        $("<div>").addClass("rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500").text("No experiments yet. Create one to compare audience segments.").appendTo($list);
        return;
      }
      experiments.forEach(function (experiment) { $list.append(renderExperimentCard(experiment)); });
    }).fail(function (xhr) { showExperimentError(campaignError(xhr)); });
  }

  function loadCampaignDetails(campaignId, successMessage) {
    var generation = ++campaignDetailGeneration;
    var selectedPanel = $(".campaign-details-tab[aria-selected='true']").data("panel") || "overview";
    campaignDetailState.campaign = null;
    setCampaignMessage("#campaign-details-error", "");
    setCampaignMessage("#campaign-details-success", "");
    $("#campaign-details-loading").removeClass("hidden");
    $("#campaign-details-content").addClass("hidden");
    $.when(
      api("/campaigns/" + encodeURIComponent(campaignId)),
      api("/campaigns/" + encodeURIComponent(campaignId) + "/history"),
      api("/campaigns/" + encodeURIComponent(campaignId) + "/workflow")
    )
      .done(function (campaignResult, historyResult, workflowResult) {
        if (generation !== campaignDetailGeneration) return;
        var campaign = campaignResult[0];
        renderDetailMetadata(campaign);
        var savedWorkflow = workflowResult[0];
        var workflow = savedWorkflow && savedWorkflow.workflow;
        var segmentId = campaign.segment_id ||
          (workflow && workflow.root && workflow.root.config && workflow.root.config.segment_id);
        var $workflowStudio = $("#campaign-details-workflow-studio");
        var existingWorkflowStudio = $workflowStudio.data("workflowStudio");
        $("#campaign-details-workflow-studio").attr("aria-busy", "false");
        $("#campaign-details-workflow-version").empty();
        if (!workflow) {
          if (existingWorkflowStudio) $workflowStudio.removeData("workflowStudio").removeData("workflowStudioMount");
          $workflowStudio.empty();
          $("<div>")
            .attr("role", "status")
            .addClass("rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500")
            .text("No customer journey workflow has been saved for this campaign yet.")
            .appendTo($workflowStudio);
        } else if (!workflow.root || !segmentId) {
          if (existingWorkflowStudio) $workflowStudio.removeData("workflowStudio").removeData("workflowStudioMount");
          $workflowStudio.empty();
          renderCampaignWorkflowError(
            $workflowStudio,
            "The saved customer journey workflow is missing its audience or has an invalid structure."
          );
        } else {
          var studioOptions = {
            title: campaign.name || "Customer Engagement Workflow",
            workflow: workflow,
            segmentId: String(segmentId),
            segmentName: campaign.segment_name || "Campaign audience",
            segments: [{
              segment_id: String(segmentId),
              segment_name: campaign.segment_name || "Campaign audience",
              description: ""
            }],
            readOnly: true,
            loadProfiles: function () {
              return [];
            },
          };
          try {
            mountCampaignWorkflowStudio($workflowStudio, studioOptions);
          } catch (error) {
            $workflowStudio.removeData("workflowStudio").removeData("workflowStudioMount").empty();
            renderCampaignWorkflowError($workflowStudio, error.message);
          }
          var workflowVersionLabel = "Version " + savedWorkflow.workflow_version;
          if (savedWorkflow.approval_status) {
            workflowVersionLabel += " | " + savedWorkflow.approval_status;
          }
          $("#campaign-details-workflow-version").text(workflowVersionLabel);
        }
        loadDetailAudience(campaign.segment_id);
        renderDetailHistory(historyResult[0]);
        $("#campaign-details-title").text(campaign.name || "Campaign details");
        $("#campaign-details-subtitle").text([campaign.campaign_code, campaign.channel, campaign.platform].filter(Boolean).join(" / "));
        var statusPresentation = campaignStatusPresentation(campaign.status);
        $("#campaign-details-status").attr("class", "inline-flex items-center gap-1.5 text-xs font-semibold rounded-md px-2.5 py-1.5 " + statusPresentation.classes).attr("aria-label", "Campaign status: " + statusPresentation.label);
        $("#campaign-details-status-icon").attr("class", "bi " + statusPresentation.icon);
        $("#campaign-details-status-label").text(statusPresentation.label);
        var approvalStatus = campaignApprovalStatus(campaign);
        var workflowStatus = workflowApprovalStatus(savedWorkflow);
        var workflowApproved = isWorkflowApproved(savedWorkflow);
        var isAdmin = canReviewCampaign();
        campaignDetailState.campaign = campaign;
        campaignDetailState.workflow = savedWorkflow;
        campaignDetailState.actions = campaignStateActions(campaign, savedWorkflow, isAdmin);
        $("#campaign-state-approval").text(approvalStatus);
        $("#campaign-state-workflow").text((savedWorkflow.workflow_version != null ? "v" + savedWorkflow.workflow_version + " - " : "") + (workflowStatus || "Not saved"));
        $("#campaign-state-execution").text(campaign.status || "Draft");
        $("#campaign-details-approval").text(approvalStatus).attr("class", "text-xs font-semibold rounded-md px-2.5 py-1.5 bg-indigo-50 text-indigo-700");
        if (approvalStatus === "Draft") {
          setCampaignActionHint("Save the campaign, then submit it for approval. A tenant admin must approve it before it can run.");
        } else if (workflowStatus === "InReview") {
          setCampaignActionHint("The workflow is waiting for admin review. Approve the workflow first, then approve the campaign.");
        } else if (isAdmin && workflowStatus === "Approved" && approvalStatus === "InReview") {
          setCampaignActionHint("Workflow approved. Continue with campaign approval when the campaign is InReview.");
        } else if (approvalStatus === "InReview") {
          setCampaignActionHint("The campaign is waiting for admin approval. Starting it is available only after approval.");
        } else if (approvalStatus === "Approved" && !workflowApproved) {
          setCampaignActionHint("The campaign is approved, but its pinned workflow is not approved yet.");
        } else if (approvalStatus === "Approved" && String(campaign.status || "").toLowerCase() === "running") {
          setCampaignActionHint("Dagster is running this campaign. Monitor the run in the Dagster UI.");
        } else if (approvalStatus === "Rejected" || workflowStatus === "Rejected") {
          setCampaignActionHint("A review was rejected. Read the decision in history, then edit and save a revised campaign or workflow before review.");
        } else if (String(campaign.status || "").toLowerCase() === "completed") {
          setCampaignActionHint("Campaign execution is complete. Admins can use Manage state to fully replay it; messages may be sent again. Check audience-sync child runs separately.");
        } else if (approvalStatus === "Approved" && workflowApproved) {
          setCampaignActionHint("Campaign and pinned workflow are approved. Open Manage state to start or retry execution.");
        } else {
          setCampaignActionHint("");
        }
        $("#campaign-details-loading").addClass("hidden");
        $("#campaign-details-content").removeClass("hidden");
        activateCampaignDetailsTab(selectedPanel);
        if (successMessage) setCampaignMessage("#campaign-details-success", successMessage);
        $("#btn-campaign-details-edit").toggleClass("hidden", String(campaign.status || "").toLowerCase() === "running");
        $("#campaign-experiment-form").attr("data-campaign-id", campaignId);
        $("#btn-campaign-details-edit").off("click.c360campaign").on("click.c360campaign", function () { C360.router.navigate("/campaigns/" + encodeURIComponent(campaignId) + "/edit"); });
        $("#btn-campaign-report-refresh").off("click.c360campaign").on("click.c360campaign", function () { loadCampaignReport(campaign); });
        loadCampaignReport(campaign);
        loadExperimentSegments(function () { resetExperimentForm(); loadCampaignExperiments(campaignId); });
      })
      .fail(function (xhr) {
        if (generation !== campaignDetailGeneration) return;
        $("#campaign-details-loading").addClass("hidden");
        setCampaignMessage("#campaign-details-error", campaignError(xhr));
      });
  }

  function activateCampaignDetailsTab(panel) {
    $(".campaign-details-tab").each(function () {
      var selected = $(this).data("panel") === panel;
      $(this)
        .attr("aria-selected", selected ? "true" : "false")
        .attr("tabindex", selected ? "0" : "-1")
        .toggleClass("border-indigo-600 text-indigo-700", selected)
        .toggleClass("border-transparent text-slate-500", !selected);
    });
    $(".campaign-details-panel").addClass("hidden");
    $("#campaign-details-panel-" + panel).removeClass("hidden");
  }

  function bindWorkspaceEvents() {
    campaignDetailGeneration += 1;
    campaignDetailState.campaign = null;
    var $doc = $(document);
    $doc.off(".c360campaignworkspace");
    $doc.on("click.c360campaignworkspace", "#btn-campaign-manage-state", function () { openCampaignStateModal(); });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-details-refresh", function () {
      if (campaignDetailState.campaign && !campaignDetailState.busy) loadCampaignDetails(campaignDetailState.campaign.campaign_id);
    });
    $("#campaign-details-tab-history").text("State history").attr("data-assistant-label", "State history");
    $doc.on("click.c360campaignworkspace", "[data-campaign-state-close]", closeCampaignStateModal);
    $doc.on("keydown.c360campaignworkspace", "#campaign-state-modal", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeCampaignStateModal();
      }
    });
    $("#campaign-state-modal").off("cancel.c360campaign").on("cancel.c360campaign", function (event) {
      event.preventDefault();
      closeCampaignStateModal();
    });
    $doc.on("change.c360campaignworkspace", "#campaign-state-action", updateCampaignStateAction);
    $doc.on("change.c360campaignworkspace", "#campaign-state-restart-ack", updateCampaignStateAction);
    $doc.on("submit.c360campaignworkspace", "#campaign-state-form", submitCampaignState);
    $doc.on("click.c360campaignworkspace", "#btn-campaign-state-history", function () {
      activateCampaignDetailsTab("history");
      $("#campaign-details-tab-history").trigger("focus");
    });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-history-refresh", refreshCampaignHistory);
    $doc.on("change.c360campaignworkspace", "#campaign-history-filter", function () { renderDetailHistory(campaignDetailState.history); });
    $doc.on("keydown.c360campaignworkspace", ".campaign-details-tab", function (event) {
      var $tabs = $(".campaign-details-tab");
      var index = $tabs.index(this);
      if (event.key === "ArrowRight") index = (index + 1) % $tabs.length;
      else if (event.key === "ArrowLeft") index = (index + $tabs.length - 1) % $tabs.length;
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = $tabs.length - 1;
      else return;
      event.preventDefault();
      var $tab = $tabs.eq(index);
      activateCampaignDetailsTab($tab.data("panel"));
      $tab.trigger("focus");
    });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-create", function () { C360.router.navigate("/campaigns/new/edit"); });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-editor-back, #btn-campaign-editor-cancel, #btn-campaign-details-back", function () { C360.router.navigate("/campaigns"); });
    $doc.on("submit.c360campaignworkspace", "#campaign-editor-form", saveCampaignEditor);
    $doc.on("change.c360campaignworkspace", "#campaign-field-channel", function () {
      populateCampaignMetadataOptions($(this).val(), "", $("#campaign-field-lang").val());
    });
    $doc.on("change.c360campaignworkspace", "#campaign-field-objective", function () {
      var $stage = $("#campaign-field-journey-stage");
      if (!$stage.val()) {
        var inferred = inferJourneyStageFromObjective($(this).val());
        if (inferred) $stage.val(inferred);
      }
    });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-experiment-add-variant", function () { loadExperimentSegments(function () { addExperimentVariant(); }); });
    $doc.on("click.c360campaignworkspace", ".campaign-experiment-remove", function () { if ($("#campaign-experiment-variants .campaign-experiment-variant").length > 2) { $(this).closest(".campaign-experiment-variant").remove(); updateExperimentAllocation(); } });
    $doc.on("input.c360campaignworkspace", ".campaign-experiment-allocation-input", updateExperimentAllocation);
    $doc.on("submit.c360campaignworkspace", "#campaign-experiment-form", function (event) { createCampaignExperiment($(this).data("campaign-id"), event); });
    $doc.on("change.c360campaignworkspace", ".campaign-experiment-status", function () { var $control = $(this); api("/campaign-experiments/" + encodeURIComponent($control.data("experiment-id")), { status: $control.val() }, "PATCH").fail(function (xhr) { showExperimentError(campaignError(xhr)); }); });
    $doc.on("click.c360campaignworkspace", ".campaign-experiment-winner", function () { var $button = $(this); api("/campaign-experiments/" + encodeURIComponent($button.data("experiment-id")), { winning_variant_id: $button.data("variant-id"), status: "Completed" }, "PATCH").done(function () { var campaignId = $("#campaign-experiment-form").data("campaign-id"); loadCampaignExperiments(campaignId); }).fail(function (xhr) { showExperimentError(campaignError(xhr)); }); });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-copy-segment-id", function () {
      var segmentId = $(this).data("segment-id");
      if (!segmentId || !navigator.clipboard) return;
      var $label = $("#campaign-copy-segment-label");
      navigator.clipboard.writeText(String(segmentId)).then(function () {
        $label.text("Copied");
        setTimeout(function () { $label.text("Copy ID"); }, 1400);
      });
    });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-select-segment, #btn-campaign-change-segment", openSegmentPicker);
    $doc.on("click.c360campaignworkspace", "#btn-campaign-segment-modal-close, #btn-campaign-segment-modal-cancel", closeSegmentPicker);
    $doc.on("click.c360campaignworkspace", "#campaign-segment-modal", function (event) { if (event.target === this) closeSegmentPicker(); });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-clear-segment", function () {
      campaignSegmentRequest += 1;
      renderSegmentSelection(null);
    });
    $doc.on("input.c360campaignworkspace", "#campaign-segment-search", function () { renderSegmentResults($(this).val()); });
    $doc.on("click.c360campaignworkspace", ".campaign-segment-option", function () {
      var segment = segmentPickerState.byId[$(this).data("segment-id")];
      if (!segment) return;
      campaignSegmentRequest += 1;
      renderSegmentSelection(segment);
      closeSegmentPicker();
    });
    $doc.on("click.c360campaignworkspace", ".campaign-details-tab", function () { activateCampaignDetailsTab($(this).data("panel")); });
  }

  // ---- filter / control bindings ----

  function bindFilters() {
    var $doc = $(document);
    $doc.off(".c360campaign");

    dtv.bindRowClick();
    dtv.bindSearch("#campaign-filter-search", "search", 300);
    dtv.bindSelect("#campaign-filter-status",    "status");
    dtv.bindSelect("#campaign-filter-channel",   "channel");
    dtv.bindSelect("#campaign-filter-platform",  "platform");
    dtv.bindSelect("#campaign-filter-objective", "objective");
    dtv.bindLoadMore();

    // Sort-by select
    $doc.on("change.c360campaign", "#campaign-filter-sort-by", function () {
      state.sortBy = $(this).val();
      dtv.load(false);
    });

    // Sort-order toggle
    $doc.on("click.c360campaign", "#campaign-filter-sort-order", function () {
      state.sortOrder = state.sortOrder === "desc" ? "asc" : "desc";
      var isAsc = state.sortOrder === "asc";
      $("#campaign-sort-label").text(state.sortOrder.toUpperCase());
      $("#campaign-sort-icon").html(isAsc ? ICONS.sortAsc : ICONS.sortDesc);
      dtv.load(false);
    });

    // Reset all filters
    $doc.on("click.c360campaign", "#campaign-filter-reset", function () {
      $("#campaign-filter-search").val("");
      $("#campaign-filter-status, #campaign-filter-channel, #campaign-filter-platform, #campaign-filter-objective").val("");
      $("#campaign-filter-sort-by").val("total_spend");
      state.sortBy    = "total_spend";
      state.sortOrder = "desc";
      $("#campaign-sort-label").text("DESC");
      $("#campaign-sort-icon").html(ICONS.sortDesc);
      // clear each filter key then reload once
      ["search","status","channel","platform","objective"].forEach(function (k) { dtv.setFilter(k, ""); });
    });

    // Trend range buttons
    $doc.on("click.c360campaign", ".campaign-trend-range", function () {
      $(".campaign-trend-range")
        .removeClass("border-violet-400 text-violet-700 bg-violet-50")
        .addClass("border-slate-200 text-slate-600");
      $(this).addClass("border-violet-400 text-violet-700 bg-violet-50")
             .removeClass("border-slate-200 text-slate-600");
      state.trendDays = parseInt($(this).data("days"), 10);
      loadSpendTrend();
    });

    // Top campaigns metric toggle
    $doc.on("click.c360campaign", "#btn-top-by-conversions, #btn-top-by-roas", function () {
      state.topMetric = this.id === "btn-top-by-roas" ? "roas" : "conversions";
      $("#btn-top-by-conversions, #btn-top-by-roas")
        .removeClass("border-violet-400 text-violet-700 bg-violet-50")
        .addClass("border-slate-200 text-slate-600");
      $(this).addClass("border-violet-400 text-violet-700 bg-violet-50")
             .removeClass("border-slate-200 text-slate-600");
      if (_topData) renderTopChart(_topData);
    });

    $doc.on("click.c360campaign", "#btn-campaign-refresh", loadAll);
  }

  // ---- orchestration ----

  function loadAll() {
    destroyCharts();
    $.when(loadKPIs(), dtv.load(false), loadSpendTrend(), loadTopCampaigns());
  }

  // ---- route registration ----

  C360.router.define("/campaigns", {
    section: "view-campaigns",
    tab: "campaigns",
    mount: function () {
      $("#campaign-content").html(C360.templates.html("campaign-dashboard"));
      dtv = createDtv();
      bindFilters();
      bindWorkspaceEvents();
      loadAll();
    }
  });

  C360.router.define("/campaigns/:id/edit", {
    section: "view-campaigns",
    tab: "campaigns",
    mount: function (params) {
      $("#campaign-content").html(C360.templates.html("campaign-editor"));
      bindWorkspaceEvents();
      loadCampaignEditor(params.id === "new" ? null : params.id);
    }
  });

  C360.router.define("/campaigns/:id", {
    section: "view-campaigns",
    tab: "campaigns",
    mount: function (params) {
      $("#campaign-content").html(C360.templates.html("campaign-details"));
      bindWorkspaceEvents();
      loadCampaignDetails(params.id);
    }
  });

  C360.campaignView = {
    load: loadAll,
    openCreateForStage: function (stage) {
      pendingCampaignDraftJourneyStage = stage || "";
      C360.router.navigate("/campaigns/new/edit");
    }
  };

})(window.C360);
