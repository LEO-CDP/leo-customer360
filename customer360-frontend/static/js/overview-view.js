/* Customer 360 Admin -- Reporting Overview dashboard.
 * Fetches core-customer360-api /reporting/* endpoints (see
 * customer360-api/core/routers/reporting_api.py) and renders KPI cards plus
 * Chart.js visualizations: processing funnel, profile counts by domain,
 * raw profiles by source system, and identity graph channel coverage.
 * Duplicate-profile counts remain available as reporting metrics. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var charts = {};
  var PALETTE = ["#6366f1", "#22c55e", "#3b82f6", "#f97316", "#ef4444", "#a855f7", "#14b8a6", "#eab308"];

  function renderChart(id, config) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    var el = document.getElementById(id);
    if (!el) return;
    charts[id] = new Chart(el.getContext("2d"), config);
  }

  function buildVm(summary) {
    return {
      totalRawProfilesLabel: fmt.int(summary.total_raw_profiles),
      totalMasterProfilesLabel: fmt.int(summary.total_master_profiles),
      duplicateCountLabel: fmt.int(summary.duplicate_master_profile_count),
      processedLabel: fmt.int(summary.processed_raw_profiles),
      inProgressLabel: fmt.int(summary.in_progress_raw_profiles),
      pendingLabel: fmt.int(summary.pending_raw_profiles)
    };
  }

  function renderStatusFunnelChart(summary) {
    var rows = summary.raw_profiles_by_status || [];
    renderChart("chart-status-funnel", {
      type: "doughnut",
      data: {
        labels: rows.map(function (r) { return r.label; }),
        datasets: [{ data: rows.map(function (r) { return r.count; }), backgroundColor: PALETTE }]
      },
      options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } }
    });
  }

  function renderDomainChart(summary) {
    var rawByDomain = summary.raw_profiles_by_domain || [];
    var masterByDomain = summary.master_profiles_by_domain || [];
    var domains = [];
    rawByDomain.concat(masterByDomain).forEach(function (r) {
      if (domains.indexOf(r.domain) === -1) domains.push(r.domain);
    });
    var rawMap = {}, masterMap = {};
    rawByDomain.forEach(function (r) { rawMap[r.domain] = r.count; });
    masterByDomain.forEach(function (r) { masterMap[r.domain] = r.count; });

    renderChart("chart-domain-breakdown", {
      type: "bar",
      data: {
        labels: domains.map(function (d) { return fmt.domainLabel(d); }),
        datasets: [
          { label: "Raw Profiles", data: domains.map(function (d) { return rawMap[d] || 0; }), backgroundColor: PALETTE[0] },
          { label: "Master Profiles", data: domains.map(function (d) { return masterMap[d] || 0; }), backgroundColor: PALETTE[1] }
        ]
      },
      options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true } } }
    });
  }

  function renderSourceSystemsChart(summary) {
    var rows = (summary.raw_profiles_by_source_system || []).slice(0, 10);
    renderChart("chart-source-systems", {
      type: "bar",
      data: {
        labels: rows.map(function (r) { return r.source_system + " (" + fmt.titleCase(r.domain) + ")"; }),
        datasets: [{ label: "Raw Profiles", data: rows.map(function (r) { return r.count; }), backgroundColor: PALETTE[2] }]
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } }
      }
    });
  }

  function renderIdentityCoverageChart(coverage) {
    var total = coverage.total_master_profiles || 0;
    var channels = [
      { label: "Email", count: coverage.with_email },
      { label: "Phone", count: coverage.with_phone_number },
      { label: "Device ID", count: coverage.with_device_id },
      { label: "Advertising ID", count: coverage.with_advertising_id },
      { label: "Cookie ID", count: coverage.with_cookie_id },
      { label: "External ID", count: coverage.with_external_id },
      { label: "National ID", count: coverage.with_national_id }
    ].sort(function (a, b) { return b.count - a.count; });

    renderChart("chart-identity-coverage", {
      type: "bar",
      data: {
        labels: channels.map(function (c) { return c.label; }),
        datasets: [{
          label: "Coverage %",
          data: channels.map(function (c) { return total ? Math.round((c.count / total) * 1000) / 10 : 0; }),
          backgroundColor: PALETTE[3]
        }]
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) { return ctx.parsed.x + "%"; } } }
        },
        scales: { x: { beginAtZero: true, max: 100 } }
      }
    });
  }

  function periodParams() {
    var days = C360.config.getDataPeriodDays("#overview-period-select");
    return { days: days };
  }

  function journeyWindow(days) {
    if (days <= 3) return 1;
    if (days <= 7) return 7;
    if (days <= 30) return 30;
    return 90;
  }

  function journeyMetricValue(metric) {
    if (!metric || metric.value == null) return "—";
    var value = Number(metric.value);
    if (!isFinite(value)) return "—";
    if (metric.unit === "percent") return value.toFixed(1) + "%";
    if (metric.unit === "ratio") return value.toFixed(2);
    if (metric.unit === "currency") {
      return value.toLocaleString() + (metric.currency && metric.currency !== "UNK" ? " " + metric.currency : "");
    }
    return fmt.int(value);
  }

  function journeyChangeLabel(metric) {
    if (!metric || metric.value == null) return "Awaiting snapshot";
    if (metric.change == null) return "No prior comparison";
    var change = Number(metric.change);
    if (!isFinite(change)) return "No prior comparison";
    var sign = change > 0 ? "+" : "";
    return sign + change.toFixed(1) + (metric.change_kind === "points" ? " pts" : "%") + " vs prior";
  }

  function campaignStatusPill(campaign) {
    var status = String(campaign.status || "").trim();
    var approval = String(campaign.approval_status || "").trim();
    if (status === "Running" || status === "Active") {
      return { label: status, cls: "bg-emerald-100 text-emerald-700" };
    }
    if (status === "Failed") {
      return { label: "Failed · retry", cls: "bg-rose-100 text-rose-700" };
    }
    if (approval === "InReview") {
      return { label: "Workflow in review", cls: "bg-amber-100 text-amber-800" };
    }
    if (approval === "Rejected") {
      return { label: "Rejected", cls: "bg-rose-100 text-rose-700" };
    }
    if (status === "Completed") {
      return { label: "Completed", cls: "bg-indigo-100 text-indigo-700" };
    }
    if (status === "Paused") {
      return { label: "Paused", cls: "bg-amber-100 text-amber-700" };
    }
    if (approval === "Approved") {
      return { label: "Approved", cls: "bg-emerald-50 text-emerald-700" };
    }
    return { label: status || approval || "Draft", cls: "bg-slate-100 text-slate-600" };
  }

  function shortAgentDisplayName(name) {
    return String(name || "").replace(/\s+Agent$/i, "");
  }

  function renderJourneyReport(report) {
    var $stages = $("#journey-report-stages").empty();
    var stages = report.stages || [];
    stages.forEach(function (stage, index) {
      var metric = stage.primary_metric;
      var hasChange = metric && metric.value != null && metric.change != null;
      var changeIsPositive = hasChange &&
        (metric.higher_is_better ? metric.change >= 0 : metric.change <= 0);
      var $card = $("<article>")
        .addClass("flex min-w-0 flex-col justify-between rounded-2xl border border-slate-200/90 bg-white p-4 shadow-sm transition-shadow hover:shadow-md")
        .attr("data-journey-stage", stage.stage);

      var $top = $("<div>");
      $("<div>").addClass("flex items-center gap-2").append(
        $("<span>").addClass("flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white").text(index + 1),
        $("<h3>").addClass("truncate text-sm font-bold text-slate-900").text(stage.label)
      ).appendTo($top);
      $("<div>").addClass("mt-3.5 text-xs font-medium text-slate-500").text(metric ? metric.label : "Primary KPI").appendTo($top);
      $("<div>").addClass("mt-0.5 text-2xl font-extrabold tracking-tight text-slate-900").text(journeyMetricValue(metric)).appendTo($top);
      $("<div>")
        .addClass("mt-0.5 text-[11px] font-medium " + (hasChange ? (changeIsPositive ? "text-emerald-600" : "text-rose-600") : "text-slate-400"))
        .text(journeyChangeLabel(metric))
        .appendTo($top);

      var campaigns = stage.campaigns || [];
      var $campaignSection = $("<div>").addClass("mt-3.5 space-y-2");
      if (campaigns.length) {
        campaigns.slice(0, 2).forEach(function (campaign) {
          var pill = campaignStatusPill(campaign);
          var $box = $("<button>")
            .attr("type", "button")
            .attr("data-journey-campaign-id", campaign.campaign_id)
            .addClass("block w-full rounded-xl border border-slate-200 bg-slate-50/40 p-3 text-left transition hover:border-indigo-300 hover:bg-indigo-50/30 focus:outline-none focus:ring-2 focus:ring-indigo-500/20");
          $("<div>").addClass("line-clamp-2 text-xs font-bold text-slate-900").text(campaign.name || "Untitled campaign").appendTo($box);
          var $pillRow = $("<div>").addClass("mt-1.5 flex flex-wrap items-center gap-1.5");
          $("<span>")
            .addClass("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold " + pill.cls)
            .text(pill.label)
            .appendTo($pillRow);
          $pillRow.appendTo($box);

          var assignedAgents = campaign.agents || [];
          if (assignedAgents.length) {
            var $chips = $("<div>").addClass("mt-2 space-y-1");
            assignedAgents.slice(0, 2).forEach(function (agent) {
              var chipLabel = shortAgentDisplayName(agent.display_name || agent.agent_code) + " · " + (agent.role || "decide");
              $("<div>")
                .addClass("truncate rounded-lg bg-violet-100/80 px-2 py-1 text-[11px] font-medium text-violet-800")
                .attr("title", chipLabel)
                .text(chipLabel)
                .appendTo($chips);
            });
            $chips.appendTo($box);
          }
          $box.appendTo($campaignSection);
        });
        if (campaigns.length > 2) {
          $("<div>")
            .addClass("text-[11px] font-medium text-slate-400 px-1")
            .text("+" + (campaigns.length - 2) + " more campaign" + (campaigns.length - 2 === 1 ? "" : "s"))
            .appendTo($campaignSection);
        }
      } else {
        $("<button>")
          .attr("type", "button")
          .attr("data-start-campaign-stage", stage.stage)
          .addClass("w-full rounded-xl border border-dashed border-slate-300 bg-slate-50/50 px-3 py-3 text-center text-xs font-semibold text-slate-600 transition hover:border-indigo-400 hover:bg-indigo-50/50 hover:text-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/20")
          .text("+ Start a campaign here")
          .appendTo($campaignSection);
      }
      $campaignSection.appendTo($top);
      $top.appendTo($card);

      var agents = stage.agents || [];
      var $alwaysOn = $("<div>").addClass("mt-4 border-t border-slate-100 pt-3");
      $("<div>").addClass("text-[11px] font-bold text-slate-800").text("Always-on").appendTo($alwaysOn);
      if (agents.length) {
        var $agentList = $("<div>").addClass("mt-1.5 space-y-1");
        agents.slice(0, 3).forEach(function (agent) {
          var isInactive = String(agent.status || "").toUpperCase() !== "ACTIVE";
          var label = shortAgentDisplayName(agent.display_name || agent.agent_code) + (isInactive ? " (inactive)" : "");
          $("<div>")
            .addClass("truncate text-xs " + (isInactive ? "text-slate-400" : "text-slate-600"))
            .attr("title", (agent.display_name || agent.agent_code) + (agent.schedule_definition ? " · " + agent.schedule_definition : ""))
            .text(label)
            .appendTo($agentList);
        });
        $agentList.appendTo($alwaysOn);
      } else {
        $("<div>").addClass("mt-1 text-xs text-slate-400").text("No always-on agents").appendTo($alwaysOn);
      }
      $alwaysOn.appendTo($card);
      $card.appendTo($stages);
    });
    $("#journey-report-meta").text(
      report.report_date ? "Snapshot " + report.report_date + " · trailing " + report.window_days + " days" : "Live campaign & agent map · trailing " + report.window_days + " days"
    );
    $("#journey-report-loading").addClass("hidden");
    $stages.removeClass("hidden");
  }

  function loadJourneyReport(days) {
    $("#journey-report-loading").removeClass("hidden");
    $("#journey-report-stages").addClass("hidden");
    $("#journey-report-error").addClass("hidden").text("");
    api("/reporting/journey", {
      tenant_id: C360.config.current.tenantId,
      days: days,
      window: journeyWindow(days)
    }).done(renderJourneyReport).fail(function (xhr) {
      $("#journey-report-loading").addClass("hidden");
      $("#journey-report-error").removeClass("hidden").text(
        (xhr.responseJSON && xhr.responseJSON.detail) || "Could not load the customer journey report."
      );
    });
  }

  function load() {
    var days = periodParams().days;
    $("#overview-loading").removeClass("hidden");
    $("#overview-content").empty();

    api("/reporting/summary", { days: days }).done(function (summary) {
      var vm = buildVm(summary);
      $("#overview-loading").addClass("hidden");
      $("#overview-content").html(C360.templates.render("overview-dashboard", vm));
      // The select is recreated by the render above -- restore the period the
      // user had picked instead of it snapping back to the template default.
      $("#overview-period-select").val(days);

      renderStatusFunnelChart(summary);
      renderDomainChart(summary);
      renderSourceSystemsChart(summary);
      loadJourneyReport(days);

      api("/reporting/identity-graph/coverage", { days: days })
        .done(function (coverage) { renderIdentityCoverageChart(coverage); })
        .fail(function (xhr) { showApiError("loading identity graph coverage", xhr); });
    }).fail(function (xhr) {
      $("#overview-loading").addClass("hidden");
      showApiError("loading reporting overview", xhr);
    });
  }

  function bindEvents() {
    $(document).on("change", "#overview-period-select", load);
    $(document).on("click", "#btn-overview-refresh", load);
    $(document).on("click", "[data-start-campaign-stage]", function () {
      var stage = $(this).attr("data-start-campaign-stage") || "";
      if (C360.campaignView && typeof C360.campaignView.openCreateForStage === "function") {
        C360.campaignView.openCreateForStage(stage);
      } else {
        C360.router.navigate("/campaigns/new/edit");
      }
    });
    $(document).on("click", "[data-journey-campaign-id]", function () {
      var campaignId = $(this).attr("data-journey-campaign-id");
      if (campaignId) {
        C360.router.navigate("/campaigns/" + encodeURIComponent(campaignId));
      }
    });
  }

  bindEvents();

  // Owns the "/overview" route (see router.js).
  C360.router.define("/overview", {
    section: "view-overview",
    tab: "overview",
    mount: load
  });

  C360.overviewView = { load: load };
})(window.C360);
