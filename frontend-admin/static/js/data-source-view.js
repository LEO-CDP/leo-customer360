/* Customer 360 Admin -- Data Sources view (sys_data_source).
 *
 * Owns /datasources route and renders list/create/detail/delete interactions
 * via the metadata endpoints implemented in customer360-api.
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var api = C360.config.api;
  var showApiError = C360.config.showApiError;
  var fmt = C360.fmt;

  var allItems = [];
  var filteredItems = [];
  var loadedOnce = false;
  var SOURCE_TYPE_OPTIONS = {
    1: "Web JavaScript Code (Client-side tracking)",
    2: "Data Connector API (Server-side Pull/Sync)",
    3: "Data Webhook API (Server-side Push)",
    4: "S3 File Connector (Batch File Processing)",
    5: "Mobile SDK Code (iOS/Android/Flutter tracking)"
  };

  function statusBadge(status) {
    if (Number(status) === 1) return '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold bg-green-100 text-green-700">Active</span>';
    return '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold bg-slate-100 text-slate-600">Inactive</span>';
  }

  function modeBadge(item) {
    var collectDirectly = item.collect_directly !== false;
    var firstParty = item.first_party_data !== false;
    var text = (collectDirectly ? "Direct" : "Indirect") + " / " + (firstParty ? "1P" : "3P");
    return '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold bg-cyan-50 text-cyan-700">' + text + '</span>';
  }

  function esc(value) {
    return $("<div>").text(value == null ? "" : String(value)).html();
  }

  function typeLabel(sourceType) {
    var t = Number(sourceType);
    if (SOURCE_TYPE_OPTIONS[t]) return SOURCE_TYPE_OPTIONS[t];
    return "Unknown Type (" + t + ")";
  }

  function renderRows(items) {
    var rows = items.map(function (item) {
      var id = esc(item.data_source_id);
      var name = esc(item.name || "(Unnamed)");
      var slug = esc(item.slug || "-");
      var type = esc(typeLabel(item.source_type));
      var totalEvents = fmt && fmt.int ? fmt.int(item.total_tracked_event || 0) : String(item.total_tracked_event || 0);
      var avgDailyEvents = fmt && fmt.int ? fmt.int(item.avg_daily_event || 0) : String(item.avg_daily_event || 0);
      var avgPerProfile = Number(item.avg_events_per_profile || 0).toFixed(2);
      return [
        '<tr class="border-t border-slate-100 hover:bg-slate-50 transition-colors">',
        '  <td class="px-6 py-3.5">',
        '    <div class="font-semibold text-slate-900">' + name + '</div>',
        '    <div class="text-[11px] text-slate-500 font-mono mt-0.5">' + slug + '</div>',
        '  </td>',
        '  <td class="px-6 py-3.5 text-slate-700">' + type + '</td>',
        '  <td class="px-6 py-3.5">' + statusBadge(item.status) + '</td>',
        '  <td class="px-6 py-3.5">' + modeBadge(item) + '</td>',
        '  <td class="px-6 py-3.5 text-slate-700">',
        '    <div class="font-medium">Total: ' + totalEvents + '</div>',
        '    <div class="text-[11px] text-slate-500 mt-0.5">Daily avg: ' + avgDailyEvents + ' · Per profile: ' + avgPerProfile + '</div>',
        '  </td>',
        '  <td class="px-6 py-3.5">',
        '    <div class="flex items-center justify-end gap-1.5">',
        '      <button class="btn-datasource-view border border-slate-200 bg-white rounded-lg px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50" data-id="' + id + '">View</button>',
        '      <button class="btn-datasource-delete border border-red-200 bg-white rounded-lg px-2.5 py-1 text-xs font-semibold text-red-600 hover:bg-red-50" data-id="' + id + '">Delete</button>',
        '    </div>',
        '  </td>',
        '</tr>'
      ].join("");
    }).join("");

    $("#datasources-tbody").html(rows);
    $("#datasources-empty").toggleClass("hidden", items.length !== 0);
    $("#datasources-count-label").text(items.length + " data source" + (items.length === 1 ? "" : "s") + " shown");
  }

  function applyFilters() {
    var q = String($("#datasources-search-input").val() || "").trim().toLowerCase();
    var status = String($("#datasources-status-filter").val() || "").trim();

    filteredItems = allItems.filter(function (item) {
      if (status !== "" && String(item.status) !== status) return false;
      if (!q) return true;
      var hay = [item.name, item.slug, item.data_source_url, item.thumbnail_url].map(function (v) {
        return String(v || "").toLowerCase();
      }).join(" ");
      return hay.indexOf(q) !== -1;
    });

    renderRows(filteredItems);
  }

  function setLoading(loading) {
    $("#datasources-loading").toggleClass("hidden", !loading);
  }

  function load() {
    setLoading(true);
    return api("/metadata/data-sources", {
      tenant_id: C360.config.current.tenantId,
      skip: 0,
      limit: 500
    })
      .done(function (items) {
        allItems = Array.isArray(items) ? items : [];
        loadedOnce = true;
        setLoading(false);
        applyFilters();
      })
      .fail(function (xhr) {
        setLoading(false);
        showApiError("loading data sources", xhr);
      });
  }

  function field(label, value) {
    return [
      '<div class="rounded-xl border border-slate-100 bg-slate-50/40 p-3">',
      '  <div class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">' + esc(label) + '</div>',
      '  <div class="mt-1 text-xs text-slate-700 break-words">' + esc(value == null || value === "" ? "-" : value) + '</div>',
      '</div>'
    ].join("");
  }

  function renderQrCodeSection(qrData) {
    if (typeof qrData === "string") {
      try { qrData = JSON.parse(qrData); } catch (e) { qrData = {}; }
    }
    qrData = (qrData && typeof qrData === "object" && !Array.isArray(qrData)) ? qrData : {};

    var keys = Object.keys(qrData);
    if (keys.length === 0) {
    return [
        // Outer container: Added dark mode border (dark:border-slate-700) and background (dark:bg-slate-800/60)
        '<div class="col-span-2 mt-2 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-800/60 p-4">',
        
        // Header wrapper: Added dark mode text color for the title (dark:text-slate-200)
        '  <div class="flex items-center gap-2 mb-1.5 text-slate-700 dark:text-slate-200 font-bold text-xs">',
        
        // SVG Icon: Added dark mode icon color (dark:text-slate-400) to maintain contrast without being too bright
        '    <svg class="w-4 h-4 text-slate-500 dark:text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z"/></svg>',
        '    <span>QR Code Ingestion & Tracking Details</span>',
        '  </div>',
        
        // Helper text: Added dark mode muted text color (dark:text-slate-400)
        '  <div class="text-xs text-slate-500 dark:text-slate-400">No QR code data generated yet. Provide a Data Source URL to auto-generate tracking QR codes.</div>',
        '</div>'
    ].join("");
    }

    var targetUrl = qrData.target_url || "-";
    var trackingUrl = qrData.tracking_url || "-";
    var qrCodeUrl = qrData.qr_code_url || "";
    var generatedAt = qrData.generated_at || "-";

    var qrImgHtml = qrCodeUrl ?
      '<div class="flex-shrink-0 p-2 bg-white rounded-xl border border-slate-200/80 shadow-sm">' +
      '  <img src="' + esc(qrCodeUrl) + '" alt="QR Code" class="w-28 h-28 object-contain" />' +
      '</div>' : '';

    var fieldsHtml = [
      '<div class="space-y-2 flex-grow min-w-0">',
      '  <div>',
      '    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Target URL</span>',
      '    <a href="' + esc(targetUrl) + '" target="_blank" rel="noopener noreferrer" class="text-xs text-cyan-600 hover:underline break-all font-mono">' + esc(targetUrl) + '</a>',
      '  </div>',
      '  <div>',
      '    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Tracking URL (with UTM)</span>',
      '    <a href="' + esc(trackingUrl) + '" target="_blank" rel="noopener noreferrer" class="text-xs text-cyan-600 hover:underline break-all font-mono">' + esc(trackingUrl) + '</a>',
      '  </div>',
      '  <div>',
      '    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Generated At</span>',
      '    <span class="text-xs text-slate-700 font-mono">' + esc(generatedAt) + '</span>',
      '  </div>',
      '</div>'
    ].join("");

    var extraKeys = keys.filter(function (k) {
      return ["target_url", "tracking_url", "qr_code_url", "generated_at"].indexOf(k) === -1;
    });

    var extraFieldsHtml = "";
    if (extraKeys.length > 0) {
      extraFieldsHtml = '<div class="col-span-2 pt-2 border-t border-cyan-100 text-xs space-y-1 mt-2">' +
        extraKeys.map(function (k) {
          var val = typeof qrData[k] === "object" ? JSON.stringify(qrData[k]) : qrData[k];
          return '<div><span class="font-semibold text-slate-500">' + esc(k) + ':</span> <span class="font-mono text-slate-700">' + esc(val) + '</span></div>';
        }).join("") +
        '</div>';
    }

    return [
      '<div class="col-span-2 mt-2 rounded-xl border border-cyan-200/80 bg-cyan-50/40 p-4 shadow-sm">',
      '  <div class="flex items-center gap-2 mb-3 pb-2 border-b border-cyan-100 text-cyan-900 font-bold text-xs">',
      '    <svg class="w-4 h-4 text-cyan-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z"/></svg>',
      '    <span>QR Code Ingestion & Tracking Details</span>',
      '  </div>',
      '  <div class="flex flex-wrap sm:flex-nowrap gap-4 items-start">',
      qrImgHtml,
      fieldsHtml,
      '  </div>',
      extraFieldsHtml,
      '</div>'
    ].join("");
  }

  function openDetailModal(item) {
    var body = $("#datasource-detail-body");
    var hosts = Array.isArray(item.data_source_hosts) ? item.data_source_hosts.join(", ") : "-";
    var tags = Array.isArray(item.javascript_tags) ? item.javascript_tags.join(", ") : "-";
    var accessTokens = item.access_tokens ? JSON.stringify(item.access_tokens, null, 2) : "{}";

    var qrSection = "";
    if (Number(item.source_type) === 1 || (item.qr_code_data && typeof item.qr_code_data === "object" && Object.keys(item.qr_code_data).length > 0)) {
      qrSection = renderQrCodeSection(item.qr_code_data);
    }

    body.html(
      field("Data Source ID", item.data_source_id) +
      field("Tenant ID", item.tenant_id) +
      field("Name", item.name) +
      field("Slug", item.slug) +
      field("Source Type", typeLabel(item.source_type)) +
      field("Status", Number(item.status) === 1 ? "Active" : "Inactive") +
      field("Data Source URL", item.data_source_url) +
      field("Thumbnail URL", item.thumbnail_url) +
      field("Collect Directly", item.collect_directly ? "Yes" : "No") +
      field("First-Party Data", item.first_party_data ? "Yes" : "No") +
      field("Total Tracked Event", item.total_tracked_event) +
      field("Average Daily Event", item.avg_daily_event) +
      field("Average Events Per Profile", item.avg_events_per_profile) +
      field("Hosts", hosts) +
      field("Javascript Tags", tags) +
      field("Access Tokens", accessTokens) +
      field("Created At", item.created_at) +
      field("Updated At", item.updated_at) +
      qrSection
    );

    $("#datasource-detail-modal").removeClass("hidden");
  }

  function closeDetailModal() {
    $("#datasource-detail-modal").addClass("hidden");
  }

  function openCreateModal() {
    $("#datasource-add-error").addClass("hidden").text("");
    $("#datasource-add-name").val("");
    $("#datasource-add-slug").val("");
    $("#datasource-add-source-type").val("2");
    $("#datasource-add-status").val("1");
    $("#datasource-add-url").val("");
    $("#datasource-add-thumbnail-url").val("");
    $("#datasource-add-hosts").val("");
    $("#datasource-add-access-tokens").val("");
    $("#datasource-add-collect-directly").prop("checked", true);
    $("#datasource-add-first-party-data").prop("checked", true);
    $("#datasource-form-modal").removeClass("hidden");
  }

  function closeCreateModal() {
    $("#datasource-form-modal").addClass("hidden");
  }

  function parseCsvList(value) {
    return String(value || "")
      .split(",")
      .map(function (x) { return x.trim(); })
      .filter(function (x) { return x.length > 0; });
  }

  function saveDataSource() {
    var errorEl = $("#datasource-add-error");
    errorEl.addClass("hidden").text("");

    var name = String($("#datasource-add-name").val() || "").trim();
    var slug = String($("#datasource-add-slug").val() || "").trim();
    if (!name || !slug) {
      errorEl.removeClass("hidden").text("Name and Slug are required.");
      return;
    }

    var accessTokensRaw = String($("#datasource-add-access-tokens").val() || "").trim();
    var accessTokens = {};
    if (accessTokensRaw) {
      try {
        accessTokens = JSON.parse(accessTokensRaw);
        if (typeof accessTokens !== "object" || accessTokens === null || Array.isArray(accessTokens)) {
          errorEl.removeClass("hidden").text("Access Tokens must be a valid JSON object (e.g. {\"key\": \"value\"}).");
          return;
        }
      } catch (e) {
        errorEl.removeClass("hidden").text("Access Tokens is not valid JSON. Format as {\"key\": \"value\"}.");
        return;
      }
    }

    var payload = {
      tenant_id: C360.config.current.tenantId,
      name: name,
      slug: slug,
      source_type: Number($("#datasource-add-source-type").val() || 2),
      status: Number($("#datasource-add-status").val() || 1),
      data_source_url: String($("#datasource-add-url").val() || "").trim() || null,
      thumbnail_url: String($("#datasource-add-thumbnail-url").val() || "").trim() || null,
      collect_directly: $("#datasource-add-collect-directly").is(":checked"),
      first_party_data: $("#datasource-add-first-party-data").is(":checked"),
      data_source_hosts: parseCsvList($("#datasource-add-hosts").val()),
      access_tokens: accessTokens,
      javascript_tags: []
    };

    if (!SOURCE_TYPE_OPTIONS[payload.source_type]) {
      errorEl.removeClass("hidden").text("Source Type must be one of 1, 2, 3, 4, 5.");
      return;
    }

    api("/metadata/data-sources", payload, "POST")
      .done(function () {
        closeCreateModal();
        load();
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        errorEl.removeClass("hidden").text(typeof detail === "string" ? detail : "Could not create data source.");
      });
  }

  function viewDataSource(id) {
    api("/metadata/data-sources/" + id)
      .done(function (item) {
        openDetailModal(item);
      })
      .fail(function (xhr) {
        showApiError("loading data source detail", xhr);
      });
  }

  function deleteDataSource(id) {
    var found = allItems.find(function (item) { return item.data_source_id === id; });
    var label = found && found.name ? found.name : id;
    if (!window.confirm("Delete data source '" + label + "'?")) return;

    api("/metadata/data-sources/" + id, {}, "DELETE")
      .done(function () {
        load();
      })
      .fail(function (xhr) {
        showApiError("deleting data source", xhr);
      });
  }

  function bindEvents() {
    $(document).on("input", "#datasources-search-input", function () {
      applyFilters();
    });

    $(document).on("change", "#datasources-status-filter", function () {
      applyFilters();
    });

    $(document).on("click", "#btn-datasource-add", openCreateModal);
    $(document).on("click", "#btn-datasource-add-cancel", closeCreateModal);
    $(document).on("click", "#btn-datasource-add-save", saveDataSource);

    $(document).on("click", "#datasource-form-modal", function (e) {
      if (e.target === this) closeCreateModal();
    });

    $(document).on("click", "#btn-datasource-detail-close, #btn-datasource-detail-done", closeDetailModal);
    $(document).on("click", "#datasource-detail-modal", function (e) {
      if (e.target === this) closeDetailModal();
    });

    $(document).on("click", ".btn-datasource-view", function () {
      viewDataSource(String($(this).data("id") || ""));
    });

    $(document).on("click", ".btn-datasource-delete", function () {
      deleteDataSource(String($(this).data("id") || ""));
    });
  }

  C360.router.define("/datasources", {
    section: "view-datasources",
    tab: "datasources",
    mount: function () {
      load();
    }
  });

  // Backward compatibility for previous placeholder deep links.
  C360.router.redirect("/datasources/connectors", "/datasources");
  C360.router.redirect("/datasources/importers", "/datasources");
  C360.router.redirect("/datasources/identity-rules", "/datasources");

  C360.dataSourceView = {
    load: load,
    bindEvents: bindEvents
  };
})(window.C360);
