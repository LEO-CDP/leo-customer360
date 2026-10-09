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
    Zalo: "💬", Adjust: "📱", YouTube: "▶️"
  };

  var CHANNEL_ICONS = {
    "Paid Search":       "🔍",
    "Paid Social":       "📲",
    "Push Notification": "🔔",
    "Email":             "✉️",
    "Video":             "▶️"
  };

  var APPROVAL_BADGE = {
    Approved: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
    InReview: "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-200",
    Rejected: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200"
  };

  var STATUS_BADGE = {
    Active:    "bg-emerald-100 text-emerald-700",
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
    { label: "Approval", type: "badge",  field: "approvalLabel",   classField: "approvalBadgeClass" },
    { label: "Owner",    field: "ownerLabel",       muted: true },
    { label: "Schedule", field: "scheduleLabel" },
    { label: "Budget",   field: "budgetLabel",      cellClass: "text-right" },
    { label: "Planner",  field: "plannerLabel" },
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
      ctrLabel:          ratio(c.ctr_percentage, "%"),
      conversionsLabel:  fmt.int(c.total_conversions),
      // a zero-conversion campaign has no meaningful CPA
      cpaLabel:          c.total_conversions ? fmtVnd(c.cpa) : "—",
      approvalLabel:     c.approval_status || "—",
      approvalBadgeClass: APPROVAL_BADGE[c.approval_status] || "bg-slate-100 text-slate-500",
      ownerLabel:        c.user_id ? String(c.user_id).slice(0, 8) : "—",
      scheduleLabel:     [c.start_date, c.end_date].filter(Boolean).join(" → ") || "—",
      budgetLabel:       c.budget_amount == null ? "—" : fmtVnd(c.budget_amount) + " " + (c.currency || ""),
      plannerLabel:      c.agent_display_name ? c.agent_display_name + " v" + c.agent_instruction_version : "—",
      roasLabel:         ratio(c.roas, "×"),
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
        $("#kpi-campaign-ctr").text(ratio(data.overall_ctr, "%"));
        $("#kpi-campaign-conversions").text(fmt.int(data.total_conversions));
        $("#kpi-campaign-cvr").text(ratio(data.overall_cvr, "%"));
        $("#kpi-campaign-revenue").text(fmtVnd(data.total_revenue));
        $("#kpi-campaign-roas").text(ratio(data.overall_roas, "×"));
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
    if (Array.isArray(detail)) {
      return detail.map(function (item) {
        var field = Array.isArray(item.loc) ? item.loc.filter(function (part) { return part !== "body"; }).join(".") : "";
        return (field ? field + ": " : "") + (item.msg || JSON.stringify(item));
      }).join("; ");
    }
    if (detail && typeof detail === "object" && detail.message) return detail.message;
    return typeof detail === "string" ? detail : "Request failed. Please try again.";
  }

  function setCampaignMessage(selector, message) {
    $(selector).toggleClass("hidden", !message).text(message || "");
  }

  function canReviewCampaign() {
    var user = C360.config.currentUser();
    var roles = user.roles || [];
    var reviewerRoles = ["platform_admin", "super_admin", "system_admin", "tenant_admin", "admin"];
    // The dev root login (SSO_LOGIN=false) is the super-admin; the API lets it review too.
    return !!user.isRoot || roles.some(function (role) { return reviewerRoles.indexOf(String(role).toLowerCase()) !== -1; });
  }

  function campaignStatusPresentation(status) {
    var key = String(status || "Draft").trim().toLowerCase();
    var presentations = {
      draft: { label: "Draft", icon: "bi-file-earmark-text", classes: "bg-slate-100 text-slate-700 ring-1 ring-inset ring-slate-200" },
      active: { label: "Active", icon: "bi-play-circle-fill", classes: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200" },
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
    if (!segmentId) {
      renderSegmentSelection(null, { locked: locked, allowClear: allowClear });
      return;
    }
    api("/segments/" + encodeURIComponent(segmentId))
      .done(function (segment) {
        renderSegmentSelection(segment, {
          locked: locked,
          allowClear: allowClear,
          warning: segmentIsReady(segment) ? "" : "This segment is no longer active or has no computed membership snapshot. Choose another segment before saving."
        });
      })
      .fail(function () {
        renderSegmentSelection({ segment_id: segmentId, segment_name: "Unavailable segment", segment_tag: "" }, {
          locked: locked,
          allowClear: allowClear,
          warning: "The saved segment could not be loaded. Choose another segment before saving."
        });
      });
  }

  // ---- campaign editor: planned-draft create (new) + governed edit (existing) ----

  var ZNS_CHANNEL = "zalo_zns";
  var editor = { mode: "create", campaign: null, stale: null, agents: {}, saveLabel: "Plan draft", scrollToReplan: false };

  function setEditorMode(mode) {
    editor.mode = mode;
    editor.saveLabel = mode === "edit" ? "Save campaign" : "Plan draft";
    $("#campaign-editor-view [data-mode]").each(function () { $(this).toggleClass("hidden", $(this).data("mode") !== mode); });
    $("#btn-campaign-editor-save").text(editor.saveLabel);
    $("#campaign-governed-title").text(mode === "edit" ? "Governed" : "Plan inputs");
    $("#campaign-governed-hint").text(mode === "edit" ? "Governed — changes send an approved campaign back to review." : "The planner agent drafts the strategy and content plan from these inputs.");
    $("#campaign-general-hint").text(mode === "edit" ? "Saving these fields never changes approval." : "Optional. The planner fills in anything you leave blank.");
    $("#campaign-editor-title").text(mode === "edit" ? "Edit campaign" : "Create campaign");
    $("#campaign-editor-subtitle").text(mode === "edit" ? "Update campaign metadata through the governed campaign API." : "Plan a draft with an AI planner agent; it is created InReview for human approval.");
  }

  function clearEditorMessages() {
    setCampaignMessage("#campaign-editor-error", "");
    setCampaignMessage("#campaign-editor-success", "");
    $("#campaign-editor-blocked, #campaign-editor-stale").addClass("hidden");
  }

  // text = null clears the busy state; otherwise shows it and locks the submit/re-plan buttons.
  function setEditorBusy(text) {
    $("#campaign-editor-planning").toggleClass("hidden", !text);
    $("#campaign-editor-planning-text").text(text || "");
    $("#btn-campaign-editor-save, #btn-campaign-replan").prop("disabled", !!text).toggleClass("opacity-50 cursor-not-allowed", !!text);
    $("#btn-campaign-editor-save").text(text ? "Working..." : editor.saveLabel);
  }

  function plannerLabel(code) {
    var agent = editor.agents[code];
    return agent ? agent.display_name : code;
  }

  function loadPlannerAgents(selectedCode) {
    var $select = $("#campaign-field-agent").empty();
    var $hint = $("#campaign-agent-hint").addClass("hidden");
    editor.agents = {};
    api("/ai-agents/", { status: "ACTIVE", model_type: "generative_llm", limit: 100 })
      .done(function (agents) {
        (agents || []).forEach(function (agent) {
          editor.agents[agent.agent_code] = agent;
          $("<option>").val(agent.agent_code).text(agent.display_name + " · " + (agent.model_name || "no model") + " · v" + agent.instruction_version).appendTo($select);
        });
        if (!$select.children().length) {
          $("<option>").val("").text("No active planner agents").appendTo($select);
          $hint.removeClass("hidden").text("No active generative_llm agents are registered. Create one in AI Agents first.");
        } else if (selectedCode && editor.agents[selectedCode]) {
          $select.val(selectedCode);
        } else if (selectedCode) {
          $hint.removeClass("hidden").text("The original planner \"" + selectedCode + "\" is not active; choose another.");
        }
      })
      .fail(function () { $hint.removeClass("hidden").text("Could not load planner agents."); });
  }

  // Approved templates for the channel; in edit mode keeps the campaign's current template selectable.
  function loadTemplateOptions(channel, currentId) {
    var $select = $("#campaign-field-template").empty();
    var $hint = $("#campaign-template-hint").addClass("hidden");
    api("/campaigns/draft/template-options", { channel: channel })
      .done(function (rows) {
        var found = false;
        $("<option>").val("").text(currentId ? "Keep current template" : "Choose an approved template...").appendTo($select);
        (rows || []).forEach(function (row) {
          found = found || row.template_id === currentId;
          $("<option>").val(row.template_id).text(row.name || row.template_id).appendTo($select);
        });
        if (currentId && !found) $("<option>").val(currentId).text(currentId + " (current, not Approved)").appendTo($select);
        $select.val(currentId || "");
        if (!rows || !rows.length) $hint.removeClass("hidden").text("No Approved templates for this channel yet. Approve one in Templates first.");
      })
      .fail(function () { $hint.removeClass("hidden").text("Could not load templates."); });
  }

  function fillCampaignEditor(campaign) {
    editor.campaign = campaign;
    editor.stale = null;
    $("#campaign-editor-stale").addClass("hidden");
    $("#campaign-editor-id").val(campaign.campaign_id || "");
    $("#campaign-editor-updated-at").val(campaign.updated_at || "");
    $("#campaign-editor-version").text(campaign.updated_at ? "Version " + campaign.updated_at : "New campaign");
    $("#campaign-editor-approval").toggleClass("hidden", !campaign.approval_status).text(campaign.approval_status || "");
    $("#campaign-field-code").val(campaign.campaign_code || "");
    $("#campaign-field-name").val(campaign.name || "");
    $("#campaign-field-status").val(campaign.status || "Draft");
    $("#campaign-field-channel").val(campaign.channel || "");
    $("#campaign-field-platform").val(campaign.platform || "");
    $("#campaign-field-lang").val(campaign.lang || "en");
    $("#campaign-field-description").val(campaign.description || "");
    $("#campaign-field-keywords").val((campaign.keywords || []).join(", "));
    $("#campaign-field-start-date").val(campaign.start_date || "");
    $("#campaign-field-end-date").val(campaign.end_date || "");
    $("#campaign-field-budget").val(campaign.budget_amount == null ? "" : campaign.budget_amount);
    $("#campaign-field-currency").val(campaign.currency || "VND");
    $("#campaign-field-segment").val(campaign.segment_id || "");
    $("#campaign-field-objective").val(campaign.objective || "");
    $("#campaign-field-strategy").val(campaign.strategy_summary || "");
    $("#campaign-field-notes").val(((campaign.metadata_ || {}).editor_context || {}).notes || "");
    $("#campaign-editor-ai-plan").text(JSON.stringify(campaign.ai_plan || {}, null, 2));
    $("#campaign-editor-provenance").text(JSON.stringify((campaign.metadata_ || {}).agent_provenance || {}, null, 2));
    loadSelectedSegment(campaign.segment_id, false, false);
    loadTemplateOptions(campaign.channel === ZNS_CHANNEL ? ZNS_CHANNEL : "email", campaign.template_id);
    loadPlannerAgents(((campaign.metadata_ || {}).agent_provenance || {}).agent_code);
  }

  function initCreateForm() {
    editor.campaign = null;
    setEditorMode("create");
    $("#campaign-editor-id, #campaign-editor-updated-at").val("");
    $("#campaign-editor-version").text("New campaign");
    $("#campaign-editor-form")[0].reset();
    $("#campaign-field-segment").val("");
    $("#campaign-template-wrap").removeClass("hidden");
    renderSegmentSelection(null);
    loadTemplateOptions("email", null);
    loadPlannerAgents(null);
  }

  function loadCampaignEditor(campaignId) {
    clearEditorMessages();
    setEditorBusy(null);
    $("#campaign-editor-loading").toggleClass("hidden", !campaignId);
    $("#campaign-editor-form").toggleClass("hidden", !!campaignId);
    setEditorMode(campaignId ? "edit" : "create");
    if (!campaignId) {
      initCreateForm();
      return;
    }
    api("/campaigns/" + encodeURIComponent(campaignId))
      .done(function (campaign) {
        $("#campaign-editor-loading").addClass("hidden");
        $("#campaign-editor-form").removeClass("hidden");
        fillCampaignEditor(campaign);
        if (editor.scrollToReplan) {
          editor.scrollToReplan = false;
          $("#btn-campaign-replan")[0].scrollIntoView({ block: "center" });
        }
      })
      .fail(function (xhr) {
        $("#campaign-editor-loading").addClass("hidden");
        setCampaignMessage("#campaign-editor-error", campaignError(xhr));
      });
  }

  // Routes a failed save/plan/re-plan: stale conflict banner, blocked planner panel, or a plain message.
  function handleEditorFailure(xhr) {
    var detail = xhr && xhr.responseJSON ? xhr.responseJSON.detail : null;
    window.scrollTo(0, 0);
    if (xhr && xhr.status === 409 && detail && typeof detail === "object" && !Array.isArray(detail)) {
      if (detail.code === "stale_update") {
        editor.stale = detail.current;
        $("#campaign-editor-stale").removeClass("hidden");
        return;
      }
      if (detail.code === "agent_configuration_invalid") {
        var $reasons = $("#campaign-editor-blocked-reasons").empty();
        (detail.reasons || []).forEach(function (reason) { $("<li>").text(reason).appendTo($reasons); });
        $("#campaign-editor-blocked").removeClass("hidden");
        return;
      }
    }
    setCampaignMessage("#campaign-editor-error", campaignError(xhr));
  }

  function createPlannedDraft() {
    var isZns = $("#campaign-field-draft-channel").val() === ZNS_CHANNEL;
    var agentCode = fieldValue("#campaign-field-agent");
    var payload = {
      agent_code: agentCode,
      segment_id: fieldValue("#campaign-field-segment").trim(),
      objective: fieldValue("#campaign-field-objective").trim()
    };
    if (!payload.segment_id) return setCampaignMessage("#campaign-editor-error", "Choose an active audience segment.");
    if (!payload.objective) return setCampaignMessage("#campaign-editor-error", "Objective is required.");
    if (!agentCode) return setCampaignMessage("#campaign-editor-error", "Choose a planner agent.");
    if (!isZns) {
      payload.template_id = fieldValue("#campaign-field-template");
      if (!payload.template_id) return setCampaignMessage("#campaign-editor-error", "Choose an approved email template.");
    }
    var optional = {
      budget_time_constraints: fieldValue("#campaign-field-constraints").trim(),
      name: fieldValue("#campaign-field-name").trim(),
      campaign_code: fieldValue("#campaign-field-code").trim(),
      start_date: fieldValue("#campaign-field-start-date"),
      end_date: fieldValue("#campaign-field-end-date"),
      budget_amount: fieldValue("#campaign-field-budget"),
      currency: fieldValue("#campaign-field-currency").trim().toUpperCase()
    };
    Object.keys(optional).forEach(function (key) { if (optional[key] !== "") payload[key] = optional[key]; });
    if (payload.budget_amount !== undefined) payload.budget_amount = Number(payload.budget_amount);
    if (payload.start_date && payload.end_date && payload.end_date < payload.start_date) return setCampaignMessage("#campaign-editor-error", "End date cannot be before start date.");
    setEditorBusy("Planning with " + plannerLabel(agentCode) + "… this can take up to 30 seconds");
    api(isZns ? "/campaigns/zalo-draft" : "/campaigns/draft", payload, "POST")
      .done(function (campaign) { C360.router.navigate("/campaigns/" + encodeURIComponent(campaign.campaign_id)); })
      .fail(function (xhr) { setEditorBusy(null); handleEditorFailure(xhr); });
  }

  // Only the fields the user actually changed (cleared values are not sent: the API cannot null a field).
  function editorChanges() {
    var original = editor.campaign, changes = {};
    [
      ["campaign_code", "#campaign-field-code"], ["name", "#campaign-field-name"], ["status", "#campaign-field-status"],
      ["channel", "#campaign-field-channel"], ["platform", "#campaign-field-platform"], ["description", "#campaign-field-description"],
      ["lang", "#campaign-field-lang"], ["segment_id", "#campaign-field-segment"], ["template_id", "#campaign-field-template"],
      ["objective", "#campaign-field-objective"], ["strategy_summary", "#campaign-field-strategy"],
      ["start_date", "#campaign-field-start-date"], ["end_date", "#campaign-field-end-date"]
    ].forEach(function (pair) {
      var value = fieldValue(pair[1]).trim();
      if (value && value !== String(original[pair[0]] == null ? "" : original[pair[0]])) changes[pair[0]] = value;
    });
    var currency = fieldValue("#campaign-field-currency").trim().toUpperCase();
    if (currency && currency !== (original.currency || "")) changes.currency = currency;
    var budget = fieldValue("#campaign-field-budget");
    if (budget !== "" && (original.budget_amount == null || Number(budget) !== Number(original.budget_amount))) changes.budget_amount = Number(budget);
    var keywords = fieldValue("#campaign-field-keywords").split(",").map(function (item) { return item.trim(); }).filter(Boolean);
    if (keywords.join("\n") !== (original.keywords || []).join("\n")) changes.keywords = keywords;
    var context = (original.metadata_ || {}).editor_context || {};
    var notes = fieldValue("#campaign-field-notes").trim();
    if (notes !== String(context.notes || "")) changes.metadata = { editor_context: $.extend({}, context, { notes: notes }) };
    return changes;
  }

  function saveCampaignEdit() {
    var campaignId = fieldValue("#campaign-editor-id");
    if (!fieldValue("#campaign-field-name").trim()) return setCampaignMessage("#campaign-editor-error", "Campaign name is required.");
    var start = fieldValue("#campaign-field-start-date"), end = fieldValue("#campaign-field-end-date");
    if (start && end && end < start) return setCampaignMessage("#campaign-editor-error", "End date cannot be before start date.");
    var changes = editorChanges();
    if (!Object.keys(changes).length) return setCampaignMessage("#campaign-editor-error", "No changes to save.");
    setEditorBusy("Saving...");
    api("/campaigns/" + encodeURIComponent(campaignId) + "/draft", $.extend({ updated_at: editor.campaign.updated_at }, changes), "PATCH")
      .done(function (campaign) { C360.router.navigate("/campaigns/" + encodeURIComponent(campaign.campaign_id || campaignId)); })
      .fail(function (xhr) { setEditorBusy(null); handleEditorFailure(xhr); });
  }

  function replanCampaign() {
    var agentCode = fieldValue("#campaign-field-agent");
    clearEditorMessages();
    if (!agentCode) return setCampaignMessage("#campaign-editor-error", "Choose a planner agent.");
    setEditorBusy("Planning with " + plannerLabel(agentCode) + "… this can take up to 30 seconds");
    api("/campaigns/" + encodeURIComponent(fieldValue("#campaign-editor-id")) + "/draft", { updated_at: editor.campaign.updated_at, replan: true, agent_code: agentCode }, "PATCH")
      .done(function (campaign) {
        fillCampaignEditor(campaign);
        setCampaignMessage("#campaign-editor-success", "Re-planned with " + plannerLabel(agentCode) + ". The campaign is back in review if it was approved.");
      })
      .fail(handleEditorFailure)
      .always(function () { setEditorBusy(null); });
  }

  function saveCampaignEditor(event) {
    event.preventDefault();
    clearEditorMessages();
    if (editor.mode === "edit") saveCampaignEdit(); else createPlannedDraft();
  }

  // Label/value grid; rows with an empty value are skipped. All values go through .text().
  function appendFacts($parent, rows) {
    var $dl = $("<dl>").addClass("mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2");
    rows.forEach(function (row) {
      var value = row[1];
      if (Array.isArray(value)) value = value.join(", ");
      else if (value && typeof value === "object") value = JSON.stringify(value);
      if (value == null || value === "") return;
      $("<div>").addClass("rounded-lg border border-slate-200 bg-slate-50/50 p-3").append(
        $("<dt>").addClass("text-[11px] font-semibold uppercase tracking-wider text-slate-500").text(row[0]),
        $("<dd>").addClass("mt-1 break-words text-sm text-slate-800").text(String(value))
      ).appendTo($dl);
    });
    if ($dl.children().length) $dl.appendTo($parent);
  }

  function renderStrategy(campaign) {
    $("#campaign-details-strategy").text(campaign.strategy_summary || "No strategy summary is available.");
    var plan = campaign.ai_plan || {};
    var $plan = $("#campaign-details-plan").empty();
    if (Object.keys(plan).length) {
      $("<h3>").addClass("text-sm font-bold text-slate-900").text("AI plan").appendTo($plan);
      if (Array.isArray(plan.action_plan) && plan.action_plan.length) {
        var $steps = $("<ol>").addClass("mt-2 list-decimal space-y-1 pl-5 text-sm text-slate-700");
        plan.action_plan.forEach(function (step) { $("<li>").text(typeof step === "string" ? step : JSON.stringify(step)).appendTo($steps); });
        $steps.appendTo($plan);
      }
      appendFacts($plan, [
        ["Planned start", plan.start_date], ["Planned end", plan.end_date],
        ["Chosen content items", plan.content_item_ids], ["Chosen template", plan.template_id],
        ["Template data", plan.template_data]
      ]);
    }
    var prov = (campaign.metadata_ || {}).agent_provenance || {};
    var $prov = $("#campaign-details-provenance").empty();
    if (Object.keys(prov).length) {
      $("<h3>").addClass("text-sm font-bold text-slate-900").text("Planner agent").appendTo($prov);
      appendFacts($prov, [
        ["Agent", [prov.display_name, prov.agent_code && "(" + prov.agent_code + ")"].filter(Boolean).join(" ")],
        ["Model", prov.model_name], ["Status at run", prov.status],
        ["Prompt", [prov.prompt_key, prov.instruction_version != null && "v" + prov.instruction_version].filter(Boolean).join(" ")],
        ["Required variables", prov.required_variables], ["Model settings", prov.hyperparameters],
        ["Run at", prov.run_at ? fmt.dateTime(prov.run_at) : ""]
      ]);
      var body = (prov.resolved_prompt_version || {}).body;
      if (body) {
        var $details = $("<details>").addClass("mt-3 rounded-lg border border-slate-200 p-3 text-xs");
        $("<summary>").addClass("cursor-pointer font-semibold text-slate-700").text("Prompt that ran").appendTo($details);
        $("<pre>").addClass("mt-2 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-slate-700").text(body).appendTo($details);
        $details.appendTo($prov);
      }
    }
  }

  function renderDetailMetadata(campaign) {
    var fields = [
      { label: "Campaign code", value: campaign.campaign_code, icon: "bi-hash", technical: true },
      { label: "Owner", value: campaign.user_id || "System / unassigned", icon: "bi-person" },
      { label: "Channel", value: campaign.channel, icon: "bi-broadcast" },
      { label: "Platform", value: campaign.platform, icon: "bi-grid-1x2" },
      { label: "Language", value: campaign.lang, icon: "bi-translate" },
      { label: "Schedule", value: [campaign.start_date, campaign.end_date].filter(Boolean).join(" - ") || "Not scheduled", icon: "bi-calendar3" },
      { label: "Budget", value: campaign.budget_amount == null ? "No budget set" : String(campaign.budget_amount) + " " + (campaign.currency || ""), icon: "bi-wallet2" },
      { label: "Objective", value: campaign.objective, icon: "bi-bullseye", wide: true },
      { label: "Description", value: campaign.description, icon: "bi-card-text", wide: true },
      { label: "Keywords", value: (campaign.keywords || []).join(", "), icon: "bi-tags", wide: true },
      { label: "Template reference", value: campaign.template_id, icon: "bi-file-earmark-text", technical: true },
      { label: "Created", value: fmt.dateTime(campaign.created_at), icon: "bi-clock-history" },
      { label: "Updated", value: fmt.dateTime(campaign.updated_at), icon: "bi-arrow-repeat" }
    ];
    var $metadata = $("#campaign-details-metadata").empty();
    fields.forEach(function (field) {
      var $item = $("<div>").addClass("rounded-xl border border-slate-200 bg-slate-50/50 p-4 transition hover:border-slate-300 hover:bg-white");
      if (field.wide) $item.addClass("sm:col-span-2 xl:col-span-3");
      var $heading = $("<div>").addClass("flex items-center gap-2");
      $("<i>").addClass("bi " + field.icon + " text-slate-400").attr("aria-hidden", "true").appendTo($heading);
      $("<dt>").addClass("text-[11px] font-semibold uppercase tracking-wider text-slate-500").text(field.label).appendTo($heading);
      $heading.appendTo($item);
      $("<dd>").addClass("mt-2 break-words text-sm font-semibold text-slate-800 " + (field.technical ? "font-mono text-xs" : "")).text(field.value || "-").appendTo($item);
      $metadata.append($item);
    });
    renderStrategy(campaign);
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

  // Short display of one audited value; full snapshots are too long to read in a timeline.
  function historyValue(value) {
    if (value == null || value === "") return "—";
    if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
    if (typeof value === "object") return value.segment_name || value.name || JSON.stringify(value);
    var text = String(value);
    return text.length > 90 ? text.slice(0, 87) + "…" : text;
  }

  // Audit rows store whole before/after snapshots; show only the fields that changed.
  function historyLines(entry) {
    if (entry.type === "review") return ["Reviewer: " + historyValue(entry.reviewer_id)].concat(entry.reason ? ["Reason: " + entry.reason] : []);
    var before = entry.before_data || {};
    var after = entry.after_data || {};
    if (entry.action === "CREATE") {
      var lines = ["Planned by " + (after.agent_code || "planner") + (after.instruction_version ? " v" + after.instruction_version : "")];
      if (after.start_date || after.end_date) lines.push("Schedule: " + historyValue(after.start_date) + " → " + historyValue(after.end_date));
      return lines;
    }
    return Object.keys(after).filter(function (key) {
      return JSON.stringify(before[key]) !== JSON.stringify(after[key]);
    }).map(function (key) {
      if (key === "ai_plan") return "AI plan: re-planned";
      return key.replace(/_/g, " ") + ": " + historyValue(before[key]) + " → " + historyValue(after[key]);
    });
  }

  function renderDetailHistory(history) {
    var $history = $("#campaign-details-history").empty();
    (history || []).forEach(function (entry) {
      var label = entry.type === "review" ? String(entry.decision || "review") : String(entry.action || "audit");
      var lines = historyLines(entry);
      var $lines = $("<ul>").addClass("mt-1 space-y-0.5 text-xs text-slate-600");
      (lines.length ? lines : ["No field changes"]).forEach(function (line) { $("<li>").text(line).appendTo($lines); });
      $("<li>").addClass("border-l-2 border-indigo-200 pl-4 text-sm text-slate-700").append($("<div>").addClass("font-semibold text-slate-900").text(label), $lines, $("<time>").addClass("mt-1 block text-xs text-slate-400").text(entry.created_at ? new Date(entry.created_at).toLocaleString() : "")).appendTo($history);
    });
    if (!$history.children().length) $("<li>").addClass("text-sm text-slate-500").text("No history recorded.").appendTo($history);
  }

  function ratio(value, suffix) {
    return value == null ? "—" : (parseFloat(value) || 0).toFixed(2) + suffix;
  }

  // One row of KPI cards for a Totals object; CPA/ROAS are null server-side when undefined.
  function renderTotalsCards(title, totals) {
    var $block = $("<div>");
    $("<h3>").addClass("mb-2 text-xs font-bold uppercase tracking-wider text-slate-500").text(title).appendTo($block);
    var $grid = $("<div>").addClass("grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8").appendTo($block);
    [
      ["Spend", fmtVnd(totals.spend)], ["Impressions", fmt.int(totals.impressions || 0)], ["Clicks", fmt.int(totals.clicks || 0)],
      ["Conversions", fmt.int(totals.conversions || 0)], ["CTR", ratio(totals.ctr_percentage, "%")], ["CVR", ratio(totals.cvr_percentage, "%")],
      ["CPA", totals.cpa == null ? "CPA unavailable — no conversions" : fmtVnd(totals.cpa)], ["ROAS", ratio(totals.roas, "×")]
    ].forEach(function (card) {
      $("<article>").addClass("rounded-lg border border-slate-200 bg-slate-50/50 p-3").append(
        $("<p>").addClass("text-[11px] uppercase tracking-wider text-slate-500").text(card[0]),
        $("<p>").addClass("mt-1 text-sm font-bold text-slate-900").text(card[1])
      ).appendTo($grid);
    });
    return $block;
  }

  function renderCampaignReport(report) {
    var lifetime = report.lifetime || {}, period = report.period_totals || {}, coverage = report.coverage || {};
    $("#campaign-report-spend").text(fmtVnd(lifetime.spend));
    $("#campaign-report-impressions").text(fmt.int(lifetime.impressions || 0));
    $("#campaign-report-conversions").text(fmt.int(lifetime.conversions || 0));
    $("#campaign-report-roas").text(ratio(lifetime.roas, "x"));
    $("#campaign-report-kpis").empty().append(renderTotalsCards("Lifetime", lifetime), renderTotalsCards("Selected period", period));
    var reportWindow = coverage.start_date || coverage.end_date
      ? (coverage.start_date || "the start") + " to " + (coverage.end_date || "today")
      : "all dates";
    $("#campaign-report-coverage").text(coverage.days_with_data
      ? "Period " + reportWindow + " · " + coverage.days_with_data + " days with data (first " + coverage.first_report_date + ", last " + coverage.last_report_date + ")"
      : "No daily report data for " + reportWindow + ".");
    var $daily = $("#campaign-report-daily").empty();
    (report.daily || []).forEach(function (item) {
      $("<tr>").append($("<td>").addClass("py-3 pr-4 text-slate-700").text(item.report_date || "-"), $("<td>").addClass("py-3 pr-4").text(fmtVnd(item.spend)), $("<td>").addClass("py-3 pr-4").text(fmt.int(item.impressions || 0)), $("<td>").addClass("py-3 pr-4").text(fmt.int(item.clicks || 0)), $("<td>").addClass("py-3 pr-4").text(fmt.int(item.conversions || 0)), $("<td>").addClass("py-3").text(fmtVnd(item.revenue))).appendTo($daily);
    });
    if (!$daily.children().length) $("<tr>").append($("<td>").attr("colspan", 6).addClass("py-6 text-center text-slate-500").text("No daily data for this period.")).appendTo($daily);
    var warn = lifetime.zero_conversion_warning || period.zero_conversion_warning;
    $("#campaign-report-warning").toggleClass("hidden", !warn).text(warn ? "No conversions are recorded. CPA must not be interpreted as positive performance." : "");
  }

  function loadCampaignReport(campaign) {
    var params = {};
    if ($("#campaign-report-start").val()) params.start_date = $("#campaign-report-start").val();
    if ($("#campaign-report-end").val()) params.end_date = $("#campaign-report-end").val();
    api("/campaigns/" + encodeURIComponent(campaign.campaign_id) + "/report", params)
      .done(renderCampaignReport)
      .fail(function (xhr) { setCampaignMessage("#campaign-details-error", campaignError(xhr)); });
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

  function loadCampaignDetails(campaignId) {
    setCampaignMessage("#campaign-details-error", "");
    $("#campaign-details-loading").removeClass("hidden");
    $("#campaign-details-content").addClass("hidden");
    $.when(api("/campaigns/" + encodeURIComponent(campaignId)), api("/campaigns/" + encodeURIComponent(campaignId) + "/history"))
      .done(function (campaignResult, historyResult) {
        var campaign = campaignResult[0];
        renderDetailMetadata(campaign);
        loadDetailAudience(campaign.segment_id);
        renderDetailHistory(historyResult[0]);
        $("#campaign-details-title").text(campaign.name || "Campaign details");
        $("#campaign-details-subtitle").text([campaign.campaign_code, campaign.channel, campaign.platform].filter(Boolean).join(" / "));
        var statusPresentation = campaignStatusPresentation(campaign.status);
        $("#campaign-details-status").attr("class", "inline-flex items-center gap-1.5 text-xs font-semibold rounded-md px-2.5 py-1.5 " + statusPresentation.classes).attr("aria-label", "Campaign status: " + statusPresentation.label);
        $("#campaign-details-status-icon").attr("class", "bi " + statusPresentation.icon);
        $("#campaign-details-status-label").text(statusPresentation.label);
        $("#campaign-details-approval").text(campaign.approval_status || "Draft").attr("class", "text-xs font-semibold rounded-md px-2.5 py-1.5 " + (APPROVAL_BADGE[campaign.approval_status] || "bg-indigo-50 text-indigo-700"));
        var canReview = canReviewCampaign() && campaign.approval_status === "InReview";
        $("#btn-campaign-details-replan").toggleClass("hidden", !canReviewCampaign());
        $("#btn-campaign-details-approve").toggleClass("hidden", !canReview);
        $("#btn-campaign-details-reject").toggleClass("hidden", !canReview);
        $("#campaign-details-loading").addClass("hidden");
        $("#campaign-details-content").removeClass("hidden");
        $("#campaign-experiment-form").attr("data-campaign-id", campaignId);
        $("#btn-campaign-details-edit").off("click.c360campaign").on("click.c360campaign", function () { C360.router.navigate("/campaigns/" + encodeURIComponent(campaignId) + "/edit"); });
        $("#btn-campaign-details-replan").off("click.c360campaign").on("click.c360campaign", function () { editor.scrollToReplan = true; C360.router.navigate("/campaigns/" + encodeURIComponent(campaignId) + "/edit"); });
        $("#btn-campaign-report-refresh").off("click.c360campaign").on("click.c360campaign", function () { loadCampaignReport(campaign); });
        $("#btn-campaign-details-approve").off("click.c360campaign").on("click.c360campaign", function () { api("/campaigns/" + encodeURIComponent(campaignId) + "/approve", {}, "POST").done(function () { loadCampaignDetails(campaignId); }).fail(function (xhr) { setCampaignMessage("#campaign-details-error", campaignError(xhr)); }); });
        $("#btn-campaign-details-reject").off("click.c360campaign").on("click.c360campaign", function () { var reason = window.prompt("Reason for rejection:"); if (reason === null) return; api("/campaigns/" + encodeURIComponent(campaignId) + "/reject", { reason: reason }, "POST").done(function () { loadCampaignDetails(campaignId); }).fail(function (xhr) { setCampaignMessage("#campaign-details-error", campaignError(xhr)); }); });
        loadCampaignReport(campaign);
        loadExperimentSegments(function () { resetExperimentForm(); loadCampaignExperiments(campaignId); });
      })
      .fail(function (xhr) { $("#campaign-details-loading").addClass("hidden"); setCampaignMessage("#campaign-details-error", campaignError(xhr)); });
  }

  function activateCampaignDetailsTab(panel) {
    $(".campaign-details-tab").each(function () {
      var selected = $(this).data("panel") === panel;
      $(this)
        .attr("aria-selected", selected ? "true" : "false")
        .toggleClass("border-indigo-600 text-indigo-700", selected)
        .toggleClass("border-transparent text-slate-500", !selected);
    });
    $(".campaign-details-panel").addClass("hidden");
    $("#campaign-details-panel-" + panel).removeClass("hidden");
  }

  function bindWorkspaceEvents() {
    var $doc = $(document);
    $doc.off(".c360campaignworkspace");
    $doc.on("click.c360campaignworkspace", "#btn-campaign-create", function () { C360.router.navigate("/campaigns/new/edit"); });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-editor-back, #btn-campaign-editor-cancel, #btn-campaign-details-back", function () { C360.router.navigate("/campaigns"); });
    $doc.on("submit.c360campaignworkspace", "#campaign-editor-form", saveCampaignEditor);
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
    $doc.on("change.c360campaignworkspace", "#campaign-field-draft-channel", function () {
      var channel = $(this).val();
      $("#campaign-template-wrap").toggleClass("hidden", channel === ZNS_CHANNEL);
      if (channel !== ZNS_CHANNEL) loadTemplateOptions("email", null);
    });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-replan", replanCampaign);
    $doc.on("click.c360campaignworkspace", "#btn-campaign-editor-reload", function () { if (editor.stale) { clearEditorMessages(); fillCampaignEditor(editor.stale); } });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-select-segment, #btn-campaign-change-segment", openSegmentPicker);
    $doc.on("click.c360campaignworkspace", "#btn-campaign-segment-modal-close, #btn-campaign-segment-modal-cancel", closeSegmentPicker);
    $doc.on("click.c360campaignworkspace", "#campaign-segment-modal", function (event) { if (event.target === this) closeSegmentPicker(); });
    $doc.on("click.c360campaignworkspace", "#btn-campaign-clear-segment", function () { renderSegmentSelection(null); });
    $doc.on("input.c360campaignworkspace", "#campaign-segment-search", function () { renderSegmentResults($(this).val()); });
    $doc.on("click.c360campaignworkspace", ".campaign-segment-option", function () {
      var segment = segmentPickerState.byId[$(this).data("segment-id")];
      if (!segment) return;
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

  C360.campaignView = { load: loadAll };

})(window.C360);

