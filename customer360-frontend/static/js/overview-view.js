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
  var journeyReportRequest = 0;
  var JOURNEY_STAGES = [
    {
      key: "awareness",
      label: "Awareness",
      icon: "bi-eye-fill",
      marker: "bg-blue-600",
      chevronBg: "bg-gradient-to-r from-blue-100/90 to-blue-50/90",
      chevronText: "text-blue-800",
      iconBadge: "bg-blue-50 text-blue-600",
      text: "text-blue-800",
      soft: "bg-slate-50/90",
      border: "border-l-blue-500",
      line: "border-blue-200",
      sparkColor: "#3b82f6"
    },
    {
      key: "acquisition",
      label: "Acquisition",
      icon: "bi-person-plus-fill",
      marker: "bg-sky-500",
      chevronBg: "bg-gradient-to-r from-sky-100/90 to-sky-50/90",
      chevronText: "text-sky-800",
      iconBadge: "bg-sky-50 text-sky-600",
      text: "text-sky-800",
      soft: "bg-slate-50/90",
      border: "border-l-sky-500",
      line: "border-sky-200",
      sparkColor: "#0ea5e9"
    },
    {
      key: "activation",
      label: "Activation",
      icon: "bi-lightning-charge-fill",
      marker: "bg-violet-600",
      chevronBg: "bg-gradient-to-r from-violet-100/90 to-violet-50/90",
      chevronText: "text-violet-800",
      iconBadge: "bg-violet-50 text-violet-600",
      text: "text-violet-800",
      soft: "bg-slate-50/90",
      border: "border-l-violet-500",
      line: "border-violet-200",
      sparkColor: "#8b5cf6"
    },
    {
      key: "engagement",
      label: "Engagement",
      icon: "bi-people-fill",
      marker: "bg-emerald-500",
      chevronBg: "bg-gradient-to-r from-emerald-100/90 to-emerald-50/90",
      chevronText: "text-emerald-800",
      iconBadge: "bg-emerald-50 text-emerald-600",
      text: "text-emerald-800",
      soft: "bg-slate-50/90",
      border: "border-l-emerald-500",
      line: "border-emerald-200",
      sparkColor: "#10b981"
    },
    {
      key: "conversion",
      label: "Conversion",
      icon: "bi-cart-fill",
      marker: "bg-amber-500",
      chevronBg: "bg-gradient-to-r from-amber-100/90 to-amber-50/90",
      chevronText: "text-amber-900",
      iconBadge: "bg-amber-50 text-amber-600",
      text: "text-amber-900",
      soft: "bg-slate-50/90",
      border: "border-l-amber-400",
      line: "border-amber-200",
      sparkColor: "#f59e0b"
    },
    {
      key: "retention",
      label: "Retention",
      icon: "bi-arrow-repeat",
      marker: "bg-teal-500",
      chevronBg: "bg-gradient-to-r from-teal-100/90 to-teal-50/90",
      chevronText: "text-teal-800",
      iconBadge: "bg-teal-50 text-teal-600",
      text: "text-teal-800",
      soft: "bg-slate-50/90",
      border: "border-l-teal-500",
      line: "border-teal-200",
      sparkColor: "#14b8a6"
    },
    {
      key: "loyalty",
      label: "Loyalty",
      icon: "bi-heart-fill",
      marker: "bg-pink-500",
      chevronBg: "bg-gradient-to-r from-pink-100/90 to-pink-50/90",
      chevronText: "text-pink-800",
      iconBadge: "bg-pink-50 text-pink-600",
      text: "text-pink-800",
      soft: "bg-slate-50/90",
      border: "border-l-pink-500",
      line: "border-pink-200",
      sparkColor: "#ec4899"
    },
    {
      key: "churned",
      label: "Churned",
      icon: "bi-box-arrow-right",
      marker: "bg-rose-600",
      chevronBg: "bg-gradient-to-r from-rose-100/95 to-rose-50/95",
      chevronText: "text-rose-800",
      iconBadge: "bg-rose-100/80 text-rose-600",
      text: "text-rose-800",
      soft: "bg-rose-50/60",
      border: "border-l-rose-500",
      line: "border-rose-200",
      sparkColor: "#e11d48",
      isChurned: true
    }
  ];
  var JOURNEY_STAGE_INDEX = {};
  JOURNEY_STAGES.forEach(function (stage, index) {
    JOURNEY_STAGE_INDEX[stage.key] = index;
  });
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
      pendingLabel: fmt.int(summary.pending_raw_profiles),
      journeySkeletonStages: JOURNEY_STAGES
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
    if (!metric || metric.value == null || metric.value === "") return "Not available";
    var value = Number(metric.value);
    if (!isFinite(value)) return "Not available";
    if (metric.unit === "percent") return value.toFixed(1) + "%";
    if (metric.unit === "ratio") return value.toFixed(2);
    if (metric.unit === "currency") {
      return value.toLocaleString() + (metric.currency && metric.currency !== "UNK" ? " " + metric.currency : "");
    }
    return fmt.int(value);
  }

  function journeyChangeLabel(metric) {
    if (!metric || metric.value == null || metric.value === "") return "KPI snapshot not available";
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

  function journeyStageTheme(stageCode) {
    var index = JOURNEY_STAGE_INDEX[stageCode];
    return index == null
      ? {
          key: stageCode,
          label: fmt.titleCase(String(stageCode || "").replace(/_/g, " ")),
          icon: "bi-circle-fill",
          marker: "bg-slate-600",
          chevronBg: "bg-slate-100",
          chevronText: "text-slate-800",
          iconBadge: "bg-slate-100 text-slate-600",
          text: "text-slate-800",
          soft: "bg-slate-50",
          border: "border-l-slate-400",
          line: "border-slate-200",
          sparkColor: "#64748b"
        }
      : JOURNEY_STAGES[index];
  }

  function buildStageSparklineSvg(color, index) {
    var paths = [
      "M0,22 C24,21 38,16 58,18 C82,20 102,11 126,13 C140,14 150,8 160,9",
      "M0,23 C22,20 42,15 64,17 C86,19 106,9 128,12 C142,13 151,7 160,8",
      "M0,22 C20,23 44,14 66,16 C88,18 108,10 130,12 C144,13 152,6 160,8",
      "M0,21 C26,18 46,19 68,14 C90,10 110,13 132,9 C145,7 153,10 160,7"
    ];
    var strokePath = paths[index % paths.length];
    var areaPath = strokePath + " L160,28 L0,28 Z";
    return (
      '<svg class="pointer-events-none absolute inset-x-0 bottom-0 h-7 w-full" viewBox="0 0 160 28" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="' + areaPath + '" fill="' + color + '" fill-opacity="0.14"></path>' +
      '<path d="' + strokePath + '" fill="none" stroke="' + color + '" stroke-width="1.6" stroke-opacity="0.55" stroke-linecap="round"></path>' +
      "</svg>"
    );
  }

  function renderJourneyTrack(stages) {
    var $track = $("#journey-report-track");
    var $items = $("#journey-report-track-items").empty();
    var $dots = $("#journey-report-track-dots").empty();
    if (!stages.length) {
      $track.addClass("hidden");
      return;
    }

    $("<span>")
      .attr("aria-hidden", "true")
      .addClass("pointer-events-none absolute left-[6%] right-[6%] top-1/2 -translate-y-1/2 border-t-2 border-dashed border-slate-300")
      .appendTo($dots);

    stages.forEach(function (stage, index) {
      var theme = journeyStageTheme(stage.stage);
      var clipPolygon = "polygon(0 0, calc(100% - 12px) 0, 100% 50%, calc(100% - 12px) 100%, 0 100%, 10px 50%)";
      var $item = $("<li>").addClass("min-w-0");
      $("<button>")
        .attr({
          type: "button",
          "data-journey-stage-link": stage.stage,
          "aria-label": "Scroll to " + stage.label + " stage"
        })
        .css("clip-path", clipPolygon)
        .addClass("group flex h-11 w-full items-center justify-center gap-1.5 px-3 text-center transition hover:brightness-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 " + theme.chevronBg + " " + theme.chevronText)
        .append(
          $("<i>").addClass("bi " + theme.icon + " shrink-0 text-xs opacity-90").attr("aria-hidden", "true"),
          $("<span>").addClass("truncate text-xs font-bold tracking-tight").text((index + 1) + ". " + stage.label)
        )
        .appendTo($item);
      $item.appendTo($items);

      $("<div>")
        .addClass("relative z-10 flex items-center justify-center")
        .append(
          $("<span>").addClass("h-3.5 w-3.5 rounded-full ring-4 ring-white shadow-sm " + theme.marker)
        )
        .appendTo($dots);
    });
    $track.removeClass("hidden");
  }

  function createJourneyCampaignCard(campaign) {
    var pill = campaignStatusPill(campaign);
    var $box = $("<button>")
      .attr("type", "button")
      .attr("data-journey-campaign-id", campaign.campaign_id)
      .attr("aria-label", "Open campaign " + (campaign.name || "Untitled campaign"))
      .addClass("block w-full rounded-xl border border-slate-200 bg-white p-3 text-left transition hover:border-indigo-300 hover:bg-indigo-50/40 focus:outline-none focus:ring-2 focus:ring-indigo-500/20");
    $("<div>")
      .addClass("line-clamp-2 text-xs font-bold leading-relaxed text-slate-900")
      .text(campaign.name || "Untitled campaign")
      .appendTo($box);

    var $meta = $("<div>").addClass("mt-2 flex flex-wrap items-center gap-1.5");
    $("<span>")
      .addClass("inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold " + pill.cls)
      .text(pill.label)
      .appendTo($meta);
    if (campaign.channel || campaign.platform) {
      $("<span>")
        .addClass("truncate text-[10px] text-slate-500")
        .text([campaign.channel, campaign.platform].filter(Boolean).join(" · "))
        .appendTo($meta);
    }
    $meta.appendTo($box);

    var assignedAgents = campaign.agents || [];
    if (assignedAgents.length) {
      var $chips = $("<div>").addClass("mt-2 flex flex-wrap gap-1");
      assignedAgents.slice(0, 2).forEach(function (agent) {
        var chipLabel = shortAgentDisplayName(agent.display_name || agent.agent_code) + " · " + (agent.role || "decide");
        $("<span>")
          .addClass("max-w-full truncate rounded-md bg-violet-50 px-1.5 py-1 text-[10px] font-medium text-violet-800")
          .attr("title", chipLabel)
          .text(chipLabel)
          .appendTo($chips);
      });
      if (assignedAgents.length > 2) {
        $("<span>")
          .addClass("rounded-md bg-violet-50 px-1.5 py-1 text-[10px] font-medium text-violet-800")
          .text("+" + (assignedAgents.length - 2))
          .appendTo($chips);
      }
      $chips.appendTo($box);
    }
    return $box;
  }

  function renderJourneyReport(report) {
    var $stages = $("#journey-report-stages").empty();
    var stages = (report.stages || []).slice().sort(function (a, b) {
      var aIndex = JOURNEY_STAGE_INDEX[a.stage];
      var bIndex = JOURNEY_STAGE_INDEX[b.stage];
      return (aIndex == null ? Number.MAX_SAFE_INTEGER : aIndex) -
        (bIndex == null ? Number.MAX_SAFE_INTEGER : bIndex);
    });
    renderJourneyTrack(stages);
    var campaignCount = 0;
    var agentCodes = {};
    var stagesWithKpis = 0;

    stages.forEach(function (stage, index) {
      var theme = journeyStageTheme(stage.stage);
      var metric = stage.primary_metric;
      var hasMetricValue = metric && metric.value != null && metric.value !== "" && isFinite(Number(metric.value));
      var hasChange = hasMetricValue && metric.change != null && isFinite(Number(metric.change));
      var changeIsPositive = hasChange &&
        (metric.higher_is_better ? metric.change >= 0 : metric.change <= 0);
      if (hasMetricValue) stagesWithKpis += 1;

      var $card = $("<article>")
        .attr({
          id: "journey-stage-" + stage.stage,
          "data-journey-stage": stage.stage,
          tabindex: "-1"
        })
        .addClass(
          "flex min-h-[370px] min-w-0 flex-col rounded-2xl border border-slate-200/90 border-l-4 p-3.5 shadow-sm transition-shadow hover:shadow-md " +
          (theme.isChurned ? "bg-rose-50/20 " : "bg-white ") +
          theme.border
        );

      var $top = $("<div>");
      $("<div>").addClass("flex items-center gap-2").append(
        $("<span>")
          .addClass("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs " + theme.iconBadge)
          .append($("<i>").addClass("bi " + theme.icon).attr("aria-hidden", "true")),
        $("<h3>").addClass("truncate text-sm font-bold text-slate-900").text(stage.label)
      ).appendTo($top);

      var $metric = $("<div>").addClass(
        "relative mt-3.5 min-h-[104px] overflow-hidden rounded-xl border px-3 pt-2.5 pb-7 " +
        (theme.isChurned ? "border-rose-100/90 bg-rose-50/60" : "border-slate-100 bg-slate-50/80")
      );
      $("<div>")
        .addClass("text-[11px] font-medium " + (theme.isChurned ? "text-rose-700" : "text-slate-600"))
        .text(metric ? metric.label : "Primary KPI")
        .appendTo($metric);
      $("<div>")
        .addClass(
          "mt-0.5 break-words text-lg font-extrabold tracking-tight " +
          (hasMetricValue
            ? (theme.isChurned ? "text-rose-700" : "text-slate-900")
            : (theme.isChurned ? "text-rose-600/80" : "text-slate-600"))
        )
        .text(journeyMetricValue(metric))
        .appendTo($metric);
      $("<div>")
        .addClass(
          "mt-0.5 text-[10px] leading-relaxed font-medium " +
          (hasChange
            ? (changeIsPositive ? "text-emerald-700" : "text-rose-700")
            : (theme.isChurned ? "text-rose-500/80" : "text-slate-500"))
        )
        .text(journeyChangeLabel(metric))
        .appendTo($metric);
      $metric.append(buildStageSparklineSvg(theme.sparkColor, index));
      $metric.appendTo($top);

      var campaigns = stage.campaigns || [];
      campaignCount += campaigns.length;
      var $campaignSection = $("<div>").addClass("mt-3.5");
      var $campaignHeading = $("<div>").addClass("mb-2 flex items-center justify-between gap-2");
      $("<span>").addClass("text-[11px] font-bold text-slate-900").text("Campaigns").appendTo($campaignHeading);
      $("<span>").addClass("rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600").text(campaigns.length).appendTo($campaignHeading);
      $campaignHeading.appendTo($campaignSection);
      if (campaigns.length) {
        var $campaignList = $("<div>").addClass("space-y-2");
        campaigns.forEach(function (campaign, campaignIndex) {
          var $campaignCard = createJourneyCampaignCard(campaign);
          if (campaignIndex >= 2) {
            $campaignCard.addClass("hidden").attr("data-overflow-campaign", "true");
          }
          $campaignCard.appendTo($campaignList);
        });
        $campaignList.appendTo($campaignSection);
        if (campaigns.length > 2) {
          $("<button>")
            .attr({
              type: "button",
              "data-toggle-journey-campaigns": stage.stage,
              "data-collapsed-label": "Show " + (campaigns.length - 2) + " more campaign" + (campaigns.length - 2 === 1 ? "" : "s"),
              "data-expanded-label": "Show fewer campaigns",
              "aria-expanded": "false"
            })
            .addClass("mt-2 text-[11px] font-semibold text-indigo-700 hover:text-indigo-900 focus:outline-none focus:underline")
            .text("Show " + (campaigns.length - 2) + " more campaign" + (campaigns.length - 2 === 1 ? "" : "s"))
            .appendTo($campaignSection);
        }
      } else {
        $("<button>")
          .attr("type", "button")
          .attr("data-start-campaign-stage", stage.stage)
          .addClass("w-full rounded-full border border-slate-200/90 bg-white px-3 py-2 text-center text-xs font-semibold text-indigo-600 shadow-sm transition hover:border-indigo-300 hover:bg-indigo-50/40 focus:outline-none focus:ring-2 focus:ring-indigo-500/20")
          .text("+ Start a campaign")
          .appendTo($campaignSection);
      }
      $campaignSection.appendTo($top);
      $top.appendTo($card);

      var agents = stage.agents || [];
      agents.forEach(function (agent) {
        if (agent.agent_code) agentCodes[agent.agent_code] = true;
      });
      var $alwaysOn = $("<div>").addClass("mt-auto border-t border-slate-100 pt-3");
      var $agentHeading = $("<div>").addClass("mb-1.5 flex items-center justify-between gap-2");
      $("<span>").addClass("text-[11px] font-bold text-slate-800").text("Always-on agents").appendTo($agentHeading);
      $("<span>").addClass("rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700").text(agents.length).appendTo($agentHeading);
      $agentHeading.appendTo($alwaysOn);
      if (agents.length) {
        var $agentList = $("<div>").addClass("mt-1.5 space-y-1");
        agents.forEach(function (agent, agentIndex) {
          var agentStatus = String(agent.status || "").toUpperCase();
          var isInactive = agentStatus !== "ACTIVE";
          var agentStateClasses = {
            INACTIVE: { dot: "bg-slate-300", badge: "bg-slate-100 text-slate-500" },
            TRAINING: { dot: "bg-blue-500", badge: "bg-blue-50 text-blue-700" },
            FAILED: { dot: "bg-rose-500", badge: "bg-rose-50 text-rose-700" },
            DEPRECATED: { dot: "bg-amber-500", badge: "bg-amber-50 text-amber-700" }
          };
          var stateClasses = agentStatus === "ACTIVE"
            ? { dot: "bg-violet-500", badge: "" }
            : (agentStateClasses[agentStatus] || agentStateClasses.INACTIVE);
          var label = shortAgentDisplayName(agent.display_name || agent.agent_code);
          var $agentRow = $("<div>")
            .addClass("flex min-w-0 items-center gap-1.5 text-[11px] " + (isInactive ? "text-slate-500" : "text-slate-700"))
            .attr("title", (agent.display_name || agent.agent_code) + (agent.schedule_definition ? " · " + agent.schedule_definition : ""));
          $("<span>").addClass("h-1.5 w-1.5 shrink-0 rounded-full " + stateClasses.dot).appendTo($agentRow);
          $("<span>").addClass("min-w-0 flex-1 truncate").text(label).appendTo($agentRow);
          if (agent.status && String(agent.status).toUpperCase() !== "ACTIVE") {
            $("<span>")
              .addClass("shrink-0 rounded px-1 py-0.5 text-[9px] font-semibold uppercase " + stateClasses.badge)
              .text(fmt.titleCase(String(agent.status).toLowerCase()))
              .appendTo($agentRow);
          }
          if (agentIndex >= 3) $agentRow.addClass("hidden").attr("data-overflow-agent", "true");
          $agentRow.appendTo($agentList);
        });
        $agentList.appendTo($alwaysOn);
        if (agents.length > 3) {
          $("<button>")
            .attr({
              type: "button",
              "data-toggle-journey-agents": stage.stage,
              "data-collapsed-label": "Show " + (agents.length - 3) + " more agent" + (agents.length - 3 === 1 ? "" : "s") + " \u2304",
              "data-expanded-label": "Show fewer agents \u2303",
              "aria-expanded": "false"
            })
            .addClass("mt-1.5 text-[11px] font-bold text-indigo-700 hover:text-indigo-900 focus:outline-none focus:underline")
            .text("Show " + (agents.length - 3) + " more agent" + (agents.length - 3 === 1 ? "" : "s") + " \u2304")
            .appendTo($alwaysOn);
        }
      } else {
        $("<div>").addClass("mt-1 text-[11px] text-slate-400").text("No agents assigned to this stage").appendTo($alwaysOn);
      }
      $alwaysOn.appendTo($card);
      $card.appendTo($stages);
    });
    $("#journey-report-campaign-count").text(campaignCount + " campaign" + (campaignCount === 1 ? "" : "s"));
    $("#journey-report-agent-count").text(Object.keys(agentCodes).length + " unique agents");
    $("#journey-report-stage-count").text(stages.length + " stages");
    $("#journey-report-meta").text(
      (report.report_date ? "KPI snapshot " + report.report_date : "Operational map") +
        " · " + report.window_days + "-day window"
    );
    var $snapshotNotice = $("#journey-report-snapshot-notice");
    if (stagesWithKpis > 0 && stagesWithKpis < stages.length) {
      $snapshotNotice
        .removeClass("hidden")
        .text("Campaigns and agent assignments below are current. KPI snapshots are available for " + stagesWithKpis + " of " + stages.length + " stages.");
    } else {
      $snapshotNotice.addClass("hidden").text("");
    }
    $("#journey-report-loading").addClass("hidden");
    $stages.removeClass("hidden");
  }

  function loadJourneyReport(days) {
    var requestId = ++journeyReportRequest;
    $("#journey-report-loading").removeClass("hidden");
    $("#journey-report-stages").addClass("hidden");
    $("#journey-report-error").addClass("hidden").text("");
    $("#journey-report-snapshot-notice").addClass("hidden").text("");
    api("/reporting/journey", {
      tenant_id: C360.config.current.tenantId,
      days: days,
      window: journeyWindow(days)
    }).done(function (report) {
      if (requestId !== journeyReportRequest) return;
      renderJourneyReport(report);
    }).fail(function (xhr) {
      if (requestId !== journeyReportRequest) return;
      $("#journey-report-loading").addClass("hidden");
      $("#journey-report-error-message").text(
        (xhr.responseJSON && xhr.responseJSON.detail) || "Could not load the customer journey report."
      );
      $("#journey-report-error").removeClass("hidden");
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
    $(document).on("click", "#btn-journey-report-retry", function () {
      loadJourneyReport(periodParams().days);
    });
    $(document).on("click", "[data-journey-stage-link]", function () {
      var stage = $(this).attr("data-journey-stage-link");
      var card = document.getElementById("journey-stage-" + stage);
      if (!card) return;
      card.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      card.focus({ preventScroll: true });
    });
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
    $(document).on("click", "[data-toggle-journey-campaigns]", function () {
      var $button = $(this);
      var $card = $button.closest("[data-journey-stage]");
      var expanded = $button.attr("aria-expanded") === "true";
      $card.find("[data-overflow-campaign]").toggleClass("hidden", expanded);
      $button.attr("aria-expanded", expanded ? "false" : "true")
        .text(expanded ? $button.data("collapsed-label") : $button.attr("data-expanded-label"));
    });
    $(document).on("click", "[data-toggle-journey-agents]", function () {
      var $button = $(this);
      var $card = $button.closest("[data-journey-stage]");
      var expanded = $button.attr("aria-expanded") === "true";
      $card.find("[data-overflow-agent]").toggleClass("hidden", expanded);
      $button.attr("aria-expanded", expanded ? "false" : "true")
        .text(expanded ? $button.data("collapsed-label") : $button.attr("data-expanded-label"));
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
