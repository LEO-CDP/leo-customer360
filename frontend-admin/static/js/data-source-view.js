/* Customer 360 Admin -- Data Sources view (sys_data_source).
 *
 * Modeled directly on scoring-model-view.js & attributes-view.js:
 * Consumer of the shared C360.DataTableView component (static/js/common/data-table-view.js),
 * client-side searching & multi-filter support (source_type & status),
 * full Add/Edit/Detail modal lifecycle with 16:9 aspect ratio inspector,
 * and background refresh with cache-invalidation.
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var SOURCE_TYPE_OPTIONS = {
    1: "Web JavaScript Code (Client-side tracking)",
    2: "Data Connector API (Server-side Pull/Sync)",
    3: "Data Webhook API (Server-side Push)",
    4: "S3 File Connector (Batch File Processing)",
    5: "Mobile SDK Code (iOS/Android/Flutter tracking)"
  };

  var SOURCE_TYPE_SHORT_LABELS = {
    1: "Web JS",
    2: "API Pull",
    3: "Webhook",
    4: "S3 File",
    5: "Mobile SDK"
  };

  var TYPE_BADGE_CLASSES = {
    1: "bg-indigo-100 text-indigo-700",
    2: "bg-cyan-100 text-cyan-700",
    3: "bg-fuchsia-100 text-fuchsia-700",
    4: "bg-amber-100 text-amber-700",
    5: "bg-violet-100 text-violet-700"
  };
  function typeBadgeClass(t) { return TYPE_BADGE_CLASSES[t] || "bg-slate-100 text-slate-500"; }

  var STATUS_BADGE_CLASSES = {
    1: "bg-green-100 text-green-700",
    0: "bg-slate-100 text-slate-500"
  };
  function statusBadgeClass(s) { return STATUS_BADGE_CLASSES[s] || "bg-slate-100 text-slate-500"; }

  var TYPE_ICONS = {
    1: "🌐",
    2: "🔗",
    3: "📡",
    4: "📦",
    5: "📱"
  };
  function typeIcon(t) { return TYPE_ICONS[t] || "🧩"; }

  function typeLabel(sourceType) {
    var t = Number(sourceType);
    return SOURCE_TYPE_OPTIONS[t] || ("Unknown Type (" + t + ")");
  }

  function esc(value) {
    return $("<div>").text(value == null ? "" : String(value)).html();
  }

  function parseCsvList(value) {
    return String(value || "")
      .split(",")
      .map(function (x) { return x.trim(); })
      .filter(function (x) { return x.length > 0; });
  }

  function volumeMetrics(item) {
    var total = fmt && fmt.int ? fmt.int(item.total_tracked_event || 0) : String(item.total_tracked_event || 0);
    var daily = fmt && fmt.int ? fmt.int(item.avg_daily_event || 0) : String(item.avg_daily_event || 0);
    var perProfile = Number(item.avg_events_per_profile || 0).toFixed(2);
    return [
      { label: "Total", value: total },
      { label: "Daily Event", value: daily },
      { label: "AVG event / profile", value: perProfile }
    ];
  }

  function rowVm(item) {
    var t = Number(item.source_type);
    var s = Number(item.status);
    return $.extend({}, item, {
      nameLabel: item.name || "(Unnamed)",
      slugLabel: item.slug || "-",
      typeIcon: typeIcon(t),
      typeShortLabel: SOURCE_TYPE_SHORT_LABELS[t] || ("Type " + t),
      typeBadgeClass: typeBadgeClass(t),
      statusLabel: s === 1 ? "Active" : "Inactive",
      statusBadgeClass: statusBadgeClass(s),
      modeLabel: (item.collect_directly !== false ? "Direct" : "Indirect") + " / " + (item.first_party_data !== false ? "1P" : "3P"),
      modeBadgeClass: "bg-cyan-50 text-cyan-700",
      volumeMetrics: volumeMetrics(item)
    });
  }

  var dataSourcesById = {}; // cache of last-fetched rows, keyed by data_source_id
  var editingDataSourceId = null; // non-null when editing an existing data source
  var currentDetailId = null; // currently selected data source in detail modal

  var dtv = C360.DataTableView.create({
    columns: [
      {
        label: "Data Source", type: "identity", nameField: "nameLabel", subField: "slugLabel", subStyle: "tag",
        avatarField: "typeIcon", avatarBg: "bg-cyan-100", avatarColor: "text-cyan-700", avatarTextClass: "text-base"
      },
      { label: "Type", type: "badge", field: "typeShortLabel", classField: "typeBadgeClass" },
      { label: "Status", type: "badge", field: "statusLabel", classField: "statusBadgeClass" },
      { label: "Mode", type: "badge", field: "modeLabel", classField: "modeBadgeClass" },
      { label: "Volume Metrics", type: "metrics", field: "volumeMetrics" }
    ],
    rowVm: rowVm,
    rowId: function (vm) { return vm.data_source_id; },
    rowClickable: false,
    onEdit: function (id) { viewDataSource(id); },
    editLabel: "View",
    resourceLabel: "data source",
    clientSide: true,
    clientSideLimit: 500,
    fetch: function (params) {
      return api("/metadata/data-sources", $.extend({ tenant_id: C360.config.current.tenantId }, params));
    },
    clientFilters: {
      q: function (vm, value) {
        var needle = String(value || "").toLowerCase().trim();
        if (!needle) return true;
        var hosts = Array.isArray(vm.data_source_hosts) ? vm.data_source_hosts.join(" ").toLowerCase() : "";
        return (vm.name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.slug || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.data_source_url || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.thumbnail_url || "").toLowerCase().indexOf(needle) !== -1 ||
          hosts.indexOf(needle) !== -1;
      },
      type: function (vm, value) {
        if (!value) return true;
        return String(vm.source_type) === String(value);
      },
      status: function (vm, value) {
        if (value === "" || value == null) return true;
        return String(vm.status) === String(value);
      }
    },
    onFetched: function (items) {
      dataSourcesById = {};
      (items || []).forEach(function (i) { dataSourcesById[i.data_source_id] = i; });
    },
    onError: function (xhr) { showApiError("loading data sources", xhr); },
    el: {
      thead: "#datasources-thead",
      tbody: "#datasources-tbody",
      loading: "#datasources-loading",
      empty: "#datasources-empty",
      countLabel: "#datasources-count-label"
    }
  });

  function load(append, forceReload) {
    if (forceReload && dtv.resetClientCache) dtv.resetClientCache();
    return dtv.load(append, forceReload);
  }

  // --- Add / Edit Form Modal Management ---

  var FORM_TYPE_CONFIG = {
    1: {
      urlLabel: "Website URL *",
      urlPlaceholder: "https://www.example.com",
      hostsLabel: "Website Domains / Hosts (comma-separated)",
      hostsPlaceholder: "example.com, www.example.com",
      tokensLabel: "Configuration / Access Tokens (JSON Object)",
      tokensPlaceholder: '{"site_id": "site_01"}'
    },
    2: {
      urlLabel: "API Base URL / Endpoint *",
      urlPlaceholder: "https://analyticsdata.googleapis.com",
      hostsLabel: "Ingestion API Hosts (comma-separated)",
      hostsPlaceholder: "googleapis.com, api.segment.io",
      tokensLabel: "API Credentials & Access Tokens (JSON Object) *",
      tokensPlaceholder: '{"client_id": "...", "client_secret": "...", "measurement_id": "G-DEMO360"}'
    },
    3: {
      urlLabel: "Callback / Webhook Source URL (Optional)",
      urlPlaceholder: "https://crm.example.com/events",
      hostsLabel: "Allowed Webhook Source Hosts (comma-separated)",
      hostsPlaceholder: "crm.example.com, payment.stripe.com",
      tokensLabel: "Webhook Secret & Auth Headers (JSON Object)",
      tokensPlaceholder: '{"webhook_secret": "whsec_...", "verify_signature": true}'
    },
    4: {
      urlLabel: "S3 Storage URI *",
      urlPlaceholder: "s3://customer360-lake/inbound-events/",
      hostsLabel: "S3 Storage Endpoints (comma-separated)",
      hostsPlaceholder: "s3.amazonaws.com, storage.googleapis.com",
      tokensLabel: "S3 Bucket Credentials & Region (JSON Object) *",
      tokensPlaceholder: '{"aws_region": "us-east-1", "aws_bucket": "customer360-lake"}'
    },
    5: {
      urlLabel: "App Store / Package Bundle URL",
      urlPlaceholder: "https://apps.apple.com/app/id123456789",
      hostsLabel: "App API Endpoints / Hosts (comma-separated)",
      hostsPlaceholder: "api.mybrand.com, mobile.mybrand.com",
      tokensLabel: "Mobile SDK Config (JSON Object)",
      tokensPlaceholder: '{"app_id": "com.mybrand.app", "environment": "production"}'
    }
  };

  function updateFormFieldsForSourceType(sourceType) {
    var t = Number(sourceType || 2);
    var conf = FORM_TYPE_CONFIG[t] || FORM_TYPE_CONFIG[2];
    $("#datasource-add-url-label").text(conf.urlLabel);
    $("#datasource-add-url").attr("placeholder", conf.urlPlaceholder);
    $("#datasource-add-hosts-label").text(conf.hostsLabel);
    $("#datasource-add-hosts").attr("placeholder", conf.hostsPlaceholder);
    $("#datasource-add-tokens-label").text(conf.tokensLabel);
    $("#datasource-add-access-tokens").attr("placeholder", conf.tokensPlaceholder);
  }

  function openAddDataSourceModal() {
    editingDataSourceId = null;
    $("#datasource-form-title").text("Create Data Source");
    $("#datasource-form-subtitle").text("Creates a sys_data_source record for this tenant");
    $("#datasource-form-save-label").text("Save Data Source");
    $("#btn-datasource-form-delete").addClass("hidden");
    $("#datasource-add-error").addClass("hidden").text("");
    $("#datasource-add-name").val("");
    $("#datasource-add-slug").val("");
    $("#datasource-add-source-type").val("2");
    updateFormFieldsForSourceType(2);
    $("#datasource-add-status").val("1");
    $("#datasource-add-url").val("");
    $("#datasource-add-thumbnail-url").val("");
    $("#datasource-add-hosts").val("");
    $("#datasource-add-access-tokens").val("");
    $("#datasource-add-collect-directly").prop("checked", true);
    $("#datasource-add-first-party-data").prop("checked", true);
    $("#datasource-form-modal").removeClass("hidden");
  }

  function openEditDataSourceModal(id) {
    var item = dataSourcesById[id];
    if (!item) return;
    editingDataSourceId = id;
    closeDetailModal();

    $("#datasource-form-title").text("Edit Data Source");
    $("#datasource-form-subtitle").text("Updates this sys_data_source connector record");
    $("#datasource-form-save-label").text("Save Changes");
    $("#btn-datasource-form-delete").removeClass("hidden");
    $("#datasource-add-error").addClass("hidden").text("");

    var st = Number(item.source_type || 2);
    $("#datasource-add-source-type").val(String(st));
    updateFormFieldsForSourceType(st);

    $("#datasource-add-name").val(item.name || "");
    $("#datasource-add-slug").val(item.slug || "");
    $("#datasource-add-status").val(String(item.status != null ? item.status : 1));
    $("#datasource-add-url").val(item.data_source_url || "");
    $("#datasource-add-thumbnail-url").val(item.thumbnail_url || "");
    $("#datasource-add-hosts").val(Array.isArray(item.data_source_hosts) ? item.data_source_hosts.join(", ") : "");
    $("#datasource-add-access-tokens").val(
      item.access_tokens && Object.keys(item.access_tokens).length ? JSON.stringify(item.access_tokens, null, 2) : ""
    );
    $("#datasource-add-collect-directly").prop("checked", item.collect_directly !== false);
    $("#datasource-add-first-party-data").prop("checked", item.first_party_data !== false);

    $("#datasource-form-modal").removeClass("hidden");
  }

  function closeFormModal() {
    $("#datasource-form-modal").addClass("hidden");
  }

  function saveDataSource() {
    var $error = $("#datasource-add-error");
    $error.addClass("hidden").text("");

    var name = $.trim($("#datasource-add-name").val());
    var slug = $.trim($("#datasource-add-slug").val());
    if (!name || !slug) {
      $error.removeClass("hidden").text("Name and Slug are required.");
      return;
    }

    var accessTokensRaw = $.trim($("#datasource-add-access-tokens").val());
    var accessTokens = {};
    if (accessTokensRaw) {
      try {
        accessTokens = JSON.parse(accessTokensRaw);
        if (typeof accessTokens !== "object" || accessTokens === null || Array.isArray(accessTokens)) {
          $error.removeClass("hidden").text("Access Tokens must be a valid JSON object (e.g. {\"key\": \"value\"}).");
          return;
        }
      } catch (e) {
        $error.removeClass("hidden").text("Access Tokens is not valid JSON. Format as {\"key\": \"value\"}.");
        return;
      }
    }

    var isEdit = editingDataSourceId !== null;
    var payload = {
      name: name,
      slug: slug,
      source_type: Number($("#datasource-add-source-type").val() || 2),
      status: Number($("#datasource-add-status").val() || 1),
      data_source_url: $.trim($("#datasource-add-url").val()) || null,
      thumbnail_url: $.trim($("#datasource-add-thumbnail-url").val()) || null,
      collect_directly: $("#datasource-add-collect-directly").is(":checked"),
      first_party_data: $("#datasource-add-first-party-data").is(":checked"),
      data_source_hosts: parseCsvList($("#datasource-add-hosts").val()),
      access_tokens: accessTokens,
      javascript_tags: []
    };

    if (!isEdit) {
      payload.tenant_id = C360.config.current.tenantId;
    }

    var $saveBtn = $("#btn-datasource-add-save");
    $saveBtn.prop("disabled", true).addClass("opacity-70");

    var request = isEdit
      ? api("/metadata/data-sources/" + encodeURIComponent(editingDataSourceId), payload, "PATCH")
      : api("/metadata/data-sources", payload, "POST");

    request
      .done(function () {
        closeFormModal();
        load(false, true);
        if (typeof showToast === "function") {
          showToast(isEdit ? "Data source updated" : "Data source created", "success");
        }
      })
      .fail(function (xhr) {
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : ("Could not " + (isEdit ? "update" : "create") + " data source."));
      })
      .always(function () {
        $saveBtn.prop("disabled", false).removeClass("opacity-70");
      });
  }

  function deleteDataSource(targetId) {
    var id = targetId || currentDetailId || editingDataSourceId;
    if (!id) return;
    var found = dataSourcesById[id];
    var label = found && found.name ? found.name : id;
    if (!window.confirm("Delete data source '" + label + "'?")) return;

    api("/metadata/data-sources/" + encodeURIComponent(id), {}, "DELETE")
      .done(function () {
        closeDetailModal();
        closeFormModal();
        load(false, true);
        if (typeof showToast === "function") showToast("Data source deleted", "success");
      })
      .fail(function (xhr) {
        showApiError("deleting data source", xhr);
      });
  }

  // --- 16:9 Detail Modal Management ---

  function updateIntegrationPanels(item) {
    var type = Number(item.source_type);
    var host = location.host || "localhost:8000";
    var apiBase = (C360.config && C360.config.current && C360.config.current.apiBase) || (location.protocol + "//" + host + "/api/v1");

    // Hide all 5 type panels first
    $("#datasource-integration-type-1").addClass("hidden");
    $("#datasource-integration-type-2").addClass("hidden");
    $("#datasource-integration-type-3").addClass("hidden");
    $("#datasource-integration-type-4").addClass("hidden");
    $("#datasource-integration-type-5").addClass("hidden");

    if (type === 1) {
      // ONLY Type 1 (Web SDK) shows the JavaScript Tracking snippet
      var $panel1 = $("#datasource-integration-type-1");
      var $textarea = $("#datasource-detail-javascript-tags");
      var templateElement = document.getElementById("c360-tracker-template");
      if (templateElement) {
        var templateText = String(templateElement.textContent || "").trim();
        try {
          var compiled = Handlebars.compile(templateText);
          var cfg = (C360.config && C360.config.current) || {};
          var serverCfg = window.C360_SERVER_CONFIG || {};
          var configuredLogDomain = cfg.leoObserverLogDomain || serverCfg.leoObserverLogDomain;
          var configuredCdnDomain = cfg.leoObserverCdnDomain || serverCfg.leoObserverCdnDomain || "gcore.jsdelivr.net/gh/LEO-CDP/leo-customer360@main";
          
          var logDomain = configuredLogDomain;
          if (!logDomain) {
            logDomain = (item.data_source_hosts && item.data_source_hosts.length)
              ? item.data_source_hosts[0]
              : "beta.leocdp.com";
          }
          var cdnDomain = configuredCdnDomain;
          var scriptBody = compiled({
            dataSourceId: item.data_source_id,
            dataSourceName: item.name || "Web Touchpoint",
            leoObserverLogDomain: logDomain,
            leoObserverCdnDomain: cdnDomain
          });
          $textarea.val("<script>\n" + scriptBody.trim() + "\n<\/script>");
        } catch (e) {
          $textarea.val(templateText);
        }
      }
      $panel1.removeClass("hidden");
    } else if (type === 2) {
      // Type 2: Data Connector API (Pull/Sync)
      var $panel2 = $("#datasource-integration-type-2");
      $("#detail-type2-endpoint").text(item.data_source_url || "Configured via background ingestion worker");
      $panel2.removeClass("hidden");
    } else if (type === 3) {
      // Type 3: Data Webhook API (Server-side Push)
      var $panel3 = $("#datasource-integration-type-3");
      var tenantId = item.tenant_id || (C360.config && C360.config.current && C360.config.current.tenantId) || "00000000-0000-0000-0000-000000000000";
      var curlCmd = [
        'curl -X POST "' + apiBase + '/events" \\',
        '  -H "Content-Type: application/json" \\',
        '  -H "X-Tenant-Id: ' + tenantId + '" \\',
        '  -H "X-Data-Source-Id: ' + item.data_source_id + '" \\',
        '  -d \'{\n    "event_name": "touchpoint_event",\n    "profile_identities": {\n      "email": "customer@example.com",\n      "phone_number": "+15550100"\n    },\n    "event_data": {\n      "source": "' + (item.slug || "custom_webhook") + '",\n      "status": "success"\n    }\n  }\''
      ].join("\n");
      $("#detail-type3-curl").text(curlCmd);
      $panel3.removeClass("hidden");
    } else if (type === 4) {
      // Type 4: S3 File Batch Connector
      var $panel4 = $("#datasource-integration-type-4");
      var s3Uri = item.data_source_url || ("s3://customer360-lake/inbound/" + (item.slug || "source") + "/");
      $("#detail-type4-uri").text(s3Uri);
      $panel4.removeClass("hidden");
    } else if (type === 5) {
      // Type 5: Mobile SDK Code
      var $panel5 = $("#datasource-integration-type-5");
      var mobileSnippet = [
        '// --- iOS (Swift) ---',
        'LeoCDP.initialize(',
        '    dataSourceId: "' + item.data_source_id + '",',
        '    endpoint: "' + (location.protocol + "//" + host) + '"',
        ')',
        '',
        '// --- Android (Kotlin) ---',
        'LeoCDP.initialize(',
        '    context = applicationContext,',
        '    dataSourceId: "' + item.data_source_id + '",',
        '    endpoint: "' + (location.protocol + "//" + host) + '"',
        ')',
        '',
        '// --- Flutter (Dart) ---',
        'await LeoCDP.initialize(',
        '    dataSourceId: "' + item.data_source_id + '",',
        '    endpoint: "' + (location.protocol + "//" + host) + '"',
        ');'
      ].join("\n");
      $("#detail-type5-code").text(mobileSnippet);
      $panel5.removeClass("hidden");
    }
  }

  function renderQrCodeSection(qrData, sourceType) {
    var $section = $("#datasource-detail-qr-field");
    // QR code is specific to Web Touchpoints (type 1) or when qr_code_data explicitly populated
    if (Number(sourceType) !== 1 && (!qrData || typeof qrData !== "object" || Object.keys(qrData).length === 0)) {
      $section.addClass("hidden");
      return;
    }

    if (typeof qrData === "string") {
      try { qrData = JSON.parse(qrData); } catch (e) { qrData = {}; }
    }
    var hasQr = qrData && typeof qrData === "object" && Object.keys(qrData).length > 0;
    if (!hasQr) {
      $section.addClass("hidden");
      return;
    }

    $section.removeClass("hidden");
    var qrCodeUrl = qrData.qr_code_url || "";
    var targetUrl = qrData.target_url || "-";
    var trackingUrl = qrData.tracking_url || "-";
    var generatedAt = qrData.generated_at ? (fmt.dateTime ? fmt.dateTime(qrData.generated_at) : qrData.generated_at) : "-";

    if (qrCodeUrl) {
      $("#detail-qr-img").attr("src", qrCodeUrl);
      $("#detail-qr-img-wrapper").removeClass("hidden");
    } else {
      $("#detail-qr-img-wrapper").addClass("hidden");
    }

    $("#detail-qr-target").attr("href", targetUrl).text(targetUrl);
    $("#detail-qr-tracking").attr("href", trackingUrl).text(trackingUrl);
    $("#detail-qr-generated").text(generatedAt);
  }

  function openDetailModal(item) {
    var t = Number(item.source_type);
    var s = Number(item.status);

    // Header
    $("#detail-datasource-icon").text(typeIcon(t));
    $("#detail-datasource-name").text(item.name || "(Unnamed)");
    $("#detail-datasource-slug").text("#" + (item.slug || "-"));

    var $typeBadge = $("#detail-datasource-type-badge");
    $typeBadge.attr("class", "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold tracking-wide " + typeBadgeClass(t))
      .text(SOURCE_TYPE_SHORT_LABELS[t] || ("Type " + t));

    var $statusBadge = $("#detail-datasource-status-badge");
    $statusBadge.attr("class", "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold tracking-wide " + statusBadgeClass(s))
      .text(s === 1 ? "Active" : "Inactive");

    $("#detail-datasource-mode-badge").text(
      (item.collect_directly !== false ? "Direct" : "Indirect") + " / " + (item.first_party_data !== false ? "1P" : "3P")
    );

    // KPI Metrics
    $("#detail-total-events").text(fmt && fmt.int ? fmt.int(item.total_tracked_event || 0) : String(item.total_tracked_event || 0));
    $("#detail-daily-events").text(fmt && fmt.int ? fmt.int(item.avg_daily_event || 0) : String(item.avg_daily_event || 0));
    $("#detail-events-per-profile").text(Number(item.avg_events_per_profile || 0).toFixed(2));

    // Endpoints & Config
    if (item.data_source_url) {
      $("#detail-datasource-url").html('<a href="' + esc(item.data_source_url) + '" target="_blank" rel="noopener noreferrer" class="text-cyan-600 hover:underline">' + esc(item.data_source_url) + '</a>');
    } else {
      $("#detail-datasource-url").text("-");
    }

    if (item.thumbnail_url) {
      $("#detail-datasource-thumbnail").html('<a href="' + esc(item.thumbnail_url) + '" target="_blank" rel="noopener noreferrer" class="text-cyan-600 hover:underline">' + esc(item.thumbnail_url) + '</a>');
    } else {
      $("#detail-datasource-thumbnail").text("-");
    }

    var hosts = Array.isArray(item.data_source_hosts) && item.data_source_hosts.length
      ? item.data_source_hosts.join(", ")
      : "-";
    $("#detail-datasource-hosts").text(hosts);

    $("#detail-datasource-id").text(item.data_source_id || "-");
    $("#detail-datasource-tenant").text(item.tenant_id || "-");
    $("#detail-datasource-created").text(fmt && fmt.dateTime ? fmt.dateTime(item.created_at) : (item.created_at || "-"));
    $("#detail-datasource-updated").text(fmt && fmt.dateTime ? fmt.dateTime(item.updated_at) : (item.updated_at || "-"));

    var tokens = item.access_tokens && Object.keys(item.access_tokens).length
      ? JSON.stringify(item.access_tokens, null, 2)
      : "{}";
    $("#detail-datasource-tokens").text(tokens);

    updateIntegrationPanels(item);
    renderQrCodeSection(item.qr_code_data, item.source_type);

    $("#datasource-detail-modal").removeClass("hidden");
  }

  function closeDetailModal() {
    $("#datasource-detail-modal").addClass("hidden");
  }

  function viewDataSource(id) {
    currentDetailId = id;
    var cached = dataSourcesById[id];
    if (cached) openDetailModal(cached);

    api("/metadata/data-sources/" + encodeURIComponent(id))
      .done(function (item) {
        dataSourcesById[item.data_source_id] = item;
        openDetailModal(item);
      })
      .fail(function (xhr) {
        if (!cached) showApiError("loading data source detail", xhr);
      });
  }

  function bindEvents() {
    dtv.bindSearch("#datasources-search-input", "q", 300);
    dtv.bindSelect("#datasources-type-filter", "type");
    dtv.bindSelect("#datasources-status-filter", "status");
    dtv.bindRowEdit();

    // Toolbar refresh
    $(document).on("click", "#btn-datasource-refresh", function () {
      var $btn = $(this);
      $btn.prop("disabled", true).addClass("opacity-70");
      var $svg = $btn.find("svg");
      $svg.addClass("animate-spin");
      load(false, true)
        .done(function () {
          if (typeof showToast === "function") showToast("Data sources refreshed", "success");
        })
        .always(function () {
          $btn.prop("disabled", false).removeClass("opacity-70");
          $svg.removeClass("animate-spin");
        });
    });

    // Form Modal Actions
    $(document).on("change", "#datasource-add-source-type", function () {
      updateFormFieldsForSourceType($(this).val());
    });
    $(document).on("click", "#btn-datasource-add", openAddDataSourceModal);
    $(document).on("click", "#btn-datasource-add-cancel", closeFormModal);
    $(document).on("click", "#btn-datasource-add-save", saveDataSource);
    $(document).on("click", "#btn-datasource-form-delete", function () { deleteDataSource(); });
    $(document).on("click", "#datasource-form-modal", function (e) {
      if (e.target === this) closeFormModal();
    });

    // Detail Modal Actions
    $(document).on("click", "#btn-datasource-detail-close, #btn-datasource-detail-done", closeDetailModal);
    $(document).on("click", "#btn-datasource-detail-delete", function () { deleteDataSource(); });
    $(document).on("click", "#btn-datasource-detail-edit", function () {
      if (currentDetailId) openEditDataSourceModal(currentDetailId);
    });

    // Copy Actions for Different Source Types
    $(document).on("click", "#btn-datasource-detail-copy-tags", function () {
      var tagsText = $("#datasource-detail-javascript-tags").val();
      if (!tagsText) return;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(tagsText);
      }
      var $btn = $(this);
      var $label = $btn.find(".copy-tags-label");
      $label.text("Copied!");
      setTimeout(function () {
        $label.text("Copy Web Tag");
      }, 1500);
    });

    $(document).on("click", "#btn-datasource-detail-copy-curl", function () {
      var text = $("#detail-type3-curl").text();
      if (!text) return;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text);
      }
      var $btn = $(this);
      var $label = $btn.find(".copy-curl-label");
      $label.text("Copied!");
      setTimeout(function () {
        $label.text("Copy cURL");
      }, 1500);
    });

    $(document).on("click", "#btn-datasource-detail-copy-mobile", function () {
      var text = $("#detail-type5-code").text();
      if (!text) return;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text);
      }
      var $btn = $(this);
      var $label = $btn.find(".copy-mobile-label");
      $label.text("Copied!");
      setTimeout(function () {
        $label.text("Copy Mobile Code");
      }, 1500);
    });

    $(document).on("click", "#datasource-detail-modal", function (e) {
      if (e.target === this) closeDetailModal();
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
