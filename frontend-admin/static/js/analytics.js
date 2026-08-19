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
  var eventHeatmap = null;
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
    if (eventHeatmap) {
      eventHeatmap.destroy();
      eventHeatmap = null;
    }
  }

  function utcDateString(date) {
    return date.toISOString().split("T")[0];
  }

  function eventTimeFrom(days) {
    var cutoff = new Date(Date.now() - days * 86400000);
    return cutoff.toISOString();
  }

  function periodParams() {
    var days = C360.config.getDataPeriodDays("#analytics-period-select");
    return { days: days };
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

  function buildEventHeatmapSeries(events, days) {
    var countsByDate = {};
    var heatmapDays = Math.max(1, Number(days) || 365);

    (events || []).forEach(function (event) {
      var eventDate = event && event.event_time ? event.event_time.substring(0, 10) : "";
      if (!eventDate) return;
      countsByDate[eventDate] = (countsByDate[eventDate] || 0) + 1;
    });

    var endDate = new Date();
    endDate.setUTCHours(0, 0, 0, 0);
    var startDate = new Date(endDate);
    startDate.setUTCDate(endDate.getUTCDate() - (heatmapDays - 1));

    var data = [];
    for (var d = new Date(startDate); d <= endDate; d.setUTCDate(d.getUTCDate() + 1)) {
      var isoDate = d.toISOString().split("T")[0];
      data.push({
        date: isoDate,
        count: countsByDate[isoDate] || 0
      });
    }
    return data;
  }

  // Builds color thresholds from the quantiles of the actual active-day
  // distribution (not fractions of the single peak day). A max-based scale
  // washes out contrast on high-volume tenants: one 5,000-event spike day
  // pushes every normal 50-200 event day into the lightest bucket. Quantiles
  // of the real active days keep contrast meaningful regardless of scale.
  function buildHeatmapColorTheme(series) {
    var palette = ["#ebedf0", "#cbe2f9", "#79b8ff", "#2188ff", "#0366d6"];
    var activeCounts = (series || [])
      .map(function (d) { return d.count; })
      .filter(function (c) { return c > 0; })
      .sort(function (a, b) { return a - b; });

    if (!activeCounts.length) return [{ min: 0, color: palette[0] }];

    function quantile(p) {
      var idx = Math.min(activeCounts.length - 1, Math.floor(p * activeCounts.length));
      return activeCounts[idx];
    }

    var quantiles = [0.25, 0.5, 0.75, 0.92];
    var theme = [{ min: 0, color: palette[0] }];
    var lastMin = 0;
    quantiles.forEach(function (p, idx) {
      var min = Math.max(lastMin + 1, quantile(p));
      if (min <= lastMin) return;
      theme.push({ min: min, color: palette[idx + 1] });
      lastMin = min;
    });
    return theme;
  }

  function renderHeatmapLegend(theme) {
    var $legend = $("#event-activity-heatmap-legend").empty();
    $legend.append($("<span></span>").text("Less"));
    theme.forEach(function (stop) {
      $legend.append($("<span></span>")
        .addClass("inline-block w-3 h-3 rounded-sm border border-slate-200/60")
        .css("background-color", stop.color)
        .attr("title", stop.min + "+ events/day"));
    });
    $legend.append($("<span></span>").text("More"));
  }

  function renderHeatmapSummary(series, days) {
    var totalEvents = series.reduce(function (sum, d) { return sum + d.count; }, 0);
    var activeDays = series.filter(function (d) { return d.count > 0; }).length;
    var peak = series.reduce(function (max, d) { return Math.max(max, d.count); }, 0);
    $("#event-activity-heatmap-summary").text(
      fmt.int(totalEvents) + " events across " + fmt.int(activeDays) + " active day" + (activeDays === 1 ? "" : "s") +
      " (last " + fmt.int(days) + " days) \u00b7 peak " + fmt.int(peak) + "/day"
    );
  }

  function renderEventHeatmap(events, days) {
    if (eventHeatmap) {
      eventHeatmap.destroy();
      eventHeatmap = null;
    }

    var hasEvents = !!(events && events.length);
    $("#event-activity-heatmap").toggleClass("hidden", !hasEvents);
    $("#event-activity-heatmap-meta").toggleClass("hidden", !hasEvents);
    $("#event-activity-heatmap-empty").toggleClass("hidden", hasEvents);
    if (!hasEvents) return;

    var canvas = document.getElementById("event-activity-chart");
    if (!canvas) return;

    var series = buildEventHeatmapSeries(events, days || 365);
    var colorTheme = buildHeatmapColorTheme(series);

    renderHeatmapSummary(series, days || 365);
    renderHeatmapLegend(colorTheme);

    eventHeatmap = new MatrixHeatmap({
      canvasId: "event-activity-chart",
      entityName: "events",
      data: series,
      colorTheme: colorTheme
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

    var period = periodParams();
    var days = period.days;

    $("#analytics-loading").removeClass("hidden");
    $("#analytics-dashboard").addClass("hidden");
    $("#analytics-empty").addClass("hidden");
    $("#events-time-series-note").addClass("hidden");
    destroyCharts();

    $.when(
      api("/events/", $.extend({ event_time_from: eventTimeFrom(days), limit: 1000 }, period)),
      api("/reporting/summary", period)
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
      renderEventHeatmap(events, days);
    }).fail(function (xhr) {
      $("#analytics-loading").addClass("hidden");
      showApiError("loading analytics data", xhr);
    });
  }

  function bindEvents() {
    $(document).on("change", "#analytics-period-select", load);
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
