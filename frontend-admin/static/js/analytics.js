/* Customer 360 Admin -- Analytics dashboard.
 *
 * Shows Chart.js time-series of event activity and a CSS heatmap matrix of
 * raw profile distribution (source system × domain). Data is fetched live
 * from customer360-api: /events for the time-series and /reporting/summary
 * for the profile heatmap.
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var charts = {};
  var campaignHeatmap = null;
  var PALETTE = ["#6366f1", "#22c55e", "#3b82f6", "#f97316", "#ef4444", "#a855f7", "#14b8a6", "#eab308", "#64748b"];

  function renderChart(id, config) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    var el = document.getElementById(id);
    if (!el) return;
    charts[id] = new Chart(el.getContext("2d"), config);
  }

  function destroyCharts() {
    Object.keys(charts).forEach(function (id) {
      if (charts[id]) { charts[id].destroy(); }
      delete charts[id];
    });
    if (campaignHeatmap) {
      campaignHeatmap.destroy();
      campaignHeatmap = null;
    }
  }

  function utcDateString(date) {
    return date.toISOString().split("T")[0];
  }

  function eventTimeFrom(days) {
    var cutoff = new Date(Date.now() - days * 86400000);
    return cutoff.toISOString();
  }

  function aggregateEvents(events, days) {
    var now = new Date();
    var today = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    var labels = [];
    for (var i = days - 1; i >= 0; i--) {
      var d = new Date(today);
      d.setUTCDate(today.getUTCDate() - i);
      labels.push(utcDateString(d));
    }

    var countsByDay = {};
    var channels = {};
    var conversions = 0;
    var activeProfiles = {};

    events.forEach(function (e) {
      var day = e.event_time ? e.event_time.substring(0, 10) : "";
      countsByDay[day] = (countsByDay[day] || 0) + 1;

      var channel = e.channel || "unknown";
      channels[channel] = (channels[channel] || 0) + 1;

      if (e.is_conversion) conversions += 1;
      if (e.master_profile_id) activeProfiles[e.master_profile_id] = true;
    });

    return {
      labels: labels,
      counts: labels.map(function (d) { return countsByDay[d] || 0; }),
      channels: channels,
      conversions: conversions,
      activeProfileCount: Object.keys(activeProfiles).length
    };
  }

  function renderTimeSeries(labels, counts) {
    renderChart("chart-events-time-series", {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: "Events",
          data: counts,
          borderColor: PALETTE[0],
          backgroundColor: "rgba(99, 102, 241, 0.12)",
          fill: true,
          tension: 0.3,
          pointRadius: 2,
          pointHoverRadius: 5
        }]
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { maxTicksLimit: 10 } },
          y: { beginAtZero: true }
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) { return fmt.int(ctx.parsed.y) + " events"; } } }
        }
      }
    });
  }

  function renderChannelChart(channels) {
    var labels = Object.keys(channels);
    var data = labels.map(function (k) { return channels[k]; });

    renderChart("chart-events-channel", {
      type: "bar",
      data: {
        labels: labels.map(function (k) { return fmt.titleCase(k); }),
        datasets: [{
          label: "Events",
          data: data,
          backgroundColor: PALETTE
        }]
      },
      options: {
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  function heatmapColor(value, max) {
    var t = max ? value / max : 0;
    var opacity = 0.12 + 0.83 * t;
    return "rgba(99, 102, 241, " + opacity.toFixed(2) + ")";
  }

  function buildProfileHeatmap(rows) {
    var $container = $("#profile-heatmap").empty();
    $("#profile-heatmap-empty").addClass("hidden");
    if (!rows || !rows.length) {
      $("#profile-heatmap-empty").removeClass("hidden");
      return;
    }

    var domains = [];
    var bySource = {};
    rows.forEach(function (r) {
      if (domains.indexOf(r.domain) === -1) domains.push(r.domain);
      bySource[r.source_system] = bySource[r.source_system] || {};
      bySource[r.source_system][r.domain] = (bySource[r.source_system][r.domain] || 0) + r.count;
    });
    domains.sort();
    var sources = Object.keys(bySource).sort();

    var maxCount = 0;
    sources.forEach(function (s) {
      domains.forEach(function (d) {
        var c = bySource[s][d] || 0;
        if (c > maxCount) maxCount = c;
      });
    });

    var $grid = $("<div></div>").addClass("grid gap-1").css({
      gridTemplateColumns: "160px repeat(" + domains.length + ", minmax(90px, 1fr))"
    });

    $grid.append($("<div></div>")
      .addClass("sticky left-0 top-0 z-20 bg-slate-50 p-2 text-xs font-semibold text-slate-500 border-b border-r border-slate-200 rounded-tl")
      .text("Source System"));

    domains.forEach(function (d, idx) {
      var cell = $("<div></div>")
        .addClass("sticky top-0 z-10 bg-slate-50 p-2 text-xs font-semibold text-slate-500 text-center border-b border-slate-200")
        .text(fmt.domainLabel(d));
      if (idx === domains.length - 1) cell.addClass("rounded-tr");
      $grid.append(cell);
    });

    sources.forEach(function (s, rowIdx) {
      var firstCell = $("<div></div>")
        .addClass("sticky left-0 z-10 bg-white p-2 text-xs font-medium text-slate-700 border-r border-slate-200 truncate")
        .attr("title", s)
        .text(s);
      if (rowIdx === sources.length - 1) firstCell.addClass("rounded-bl");
      $grid.append(firstCell);

      domains.forEach(function (d, colIdx) {
        var count = bySource[s][d] || 0;
        var cell = $("<div></div>")
          .addClass("p-2 text-xs text-center rounded")
          .text(fmt.int(count));
        if (count > 0) {
          cell.css("background-color", heatmapColor(count, maxCount));
        } else {
          cell.addClass("bg-slate-50 text-slate-300");
        }
        if (rowIdx === sources.length - 1 && colIdx === domains.length - 1) cell.addClass("rounded-br");
        $grid.append(cell);
      });
    });

    $container.append($grid);
  }

  function generateMockData(days, maxCount) {
    var data = [];
    var endDate = new Date();
    var startDate = new Date(endDate);
    startDate.setDate(startDate.getDate() - days);

    for (var d = new Date(startDate); d <= endDate; d.setDate(d.getDate() + 1)) {
      data.push({
        date: d.toISOString().split("T")[0],
        count: Math.floor(Math.random() * maxCount)
      });
    }
    return data;
  }

  function renderCampaignHeatmap() {
    if (campaignHeatmap) {
      campaignHeatmap.destroy();
      campaignHeatmap = null;
    }

    var canvas = document.getElementById("campaign-events-chart");
    if (!canvas) return;

    campaignHeatmap = new MatrixHeatmap({
      canvasId: "campaign-events-chart",
      entityName: "campaign triggers",
      data: generateMockData(365, 8),
      colorTheme: [
        { min: 0, color: "#ebedf0" },
        { min: 1, color: "#cbe2f9" },
        { min: 3, color: "#79b8ff" },
        { min: 5, color: "#2188ff" },
        { min: 7, color: "#0366d6" }
      ]
    });
  }

  function updateKpis(events, summary, agg) {
    $("#kpi-total-events").text(fmt.int(events.length));
    $("#kpi-active-profiles").text(fmt.int(agg.activeProfileCount));
    $("#kpi-conversions").text(fmt.int(agg.conversions));
    $("#kpi-master-profiles").text(fmt.int(summary.total_master_profiles || 0));
  }

  function load() {
    var current = C360.router.current();
    if (!current || current.route.tab !== "analytics") return;

    var days = C360.config.getDataPeriodDays();

    $("#analytics-loading").removeClass("hidden");
    $("#analytics-dashboard").addClass("hidden");
    $("#analytics-empty").addClass("hidden");
    $("#events-time-series-note").addClass("hidden");
    destroyCharts();

    $.when(
      api("/events", { event_time_from: eventTimeFrom(days), limit: 1000 }),
      api("/reporting/summary", { days: days })
    ).done(function (eventsRes, summaryRes) {
      var events = eventsRes[0] || [];
      var summary = summaryRes[0] || {};

      $("#analytics-loading").addClass("hidden");

      var hasEvents = events.length > 0;
      var hasHeatmap = summary.raw_profiles_by_source_system && summary.raw_profiles_by_source_system.length > 0;

      if (!hasEvents && !hasHeatmap) {
        $("#analytics-empty").removeClass("hidden");
        return;
      }

      $("#analytics-dashboard").removeClass("hidden");

      var agg = aggregateEvents(events, days);
      updateKpis(events, summary, agg);

      if (hasEvents) {
        renderTimeSeries(agg.labels, agg.counts);
        renderChannelChart(agg.channels);
        $("#events-time-series-note").toggleClass("hidden", events.length < 1000);
      } else {
        renderTimeSeries([], []);
        renderChannelChart({});
      }

      buildProfileHeatmap(summary.raw_profiles_by_source_system || []);
      renderCampaignHeatmap();
    }).fail(function (xhr) {
      $("#analytics-loading").addClass("hidden");
      showApiError("loading analytics data", xhr);
    });
  }

  function bindEvents() {
    $(document).on("change", "#data-period-select", load);
    $(document).on("click", "#btn-analytics-refresh", load);
  }

  bindEvents();

  C360.router.define("/analytics", {
    section: "view-analytics",
    tab: "analytics",
    mount: function () {
      $("#analytics-content").html(C360.templates.html("analytics"));
      load();
    }
  });

  C360.analyticsView = { load: load };
})(window.C360);
