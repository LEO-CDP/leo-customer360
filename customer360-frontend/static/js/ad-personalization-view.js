/* Customer 360 Admin -- Ad personalisation: decision explanations and the
 * personalised-versus-hold-out comparison.
 *
 * Two panels, both read-only, backed by the admin plane of customer360-api
 * (specs/003-ad-personalization-engine/contracts/admin-plane.openapi.yaml):
 *
 *   - Explain a decision: look up a request id (GET /ad-personalization/decisions/{id})
 *     and show the group, the reason and, for each chosen ad, its rank, score
 *     and the signals that account for it.
 *   - Compare with hold-out: GET /ad-personalization/reports/comparison for a
 *     date range, with per-group counts and, per campaign objective, the matching
 *     metric and the lift of personalised over hold-out.
 *
 * Every server-provided value is written with .text() / textContent, never as
 * HTML. Only the truncated visitor fingerprint is ever shown, never a visitor key.
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  // Signal names from the admin contract, with a plain description of each.
  var SIGNAL_LABELS = {
    objective_rate_lift: "Objective rate lift",
    visitor_affinity: "Visitor affinity",
    cohort_pattern: "Cohort pattern",
    exploration: "Exploration",
    base_weight: "Base weight",
    page_context: "Page context"
  };

  var GROUP_LABELS = {
    personalised: "Personalised",
    holdout: "Hold-out",
    default: "Default",
    default_no_history: "Default: no history yet",
    default_no_consent: "Default: no consent",
    default_disabled: "Default: personalisation off",
    default_fallback: "Default: fallback"
  };

  var METRIC_LABELS = {
    view_rate: "View rate",
    click_through_rate: "Click-through rate",
    conversion_rate: "Conversion rate"
  };

  var UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  // ---------------------------------------------------------------- formatting

  function number(value, digits) {
    var n = Number(value);
    return isFinite(n) ? n.toFixed(digits) : "-";
  }

  function count(value) {
    var n = Number(value);
    return isFinite(n) ? n.toLocaleString("en-AU") : "-";
  }

  function percent(rate, digits) {
    var n = Number(rate);
    return rate === null || rate === undefined || !isFinite(n) ? "-" : (n * 100).toFixed(digits) + "%";
  }

  function signedPercent(lift) {
    var n = Number(lift);
    if (lift === null || lift === undefined || !isFinite(n)) return "-";
    return (n >= 0 ? "+" : "") + (n * 100).toFixed(1) + "%";
  }

  function dateTime(value) {
    var d = new Date(value);
    return isNaN(d.getTime()) ? String(value || "-") : d.toLocaleString("en-AU");
  }

  function groupLabel(group) {
    return GROUP_LABELS[group] || String(group || "-");
  }

  // ------------------------------------------------------------- DOM builders

  function el(tag, className, text) {
    var $node = $("<" + tag + "></" + tag + ">");
    if (className) $node.addClass(className);
    if (text !== undefined && text !== null) $node.text(text);
    return $node;
  }

  function note(text, tone) {
    var cls = "rounded border p-3 text-sm " + (tone === "error"
      ? "border-red-200 bg-red-50 text-red-700"
      : "border-slate-200 bg-slate-50 text-slate-600");
    return el("div", cls, text);
  }

  function table(headers, rows) {
    var $table = el("table", "min-w-full text-sm");
    var $head = el("tr", "text-left text-xs font-semibold text-slate-500");
    headers.forEach(function (h) { $head.append(el("th", "py-2 pr-4", h)); });
    $table.append(el("thead").append($head));
    var $body = el("tbody");
    rows.forEach(function (cells) {
      var $row = el("tr", "border-t border-slate-100");
      cells.forEach(function (cell) { $row.append(el("td", "py-2 pr-4 align-top").append(cell)); });
      $body.append($row);
    });
    return $table.append($body);
  }

  // ------------------------------------------------------------- explanations

  function renderSignals(signals) {
    var list = signals || [];
    if (!list.length) return el("span", "text-slate-400", "No signals recorded");
    var $list = el("ul", "space-y-1");
    list.forEach(function (signal) {
      // Names outside the contract are shown as given, as text.
      var label = SIGNAL_LABELS[signal.name] || String(signal.name || "Signal");
      var $item = el("li");
      $item.append(el("span", "font-medium", label + " "));
      $item.append(el("span", "text-slate-500", "x" + number(signal.contribution, 2)));
      if (signal.note) $item.append(el("span", "text-slate-500", " : " + signal.note));
      $list.append($item);
    });
    return $list;
  }

  function renderDecision(decision) {
    var $root = el("div", "space-y-4");
    var chosen = decision.chosen || [];

    var $summary = el("dl", "grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-4");
    [
      ["Group", groupLabel(decision.group)],
      ["Decided", dateTime(decision.decided_at)],
      ["Placement", decision.placement_key || "-"],
      ["Candidates", decision.candidate_count === undefined ? "-" : count(decision.candidate_count)],
      ["Visitor", decision.visitor_fingerprint || "Not tracked"]
    ].forEach(function (pair) {
      var $cell = el("div");
      $cell.append(el("dt", "text-xs text-slate-500", pair[0]));
      $cell.append(el("dd", "font-medium", pair[1]));
      $summary.append($cell);
    });
    $root.append($summary);

    if (!chosen.length) {
      $root.append(note("No ads were chosen for this request."));
      return $root;
    }

    var rows = chosen.map(function (ad) {
      return [
        document.createTextNode(String(ad.rank)),
        document.createTextNode(ad.ad_key || "-"),
        document.createTextNode(ad.objective || "-"),
        document.createTextNode(ad.score === undefined || ad.score === null ? "-" : number(ad.score, 3)),
        renderSignals(ad.signals)[0]
      ];
    });
    $root.append(table(["Rank", "Ad", "Objective", "Score", "Why it ranked here"], rows));
    return $root;
  }

  // --------------------------------------------------------------- comparison

  function renderReport(report) {
    var $root = el("div", "space-y-4");
    var groups = report.groups || [];
    var objectives = report.by_objective || [];

    if (!groups.length) {
      return $root.append(note("No delivery was recorded in this period."));
    }

    $root.append(table(
      ["Group", "Impressions", "Views", "Clicks", "Conversions"],
      groups.map(function (g) {
        return [groupLabel(g.group), count(g.impressions), count(g.views), count(g.clicks), count(g.conversions)]
          .map(function (text) { return document.createTextNode(text); });
      })
    ));

    if (!objectives.length) {
      $root.append(note("No campaign objectives to compare in this period."));
      return $root;
    }

    $root.append(el("h4", "text-sm font-semibold text-slate-700", "Personalised versus hold-out, on each objective's own metric"));
    $root.append(table(
      ["Objective", "Metric", "Personalised", "Hold-out", "Lift"],
      objectives.map(function (o) {
        var insufficient = !!o.insufficient_data;
        return [
          o.objective || "-",
          METRIC_LABELS[o.metric] || o.metric || "-",
          insufficient ? "-" : percent(o.personalised_rate, 2),
          insufficient ? "-" : percent(o.holdout_rate, 2),
          insufficient ? "Insufficient data" : signedPercent(o.lift)
        ].map(function (text) { return document.createTextNode(text); });
      })
    ));
    return $root;
  }

  // -------------------------------------------------------------------- panels

  function startOfDayIso(dateText) { return dateText + "T00:00:00Z"; }

  function endOfDayIso(dateText) {
    // The range is inclusive of the chosen end date, so run to the start of the next day.
    var d = new Date(dateText + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + 1);
    return d.toISOString();
  }

  function lookupDecision() {
    var requestId = $.trim($("#adp-request-id").val() || "");
    var $out = $("#adp-decision-result").empty();
    if (!UUID_PATTERN.test(requestId)) {
      $out.append(note("Enter the request id exactly as returned by /serve (a UUID).", "error"));
      return;
    }
    $out.append(note("Loading..."));
    api("/ad-personalization/decisions/" + encodeURIComponent(requestId))
      .done(function (decision) { $out.empty().append(renderDecision(decision || {})); })
      .fail(function (xhr) {
        if (xhr && xhr.status === 404) {
          $out.empty().append(note("No decision found for that request id. It may belong to another tenant or be older than the 90-day retention period."));
          return;
        }
        $out.empty();
        showApiError("loading the decision explanation", xhr);
      });
  }

  function loadReport() {
    var from = $("#adp-from").val();
    var to = $("#adp-to").val();
    var placement = $.trim($("#adp-placement").val() || "");
    var $out = $("#adp-report-result").empty();
    if (!from || !to || from > to) {
      $out.append(note("Choose a start date on or before the end date.", "error"));
      return;
    }
    var params = { from: startOfDayIso(from), to: endOfDayIso(to) };
    if (placement) params.placement_key = placement;
    $out.append(note("Loading..."));
    api("/ad-personalization/reports/comparison", params)
      .done(function (report) { $out.empty().append(renderReport(report || {})); })
      .fail(function (xhr) {
        $out.empty();
        showApiError("loading the personalisation comparison", xhr);
      });
  }

  function isoDate(d) { return d.toISOString().slice(0, 10); }

  function mount() {
    var $root = $("#ad-personalization-content").empty();

    var $explain = el("section", "rounded-lg border border-slate-200 bg-white p-4 space-y-3");
    $explain.append(el("h3", "text-base font-semibold", "Explain a decision"));
    $explain.append(el("p", "text-sm text-slate-500",
      "Paste the request id from an ad response to see why each ad ranked where it did."));
    var $form = el("div", "flex flex-wrap items-end gap-2");
    $form.append($("<input>").attr({ id: "adp-request-id", type: "text", placeholder: "Request id (UUID)", autocomplete: "off", "aria-label": "Request id" })
      .addClass("min-w-[22rem] rounded border border-slate-300 px-2 py-1 text-sm"));
    $form.append(el("button", "rounded bg-indigo-600 px-3 py-1 text-sm text-white hover:bg-indigo-700", "Explain").attr({ id: "adp-explain-btn", type: "button" }));
    $explain.append($form).append(el("div").attr("id", "adp-decision-result"));

    var $compare = el("section", "rounded-lg border border-slate-200 bg-white p-4 space-y-3");
    $compare.append(el("h3", "text-base font-semibold", "Personalised versus hold-out"));
    $compare.append(el("p", "text-sm text-slate-500",
      "Delivery in each group and, per campaign objective, the matching rate (views for awareness, clicks for traffic, conversions for conversions)."));
    var today = new Date();
    var monthAgo = new Date(today.getTime() - 28 * 86400000);
    var $range = el("div", "flex flex-wrap items-end gap-2");
    $range.append($("<input>").attr({ id: "adp-from", type: "date", "aria-label": "From", value: isoDate(monthAgo) }).addClass("rounded border border-slate-300 px-2 py-1 text-sm"));
    $range.append($("<input>").attr({ id: "adp-to", type: "date", "aria-label": "To", value: isoDate(today) }).addClass("rounded border border-slate-300 px-2 py-1 text-sm"));
    $range.append($("<input>").attr({ id: "adp-placement", type: "text", placeholder: "Placement key (optional)", "aria-label": "Placement key" }).addClass("rounded border border-slate-300 px-2 py-1 text-sm"));
    $range.append(el("button", "rounded bg-indigo-600 px-3 py-1 text-sm text-white hover:bg-indigo-700", "Compare").attr({ id: "adp-compare-btn", type: "button" }));
    $compare.append($range).append(el("div").attr("id", "adp-report-result"));

    $root.append($explain).append($compare);
    loadReport();
  }

  $(document).on("click", "#adp-explain-btn", lookupDecision);
  $(document).on("keydown", "#adp-request-id", function (e) { if (e.key === "Enter") lookupDecision(); });
  $(document).on("click", "#adp-compare-btn", loadReport);

  C360.router.define("/ad-personalization", {
    section: "view-ad-personalization",
    tab: "ad-personalization",
    mount: mount
  });

  // The render functions are exposed so they can be exercised without a browser.
  C360.adPersonalizationView = {
    renderDecision: renderDecision,
    renderReport: renderReport,
    lookupDecision: lookupDecision,
    loadReport: loadReport
  };
})(window.C360);
