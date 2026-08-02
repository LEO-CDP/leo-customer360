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
    Zalo: "💬", AppsFlyer: "📱", YouTube: "▶️"
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
      onRowClick: function (id) { console.log("Campaign clicked:", id); },
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
      loadAll();
    }
  });

  C360.campaignView = { load: loadAll };

})(window.C360);

