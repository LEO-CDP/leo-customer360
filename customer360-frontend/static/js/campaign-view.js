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
    var roles = C360.config.currentUser().roles || [];
    var reviewerRoles = ["platform_admin", "super_admin", "system_admin", "tenant_admin", "admin"];
    return roles.some(function (role) { return reviewerRoles.indexOf(String(role).toLowerCase()) !== -1; });
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

  function campaignFormPayload(includeTenant) {
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
      template_id: fieldValue("#campaign-field-template").trim() || null,
      objective: fieldValue("#campaign-field-objective").trim() || null
    };
    if (includeTenant) payload.tenant_id = tenantId();
    return payload;
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
    $("#campaign-field-template").val(campaign.template_id || "").prop("disabled", !!campaign.campaign_id);
    $("#campaign-field-objective").val(campaign.objective || "");
    $("#campaign-field-strategy").val(campaign.strategy_summary || "");
    loadSelectedSegment(campaign.segment_id, false, !campaign.campaign_id);
  }

  function loadCampaignEditor(campaignId) {
    setCampaignMessage("#campaign-editor-error", "");
    setCampaignMessage("#campaign-editor-success", "");
    $("#campaign-editor-loading").toggleClass("hidden", !campaignId);
    $("#campaign-editor-form").toggleClass("hidden", !!campaignId);
    if (!campaignId) {
      fillCampaignEditor({});
      renderSegmentSelection(null);
      return;
    }
    api("/campaigns/" + encodeURIComponent(campaignId))
      .done(function (campaign) {
        $("#campaign-editor-loading").addClass("hidden");
        $("#campaign-editor-form").removeClass("hidden");
        fillCampaignEditor(campaign);
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
    var payload = campaignFormPayload(!campaignId);
    if (!payload.name) {
      setCampaignMessage("#campaign-editor-error", "Campaign name is required.");
      return;
    }
    if (!campaignId && !payload.segment_id) {
      setCampaignMessage("#campaign-editor-error", "Choose an active audience segment before creating the campaign.");
      return;
    }
    if (payload.start_date && payload.end_date && payload.end_date < payload.start_date) {
      setCampaignMessage("#campaign-editor-error", "End date cannot be before start date.");
      return;
    }
    var request;
    if (!campaignId) {
      request = api("/campaigns/", payload, "POST");
    } else {
      var general = $.extend({}, payload);
      delete general.tenant_id;
      delete general.segment_id;
      delete general.template_id;
      delete general.objective;
      request = api("/campaigns/" + encodeURIComponent(campaignId) + "/draft", {
        segment_id: payload.segment_id || null,
        objective: payload.objective,
        strategy_summary: fieldValue("#campaign-field-strategy").trim() || null,
        start_date: payload.start_date,
        end_date: payload.end_date
      }, "PATCH").then(function () {
        return api("/campaigns/" + encodeURIComponent(campaignId), general, "PATCH");
      });
    }
    $("#btn-campaign-editor-save").prop("disabled", true).text("Saving...");
    request.done(function (campaign) {
      var id = campaign.campaign_id || campaignId;
      C360.router.navigate("/campaigns/" + encodeURIComponent(id));
    }).fail(function (xhr) {
      setCampaignMessage("#campaign-editor-error", campaignError(xhr));
    }).always(function () {
      $("#btn-campaign-editor-save").prop("disabled", false).text("Save campaign");
    });
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
    var $history = $("#campaign-details-history").empty();
    (history || []).forEach(function (entry) {
      var label = entry.type === "review" ? String(entry.decision || "review") : String(entry.action || "audit");
      var detail = entry.reason || entry.after_data || entry.before_data || "";
      $("<li>").addClass("border-l-2 border-indigo-200 pl-4 text-sm text-slate-700").append($("<div>").addClass("font-semibold text-slate-900").text(label), $("<div>").addClass("mt-1 whitespace-pre-wrap text-xs text-slate-500").text(typeof detail === "string" ? detail : JSON.stringify(detail)), $("<time>").addClass("mt-1 block text-xs text-slate-400").text(entry.created_at || "")).appendTo($history);
    });
    if (!$history.children().length) $("<li>").addClass("text-sm text-slate-500").text("No history recorded.").appendTo($history);
  }

  function renderCampaignReport(campaign, response, isAggregateFallback) {
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
    if (!$daily.children().length && row.campaign_id) {
      $("<tr>").append($("<td>").addClass("py-3 pr-4 text-slate-700").text("Lifetime aggregate"), $("<td>").addClass("py-3 pr-4").text(fmtVnd(row.total_spend)), $("<td>").addClass("py-3 pr-4").text(fmt.int(row.total_impressions || 0)), $("<td>").addClass("py-3 pr-4").text(fmt.int(row.total_clicks || 0)), $("<td>").addClass("py-3 pr-4").text(fmt.int(row.total_conversions || 0)), $("<td>").addClass("py-3").text(fmtVnd(row.total_revenue))).appendTo($daily);
    }
    var warning = row.campaign_id && !row.total_conversions ? "No conversions are recorded. CPA must not be interpreted as positive performance." : "";
    if (isAggregateFallback && ($("#campaign-report-start").val() || $("#campaign-report-end").val())) warning = "Daily date filtering is unavailable until the single-campaign report API is enabled. Showing lifetime aggregate data.";
    $("#campaign-report-warning").toggleClass("hidden", !warning).text(warning);
  }

  function loadCampaignReport(campaign) {
    var params = {};
    if ($("#campaign-report-start").val()) params.start_date = $("#campaign-report-start").val();
    if ($("#campaign-report-end").val()) params.end_date = $("#campaign-report-end").val();
    api("/campaigns/" + encodeURIComponent(campaign.campaign_id) + "/report", params).done(function (response) {
      renderCampaignReport(campaign, response, false);
    }).fail(function (xhr) {
      if (!xhr || xhr.status !== 404) {
        setCampaignMessage("#campaign-details-error", campaignError(xhr));
        return;
      }
      var fallbackParams = { tenant_id: tenantId(), search: campaign.campaign_code || campaign.name, page: 1, page_size: 100 };
      api("/campaigns/analytics", fallbackParams).done(function (response) {
        renderCampaignReport(campaign, response, true);
      }).fail(function (fallbackXhr) { setCampaignMessage("#campaign-details-error", campaignError(fallbackXhr)); });
    });
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
        $("#campaign-details-approval").text(campaign.approval_status || "Draft").attr("class", "text-xs font-semibold rounded-md px-2.5 py-1.5 bg-indigo-50 text-indigo-700");
        var canReview = canReviewCampaign() && campaign.approval_status === "InReview";
        $("#btn-campaign-details-approve").toggleClass("hidden", !canReview);
        $("#btn-campaign-details-reject").toggleClass("hidden", !canReview);
        $("#campaign-details-loading").addClass("hidden");
        $("#campaign-details-content").removeClass("hidden");
        $("#campaign-experiment-form").attr("data-campaign-id", campaignId);
        $("#btn-campaign-details-edit").off("click.c360campaign").on("click.c360campaign", function () { C360.router.navigate("/campaigns/" + encodeURIComponent(campaignId) + "/edit"); });
        $("#btn-campaign-report-refresh").off("click.c360campaign").on("click.c360campaign", function () { loadCampaignReport(campaign); });
        $("#btn-campaign-details-approve").off("click.c360campaign").on("click.c360campaign", function () { api("/campaigns/" + encodeURIComponent(campaignId) + "/approve", {}, "POST").done(function () { loadCampaignDetails(campaignId); }).fail(function (xhr) { setCampaignMessage("#campaign-details-error", campaignError(xhr)); }); });
        $("#btn-campaign-details-reject").off("click.c360campaign").on("click.c360campaign", function () { var reason = window.prompt("Reason for rejection:"); if (reason === null) return; api("/campaigns/" + encodeURIComponent(campaignId) + "/reject", { reason: reason }, "POST").done(function () { loadCampaignDetails(campaignId); }).fail(function (xhr) { setCampaignMessage("#campaign-details-error", campaignError(xhr)); }); });
        loadCampaignReport(campaign);
        loadExperimentSegments(function () { resetExperimentForm(); loadCampaignExperiments(campaignId); });
      })
      .fail(function (xhr) { $("#campaign-details-loading").addClass("hidden"); setCampaignMessage("#campaign-details-error", campaignError(xhr)); });
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
    $doc.on("click.c360campaignworkspace", ".campaign-details-tab", function () { var panel = $(this).data("panel"); $(".campaign-details-tab").removeClass("border-indigo-600 text-indigo-700").addClass("border-transparent text-slate-500"); $(this).removeClass("border-transparent text-slate-500").addClass("border-indigo-600 text-indigo-700"); $(".campaign-details-panel").addClass("hidden"); $("#campaign-details-panel-" + panel).removeClass("hidden"); });
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

