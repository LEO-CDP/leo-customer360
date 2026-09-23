/* Customer 360 Admin -- Profile Detail view (dashboard). */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var currentProfileId = null;
  var currentContentType = "";
  var timelineLimit = 100;
  var timelineDataSourceId = "";
  var timelineDataSourcesLoadedForTenant = null;
  var timelineDataSourcesLoading = false;
  var timelineRequestLoading = false;
  var timelineRangePreset = "7d";
  var timelineFromEventTime = "";
  var timelineToEventTime = "";

  // Mirrors the sys_domain / validate_domain_value fixed dictionary
  // (customer360-api/core/utils/domains.py) -- used to populate the "Add
  // Attribute" domain <select> without a dedicated /domains endpoint.
  var DOMAIN_CODES = ["retail", "banking", "real_estate", "travel", "media", "education"];

  function populateDomainAttributeDomainSelect(defaultDomain) {
    var $select = $("#domain-attribute-domain");
    if (!$select.length) return;
    $select.empty();
    DOMAIN_CODES.forEach(function (code) {
      $select.append($("<option>").val(code).text(fmt.domainLabel(code)));
    });
    if (defaultDomain) $select.val(defaultDomain);
  }

  function submitDomainAttributeForm() {
    var domain = $("#domain-attribute-domain").val();
    var key = $.trim($("#domain-attribute-key").val());
    var value = $.trim($("#domain-attribute-value").val());
    var $error = $("#domain-attribute-form-error");
    $error.addClass("hidden").text("");

    if (!key) {
      $error.removeClass("hidden").text("Attribute key is required.");
      return;
    }

    api("/master-profiles/" + currentProfileId + "/domain-attributes", {
      domain: domain,
      attribute_key: key,
      attribute_value: value,
    }, "POST")
      .done(function () {
        $("#domain-attribute-key").val("");
        $("#domain-attribute-value").val("");
        reload();
      })
      .fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || "Could not add attribute.";
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : JSON.stringify(detail));
      });
  }

  // Profile detail has no Period control of its own (removed along with the
  // shared header one) -- always uses the fixed 90-day default.
  function periodDays() {
    return 90;
  }

  function timelineRequestParams() {
    var params = { limit: timelineLimit };
    if (timelineDataSourceId) params.data_source_id = timelineDataSourceId;
    if (timelineFromEventTime) params.from_event_time = timelineFromEventTime;
    if (timelineToEventTime) params.to_event_time = timelineToEventTime;
    return params;
  }

  function decodeTimelineValue(value) {
    if (!value) return "";
    try { return decodeURIComponent(String(value)); } catch (e) { return String(value); }
  }

  function safeTimelineUrl(value) {
    var decoded = decodeTimelineValue(value);
    return /^https?:\/\//i.test(decoded) ? decoded : "";
  }

  function compactTimelineUrl(value) {
    if (!value) return "";
    return value.length > 96 ? value.slice(0, 93) + "..." : value;
  }

  function timelineJsonLabel(value) {
    if (!value || typeof value !== "object") return "";
    try {
      var json = JSON.stringify(value);
      return json.length > 320 ? json.slice(0, 317) + "..." : json;
    } catch (e) { return ""; }
  }

  function escapeTimelineText(value) {
    return $("<div>").text(value === null || value === undefined ? "" : String(value)).html();
  }

  function timelineBadgeHtml(label, classes) {
    return label
      ? '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ' +
        classes +
        '">' +
        escapeTimelineText(label) +
        "</span>"
      : "";
  }

  function timelineLocalInputValue(date) {
    function pad(value) { return String(value).padStart(2, "0"); }
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
      "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  var TIMELINE_RANGE_LABELS = {
    "24h": "Last 24 hours",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    custom: "Custom range",
  };

  function timelineRangeStart(preset, end) {
    var hours = preset === "24h" ? 24 : preset === "30d" ? 30 * 24 : 7 * 24;
    return new Date(end.getTime() - hours * 60 * 60 * 1000);
  }

  function updateTimelineRangeUi(showGenericCustomLabel) {
    $(".timeline-range-preset").each(function () {
      var active = $(this).data("range") === timelineRangePreset;
      $(this)
        .toggleClass("bg-white text-indigo-700 shadow-sm", active)
        .toggleClass("text-slate-500 hover:bg-white/70", !active)
        .attr("aria-pressed", active ? "true" : "false");
    });
    $("#timeline-custom-range").toggleClass(
      "hidden",
      timelineRangePreset !== "custom",
    );

    var summary = TIMELINE_RANGE_LABELS[timelineRangePreset] || "Custom range";
    if (
      timelineRangePreset === "custom" &&
      timelineFromEventTime &&
      timelineToEventTime &&
      !showGenericCustomLabel
    ) {
      summary =
        fmt.dateTime(timelineFromEventTime) +
        " - " +
        fmt.dateTime(timelineToEventTime);
    }
    $("#timeline-range-summary").text(summary);
  }

  function initializeTimelineRange(forceReset) {
    if (forceReset || !timelineFromEventTime || !timelineToEventTime) {
      var now = new Date();
      timelineRangePreset = "7d";
      var start = timelineRangeStart(timelineRangePreset, now);
      timelineFromEventTime = start.toISOString();
      timelineToEventTime = now.toISOString();
    }
    $("#timeline-from-event-time").val(
      timelineLocalInputValue(new Date(timelineFromEventTime)),
    );
    $("#timeline-to-event-time").val(
      timelineLocalInputValue(new Date(timelineToEventTime)),
    );
    updateTimelineRangeUi();
  }

  function readTimelineRange() {
    var fromValue = $("#timeline-from-event-time").val();
    var toValue = $("#timeline-to-event-time").val();
    var fromDate = fromValue ? new Date(fromValue) : null;
    var toDate = toValue ? new Date(toValue) : null;
    var error = "";
    if (!fromDate || isNaN(fromDate.getTime()) || !toDate || isNaN(toDate.getTime())) {
      error = "Choose both a start and end time.";
    } else if (fromDate > toDate) {
      error = "The start time must be before the end time.";
    }
    $("#timeline-range-error").text(error).toggleClass("hidden", !error);
    if (error) return false;

    timelineRangePreset = "custom";
    timelineFromEventTime = fromDate.toISOString();
    timelineToEventTime = toDate.toISOString();
    updateTimelineRangeUi();
    return true;
  }

  function setTimelineRangePreset(preset) {
    if (!TIMELINE_RANGE_LABELS[preset]) return;
    timelineRangePreset = preset;
    $("#timeline-range-error").addClass("hidden").text("");
    if (preset !== "custom") {
      var end = new Date();
      timelineFromEventTime = timelineRangeStart(preset, end).toISOString();
      timelineToEventTime = end.toISOString();
      $("#timeline-from-event-time").val(
        timelineLocalInputValue(new Date(timelineFromEventTime)),
      );
      $("#timeline-to-event-time").val(
        timelineLocalInputValue(new Date(timelineToEventTime)),
      );
    }
    updateTimelineRangeUi(preset === "custom");
    if (preset !== "custom" && currentProfileId) reloadTimeline();
  }

  function updateTimelineLoadingState() {
    var requestLoading = timelineRequestLoading;
    var controlsLoading = timelineDataSourcesLoading || requestLoading;
    $("#profile-timeline-loading").toggleClass("hidden", !requestLoading);
    $("#timeline-data-source-filter").prop("disabled", controlsLoading);
    $(".timeline-range-control").prop("disabled", controlsLoading);
  }

  function loadTimelineDataSources() {
    var $select = $("#timeline-data-source-filter");
    var tenantId = C360.config.current && C360.config.current.tenantId;
    if (
      !$select.length ||
      !tenantId ||
      (timelineDataSourcesLoadedForTenant === tenantId && $select.find("option").length > 1)
    ) {
      return $.Deferred().resolve().promise();
    }

    timelineDataSourcesLoading = true;
    updateTimelineLoadingState();
    return api("/data-sources", {
      tenant_id: tenantId,
      status: 1,
      skip: 0,
      limit: 1000,
    }).done(function (sources) {
      var items = Array.isArray(sources) ? sources.slice() : [];
      items.sort(function (left, right) {
        return String(left.name || left.slug || "").localeCompare(String(right.name || right.slug || ""));
      });
      $select.find("option:not(:first)").remove();
      items.forEach(function (source) {
        var label = source.name || source.slug || source.data_source_id;
        if (source.slug && source.name && source.slug !== source.name) {
          label += " (" + source.slug + ")";
        }
        $select.append($("<option></option>").attr("value", source.data_source_id).text(label));
      });
      $select.val(timelineDataSourceId || "");
      timelineDataSourcesLoadedForTenant = tenantId;
    }).fail(function (xhr) {
      showApiError("loading timeline data sources", xhr);
    }).always(function () {
      timelineDataSourcesLoading = false;
      updateTimelineLoadingState();
    });
  }

  function timelineEntryVm(t) {
    var icon =
      t.kind === "transaction"
        ? "💳"
        : t.kind === "contact"
          ? "💬"
          : fmt.CATEGORY_ICONS[(t.event_category || t.subtitle || "").toUpperCase()] || "🔔";
    return {
      icon: icon,
      title: t.title || "Activity",
      channelLabel: fmt.titleCase(t.channel) || "—",
      timeLabel: fmt.dateTime(t.occurred_at),
      amountLabel:
        t.amount !== null && t.amount !== undefined && t.amount !== ""
          ? fmt.money(t.amount, t.currency)
          : null,
      sourceLabel: fmt.titleCase(t.source_system) || "",
      dataSourceIdLabel: t.data_source_id ? fmt.shortId(t.data_source_id) : "",
      domainLabel: t.domain ? fmt.domainLabel(t.domain) : "",
      deviceTypeLabel: fmt.titleCase(t.device_type) || "",
      categoryLabel: fmt.titleCase(t.event_category) || "",
      eventNameLabel: fmt.titleCase(t.event_name) || "",
      pageTitle: decodeTimelineValue(t.page_title),
      pageUrl: compactTimelineUrl(decodeTimelineValue(t.page_url)),
      pageUrlHref: safeTimelineUrl(t.page_url),
      referrerLabel: compactTimelineUrl(decodeTimelineValue(t.referrer_url)),
      eventIdLabel: t.event_id ? fmt.shortId(t.event_id) : "",
      rawProfileIdLabel: t.raw_profile_id ? fmt.shortId(t.raw_profile_id) : "",
      eventDataLabel: timelineJsonLabel(t.event_data),
    };
  }

  function buildDetailVm(
    profile,
    engagement,
    channelActivity,
    topInterests,
    timeline,
    profileLinks,
    persona,
    personaHistory,
    domainProfiles,
  ) {
    // Real (plaintext) name wins when available; hashed domains (banking)
    // fall back to the AI-computed persona name, then a generic label.
    var realName = fmt.realName(profile);
    var displayName =
      realName ||
      profile.persona_name ||
      "Profile " + fmt.shortId(profile.master_profile_id);

    // Channels & Identifiers card now focuses on activation-reachable channels only.
    var channels = [];
    if (profile.email)
      channels.push({
        icon: "✉️",
        label: "Email",
        badge: fmt.maskMiddle(profile.email),
      });
    if (profile.phone_number)
      channels.push({
        icon: "☎️",
        label: "Phone",
        badge: fmt.maskMiddle(profile.phone_number),
      });
    if (profile.push_tokens && Object.keys(profile.push_tokens).length)
      channels.push({
        icon: "🔔",
        label: "Push Notifications",
        badge: Object.keys(profile.push_tokens).length + " token(s)",
      });
    if ((profile.device_ids || []).length)
      channels.push({
        icon: "📱",
        label: "Mobile App (In-App)",
        badge: profile.device_ids.length + " device(s)",
      });
    if ((profile.cookie_ids || []).length)
      channels.push({
        icon: "💻",
        label: "Web",
        badge: profile.cookie_ids.length,
      });
    if (profile.preferred_channel)
      channels.push({
        icon: "🎯",
        label: "Preferred Channel",
        badge: fmt.titleCase(profile.preferred_channel),
      });
    if (!channels.length)
      channels.push({
        icon: "—",
        label: "No activation channels available",
        badge: "",
      });

    var attributeChips = [];

    if (profile.attributes) {
      Object.entries(profile.attributes).forEach(function ([key, value]) {
        // Skip empty values
        if (value === null || value === undefined || value === "") {
          return;
        }

        attributeChips.push({
          label: fmt.titleCase(key),
          value: String(value)
            .replace(/_/g, " ")
            .replace(/\b\w/g, (c) => c.toUpperCase()),
        });
      });
    }

    // Identity Details (CIR) surfaces resolution-relevant identity attributes
    // only. Contact channels/technical IDs already live in the Channels &
    // Identifiers card above, so they are intentionally excluded here to
    // avoid duplicate rows. Long/sensitive values are masked or shortened.
    var identityDetailChips = [];
    function addIdentityChip(label, value) {
      if (value === null || value === undefined || value === "") return;
      identityDetailChips.push({ label: label, value: String(value) });
    }

    addIdentityChip(
      "Profile Type",
      profile.is_hashed ? "Hashed (Privacy-Safe)" : "Plain (Direct PII)",
    );
    addIdentityChip("Date of Birth", fmt.date(profile.date_of_birth));
    addIdentityChip("Gender", fmt.titleCase(profile.gender));
    addIdentityChip(
      "National ID",
      profile.national_id ? fmt.maskMiddle(profile.national_id) : null,
    );
    addIdentityChip("Loyalty ID", profile.loyalty_id);
    if ((profile.secondary_emails || []).length)
      addIdentityChip(
        "Secondary Emails",
        profile.secondary_emails.length + " additional",
      );
    if ((profile.secondary_phones || []).length)
      addIdentityChip(
        "Secondary Phones",
        profile.secondary_phones.length + " additional",
      );
    if ((profile.source_systems || []).length)
      addIdentityChip(
        "Source Systems",
        profile.source_systems.map(fmt.titleCase).join(", "),
      );
    if (profile.first_seen_raw_profile_id)
      addIdentityChip(
        "First Seen Raw Profile ID",
        fmt.shortId(profile.first_seen_raw_profile_id),
      );

    var workingDetailChips = [];
    if (profile.company_name) {
      workingDetailChips.push({
        label: "Company Name",
        value: profile.company_name,
      });
    }
    if (profile.institution_name) {
      workingDetailChips.push({
        label: "Institution",
        value: profile.institution_name,
      });
    }

    var addressDetailChips = [];
    if (profile.address && typeof profile.address === "object") {
      Object.entries(profile.address).forEach(function ([key, value]) {
        if (value === null || value === undefined || value === "") {
          return;
        }

        addressDetailChips.push({
          label: fmt.titleCase(key),
          value: String(value),
        });
      });
    }

    var timelineVms = (timeline || []).map(timelineEntryVm);

    var historyVms = (personaHistory || []).map(function (h) {
      return {
        newPersonaName: h.new_persona_name || "—",
        changeReason: h.change_reason || "",
        changedAtLabel: fmt.dateTime(h.changed_at),
      };
    });

    function linkScorePercent(v) {
      if (v === null || v === undefined || v === "") return 0;
      var n = Number(v);
      if (isNaN(n)) return 0;
      var pct = n <= 1 ? n * 100 : n;
      return Math.max(0, Math.min(100, pct));
    }

    function linkScoreLabel(v) {
      var pct = linkScorePercent(v);
      return pct ? Math.round(pct) + "%" : "N/A";
    }

    function linkHasConfidenceScore(link) {
      var method = String(link && link.match_method ? link.match_method : "")
        .trim()
        .toLowerCase();
      if (method === "newmaster" || method === "none") return false;

      var raw = link && link.match_score;
      if (raw === null || raw === undefined || raw === "") return false;

      var n = Number(raw);
      return !isNaN(n);
    }

    function linkStatusBadgeClass(status) {
      var normalized = (status || "").toUpperCase();
      if (normalized === "ACTIVE") return "bg-emerald-100 text-emerald-700";
      if (normalized === "HISTORICAL") return "bg-slate-100 text-slate-700";
      if (normalized === "SUPERSEDED") return "bg-amber-100 text-amber-700";
      if (normalized === "UNLINKED") return "bg-rose-100 text-rose-700";
      return "bg-slate-100 text-slate-700";
    }

    var MATCHING_FIELDS = [
      "email",
      "phone_number",
      "national_id",
      "external_customer_id",
      "device_id",
      "advertising_id",
      "cookie_id",
    ];

    var MATCHING_FIELD_LABELS = {
      email: "Email",
      phone_number: "Phone Number",
      national_id: "National ID",
      external_customer_id: "External Customer ID",
      device_id: "Device ID",
      advertising_id: "Advertising ID",
      cookie_id: "Cookie ID",
    };

    function parseMatchFieldsFromMethod(methodRaw) {
      var raw = String(methodRaw || "").trim();
      if (!raw) return [];

      var spec = "";
      var colonIdx = raw.indexOf(":");
      var openIdx = raw.indexOf("(");
      var closeIdx = raw.lastIndexOf(")");

      if (colonIdx >= 0 && colonIdx < raw.length - 1) {
        spec = raw.slice(colonIdx + 1);
      } else if (openIdx >= 0 && closeIdx > openIdx) {
        spec = raw.slice(openIdx + 1, closeIdx);
      }

      if (!spec) return [];

      var allowed = {};
      MATCHING_FIELDS.forEach(function (f) {
        allowed[f] = true;
      });

      var fields = spec
        .split(/[|,;+\s]+/)
        .map(function (f) {
          return f.trim().toLowerCase();
        })
        .filter(function (f) {
          return !!f && allowed[f];
        });

      return fields.filter(function (f, i) {
        return fields.indexOf(f) === i;
      });
    }

    function fieldsListLabel(fields) {
      return fields.length ? fields.join(", ") : MATCHING_FIELDS.join(", ");
    }

    function formatIdentifierArray(values) {
      var arr = Array.isArray(values) ? values.filter(Boolean) : [];
      if (!arr.length) return "—";
      return arr
        .slice(0, 3)
        .map(function (v) {
          return fmt.maskMiddle(String(v), 4, 3);
        })
        .join(", ");
    }

    function externalCustomerIdEvidenceValue(externalIds) {
      if (!externalIds || typeof externalIds !== "object") return "—";
      if (externalIds.external_customer_id)
        return String(externalIds.external_customer_id);

      var entries = Object.entries(externalIds).filter(function (pair) {
        return pair[1] !== null && pair[1] !== undefined && pair[1] !== "";
      });
      if (!entries.length) return "—";

      return entries
        .slice(0, 3)
        .map(function (pair) {
          return pair[0] + ": " + pair[1];
        })
        .join(", ");
    }

    function matchingFieldValue(field) {
      if (field === "email")
        return profile.email ? fmt.maskMiddle(profile.email) : "—";
      if (field === "phone_number")
        return profile.phone_number
          ? fmt.maskMiddle(profile.phone_number)
          : "—";
      if (field === "national_id")
        return profile.national_id ? fmt.maskMiddle(profile.national_id) : "—";
      if (field === "external_customer_id")
        return externalCustomerIdEvidenceValue(profile.external_ids);
      if (field === "device_id") return formatIdentifierArray(profile.device_ids);
      if (field === "advertising_id")
        return formatIdentifierArray(profile.advertising_ids);
      if (field === "cookie_id") return formatIdentifierArray(profile.cookie_ids);
      return "—";
    }

    function normalizedMethod(link) {
      return String((link && link.match_method) || "")
        .trim()
        .toLowerCase();
    }

    function inferredFieldsForLink(link) {
      var parsed = parseMatchFieldsFromMethod(link && link.match_method);
      if (parsed.length) return parsed;

      var method = normalizedMethod(link);
      if (method === "dynamicmatch" || method === "newmaster") {
        return MATCHING_FIELDS.slice();
      }
      return [];
    }

    function linkReasonLabel(link) {
      var status = (link.status || "").toUpperCase();
      var methodRaw = String(link.match_method || "");
      var method = methodRaw.toLowerCase();
      var parsedFields = parseMatchFieldsFromMethod(methodRaw);

      if (status === "UNLINKED" && link.unlinked_reason) {
        return "Unlinked: " + link.unlinked_reason;
      }
      if (status === "SUPERSEDED") {
        return "Superseded by a newer identity resolution pass";
      }
      if (method === "newmaster") {
        return "Created a new master profile as the best identity resolution outcome";
      }
      if (method === "dynamicmatch") {
        return "Matched using fields: " + fieldsListLabel(MATCHING_FIELDS);
      }
      if (parsedFields.length) {
        return "Matched using fields: " + fieldsListLabel(parsedFields);
      }
      if (method === "exact") {
        return "Exact identifier match";
      }
      if (method === "fuzzy_trgm") {
        return "Fuzzy text similarity match (trigram)";
      }
      if (method === "fuzzy_dmetaphone") {
        return "Phonetic similarity match (double metaphone)";
      }
      if (method === "none") {
        return "Linked by resolver policy";
      }
      if (link.match_score !== null && link.match_score !== undefined) {
        return "Matched using fields: " + fieldsListLabel(MATCHING_FIELDS);
      }
      return "Matched using fields: " + fieldsListLabel(MATCHING_FIELDS);
    }

    var linkedRawProfiles = (profileLinks || []).map(function (l) {
      var scorePct = linkScorePercent(l.match_score);
      var showConfidence = linkHasConfidenceScore(l);
      return {
        linkId: l.link_id,
        rawProfileId: l.raw_profile_id,
        rawProfileIdShort: fmt.shortId(l.raw_profile_id),
        masterProfileId: l.master_profile_id,
        matchMethodLabel: fmt.titleCase(l.match_method || "unknown"),
        matchReasonLabel: linkReasonLabel(l),
        matchScoreLabel: showConfidence ? linkScoreLabel(l.match_score) : "Not applicable",
        matchScoreWidth: scorePct + "%",
        hasMatchConfidence: showConfidence,
        statusLabel: fmt.titleCase(l.status || "unknown"),
        statusBadgeClass: linkStatusBadgeClass(l.status),
        createdAtLabel: fmt.dateTime(l.created_at),
      };
    });

    var fieldUsageCounts = {};
    var fieldImpactScores = {};
    MATCHING_FIELDS.forEach(function (field) {
      fieldUsageCounts[field] = 0;
      fieldImpactScores[field] = 0;
    });

    (profileLinks || []).forEach(function (link) {
      var fields = inferredFieldsForLink(link);
      var linkImpact = linkScorePercent(link && link.match_score);
      if (!linkImpact) linkImpact = 1;

      fields.forEach(function (field) {
        if (fieldUsageCounts[field] !== undefined) fieldUsageCounts[field] += 1;
        if (fieldImpactScores[field] !== undefined)
          fieldImpactScores[field] += linkImpact;
      });
    });

    var matchingEvidenceChips = MATCHING_FIELDS.map(function (field) {
      var count = fieldUsageCounts[field];
      var value = matchingFieldValue(field);
      return {
        field: field,
        label: MATCHING_FIELD_LABELS[field] || fmt.titleCase(field),
        value: value,
        showValue: true,
        isValueMissing: value === "—",
        duplicateHint: null,
        confidenceImpactScore: fieldImpactScores[field] || 0,
        usageLabel:
          count > 0
            ? "Used in " + count + " linked profile" + (count > 1 ? "s" : "")
            : "Not used in current links",
      };
    }).sort(function (a, b) {
      if (b.confidenceImpactScore !== a.confidenceImpactScore) {
        return b.confidenceImpactScore - a.confidenceImpactScore;
      }
      var usageA = fieldUsageCounts[a.field] || 0;
      var usageB = fieldUsageCounts[b.field] || 0;
      if (usageB !== usageA) return usageB - usageA;
      return a.label.localeCompare(b.label);
    });

    var topLinkScore = 0;
    linkedRawProfiles.forEach(function (l) {
      var score = Number(String(l.matchScoreWidth).replace("%", ""));
      if (!isNaN(score) && score > topLinkScore) topLinkScore = score;
    });
    var latestLinkAtLabel = linkedRawProfiles.length
      ? linkedRawProfiles[0].createdAtLabel
      : "—";
    var activeLinkedRawProfileCount = linkedRawProfiles.filter(function (l) {
      return (l.statusLabel || "").toLowerCase() === "active";
    }).length;

    function scoreWidth(v) {
      var n = Number(v);
      return (isNaN(n) ? 0 : Math.max(0, Math.min(100, n))) + "%";
    }

    return {
      master_profile_id: profile.master_profile_id,
      domain: profile.domain,
      displayName: displayName,
      initials: fmt.initials(displayName),
      statusLabel:
        profile.status_code === 1 ? "Active Profile" : "Inactive Profile",
      statusBadgeClass:
        profile.status_code === 1
          ? "bg-green-100 text-green-700"
          : "bg-slate-100 text-slate-600",
      personaName: profile.persona_name || "—",
      acquisitionSource: profile.acquisition_source || "—",
      firstSeenLabel: fmt.date(profile.created_at),
      lastSeenLabel: fmt.dateTime(profile.last_activity_at),
      tierLabel: profile.membership_tier || profile.clv_segment || "—",
      kycStatus: profile.kyc_status || "unknown",
      domainLabel: fmt.domainLabel(profile.domain),
      customerSinceLabel: fmt.date(profile.customer_since),
      lifecycleLabel: fmt.titleCase(profile.lifecycle_stage) || "—",
      personaSummary:
        profile.persona_summary ||
        "Profile in the " +
          fmt.domainLabel(profile.domain) +
          " domain, currently in the '" +
          fmt.titleCase(profile.lifecycle_stage) +
          "' lifecycle stage.",
      channels: channels,
      hasIdentityDetails: identityDetailChips.length > 0,
      identityDetailChips: identityDetailChips,
      hasMatchingEvidence: matchingEvidenceChips.length > 0,
      matchingEvidenceChips: matchingEvidenceChips,
      hasAttributes: attributeChips.length > 0,
      attributeChips: attributeChips,
      hasWorkingInfo: workingDetailChips.length > 0,
      workingDetailChips: workingDetailChips,
      hasAddressDetails: addressDetailChips.length > 0,
      addressDetailChips: addressDetailChips,

      // Check if the communication_preferences object exists and has at least one key
      hasCommunicationPreferences: Object.keys(profile.communication_preferences || {}).length > 0,

      // Default to an empty object instead of an empty array since the data structure is JSON
      communicationPreferences: profile.communication_preferences || {},

      hasTags: (profile.segmentation_tags || []).length > 0,
      segmentationTags: profile.segmentation_tags || [],
      hasInterests: (topInterests || []).length > 0,
      topInterests: topInterests || [],

      periodDays: engagement.period_days,
      engagementScoreLabel: fmt.score(profile.engagement_score),
      totalLogins: fmt.int(engagement.total_logins),
      totalTransactions: fmt.int(engagement.total_transactions),
      totalSpentLabel: fmt.money(engagement.total_spent, engagement.currency),
      avgTransactionLabel: fmt.money(
        engagement.avg_transaction_amount,
        engagement.currency,
      ),
      lastInteractionLabel: fmt.dateTime(engagement.last_interaction_at),

      appSessions: fmt.int(channelActivity.app_sessions),
      webSessions: fmt.int(channelActivity.web_sessions),
      customerServiceContacts: fmt.int(
        channelActivity.customer_service_contacts,
      ),
      channelTransactions: fmt.int(channelActivity.transactions),

      hasTimeline: timelineVms.length > 0,
      timeline: timelineVms,

      hasLinkedRawProfiles: linkedRawProfiles.length > 0,
      linkedRawProfiles: linkedRawProfiles,
      linkedRawProfileCount: linkedRawProfiles.length,
      activeLinkedRawProfileCount: activeLinkedRawProfileCount,
      topLinkScoreLabel: topLinkScore ? Math.round(topLinkScore) + "%" : "N/A",
      latestLinkAtLabel: latestLinkAtLabel,

      lead_grade: profile.lead_grade || "—",
      leadScoreLabel: fmt.percent(profile.lead_conversion_probability),
      churn_risk_tier: profile.churn_risk_tier || "—",
      churnTextClass:
        profile.churn_risk_tier === "high" ||
        profile.churn_risk_tier === "critical"
          ? "text-red-600"
          : "text-slate-400",
      churnScoreLabel: fmt.percent(profile.churn_probability),
      predictiveClvLabel: fmt.money(profile.predictive_clv, ""),
      historicalClvLabel: fmt.money(profile.historical_clv, ""),
      completenessLabel:
        profile.profile_completeness_score !== null &&
        profile.profile_completeness_score !== undefined
          ? Number(profile.profile_completeness_score).toFixed(0) + "%"
          : "—",
      identityConfidenceLabel: fmt.score(profile.identity_confidence_score),
      scoresUpdatedLabel: fmt.dateTime(profile.scores_updated_at),

      // Customer Persona card (AI-native Persona Resolution Engine).
      hasPersona: !!persona,
      personaId: persona ? persona.persona_id : null,
      personaName: (persona && persona.persona_name) || displayName,
      // Real (plaintext) name shown alongside the persona name when available.
      hasRealName: !!realName,
      realName: realName,
      personaCategory: (persona && persona.persona_category) || fmt.domainLabel(profile.domain),
      computedVersion: persona ? persona.computed_version : null,
      customerValueTierLabel: persona ? fmt.titleCase(persona.customer_value_tier) : "—",
      riskLevelLabel: persona ? fmt.titleCase(persona.risk_level) : "—",
      riskLevelBadgeClass: persona ? fmt.churnBadgeClass(persona.risk_level) : "bg-slate-100 text-slate-600",
      nextBestAction: (persona && persona.next_best_action) || "—",
      personaScoreLabel: persona ? fmt.score(persona.persona_score) : "—",
      personaScoreWidth: scoreWidth(persona && persona.persona_score),
      behaviorScoreLabel: persona ? fmt.score(persona.behavior_score) : "—",
      behaviorScoreWidth: scoreWidth(persona && persona.behavior_score),
      engagementScoreLabel: persona ? fmt.score(persona.engagement_score) : "—",
      engagementScoreWidth: scoreWidth(persona && persona.engagement_score),
      financialScoreLabel: persona ? fmt.score(persona.financial_score) : "—",
      financialScoreWidth: scoreWidth(persona && persona.financial_score),
      loyaltyScoreLabel: persona ? fmt.score(persona.loyalty_score) : "—",
      loyaltyScoreWidth: scoreWidth(persona && persona.loyalty_score),
      relationshipScoreLabel: persona ? fmt.score(persona.relationship_score) : "—",
      relationshipScoreWidth: scoreWidth(persona && persona.relationship_score),
      riskScoreLabel: persona ? fmt.score(persona.risk_score) : "—",
      riskScoreWidth: scoreWidth(persona && persona.risk_score),
      confidenceScoreLabel: persona ? fmt.percent(persona.confidence_score) : "—",
      llmProviderLabel: persona ? fmt.titleCase(persona.llm_provider) : "—",
      computedAtLabel: persona ? fmt.dateTime(persona.computed_at) : "—",
      hasHistory: historyVms.length > 0,
      history: historyVms,

      hasDomainProfiles: (domainProfiles || []).length > 0,
      domainProfiles: domainAttributesVm(domainProfiles || []),
    };
  }

  // Flattens each cdp_domain_profiles row's domain_attributes JSONB into
  // display-ready {label, value} rows for the Domain Attributes card.
  function domainAttributesVm(domainProfiles) {
    return domainProfiles.map(function (dp) {
      var entries = Object.keys(dp.domain_attributes || {}).map(function (key) {
        var value = dp.domain_attributes[key];
        return {
          label: fmt.titleCase(key.replace(/_/g, " ")),
          value: Array.isArray(value) ? value.join(", ") : String(value),
        };
      });
      return {
        domain_profile_id: dp.domain_profile_id,
        domainLabel: fmt.domainLabel(dp.domain_code),
        hasAttributes: entries.length > 0,
        attributes: entries,
      };
    });
  }

  // Persona endpoints 404 when no persona has been computed yet for this
  // profile -- treat that as "no persona" (null/[]) rather than a hard
  // failure so it never blocks the rest of the profile detail page load.
  function loadPersona(masterProfileId) {
    return api("/master-profiles/" + masterProfileId + "/persona").then(
      function (persona) {
        return persona;
      },
      function () {
        return null;
      },
    );
  }

  function loadPersonaHistory(masterProfileId) {
    return api("/master-profiles/" + masterProfileId + "/persona-history", {
      limit: 5,
    }).then(
      function (history) {
        return history;
      },
      function () {
        return [];
      },
    );
  }

  function loadProfileLinks(masterProfileId) {
    return api("/master-profiles/" + masterProfileId + "/links", {
      limit: 12,
    }).then(
      function (links) {
        return links || [];
      },
      function () {
        return [];
      },
    );
  }

  function loadDomainProfiles(masterProfileId) {
    return api("/master-profiles/" + masterProfileId + "/domain-profiles").then(
      function (domainProfiles) {
        return domainProfiles || [];
      },
      function () {
        return [];
      },
    );
  }

  function escapeHtml(value) {
    return $("<div>").text(String(value)).html();
  }

  function modalValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function modalRow(label, value) {
    return (
      '<div class="flex items-start justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">' +
      '<span class="text-[11px] font-semibold uppercase tracking-wider text-slate-500">' +
      escapeHtml(label) +
      "</span>" +
      '<span class="text-xs font-medium text-slate-800 text-right break-all">' +
      escapeHtml(modalValue(value)) +
      "</span></div>"
    );
  }

  function setModalLoading() {
    $("#linked-raw-modal-loading").removeClass("hidden");
    $("#linked-raw-modal-error").addClass("hidden").text("");
    $("#linked-raw-modal-content").addClass("hidden");
    $("#linked-raw-modal-title").text("Raw Profile Detail");
    $("#linked-raw-modal-subtitle").text("Fetching latest linked profile data...");
  }

  function showLinkedRawModalError(message) {
    $("#linked-raw-modal-loading").addClass("hidden");
    $("#linked-raw-modal-content").addClass("hidden");
    $("#linked-raw-modal-error").removeClass("hidden").text(message);
  }

  function renderLinkedRawModal(detail) {
    var link = detail && detail.link ? detail.link : {};
    var raw = detail && detail.raw_profile ? detail.raw_profile : {};
    var method = String(link.match_method || "").trim().toLowerCase();
    var hasNumericScore =
      link.match_score !== null && link.match_score !== undefined && link.match_score !== "" && !isNaN(Number(link.match_score));
    var showConfidence = hasNumericScore && method !== "newmaster" && method !== "none";

    $("#linked-raw-modal-loading").addClass("hidden");
    $("#linked-raw-modal-error").addClass("hidden").text("");

    $("#linked-raw-modal-title").text(
      "Raw Profile " + (raw.raw_profile_id ? fmt.shortId(raw.raw_profile_id) : "—"),
    );
    $("#linked-raw-modal-subtitle").text(
      "Linked at " + fmt.dateTime(link.created_at) + " via " + fmt.titleCase(link.match_method || "unknown"),
    );

    $("#linked-raw-modal-status").text(fmt.titleCase(link.status || "unknown"));
    $("#linked-raw-modal-score").text(
      showConfidence
        ? Math.round((Number(link.match_score) <= 1 ? Number(link.match_score) * 100 : Number(link.match_score))) + "%"
        : "Not applicable",
    );
    $("#linked-raw-modal-source").text(fmt.titleCase(raw.source_system || "unknown"));

    var identityFieldsHtml = [
      modalRow("Raw Profile ID", raw.raw_profile_id),
      modalRow("External Customer ID", raw.external_customer_id),
      modalRow("Full Name", raw.full_name),
      modalRow("Email", raw.email),
      modalRow("Phone Number", raw.phone_number),
      modalRow("National ID", raw.national_id),
      modalRow("Date of Birth", fmt.date(raw.date_of_birth)),
      modalRow("Address", [raw.address_line1, raw.address_line2, raw.city, raw.state_province, raw.postal_code, raw.country].filter(Boolean).join(", ")),
      modalRow("Created At", fmt.dateTime(raw.created_at)),
      modalRow("Processed At", fmt.dateTime(raw.processed_at)),
    ].join("");

    var technicalFieldsHtml = [
      modalRow("Domain", fmt.domainLabel(raw.domain)),
      modalRow("Channel", fmt.titleCase(raw.channel)),
      modalRow("Device ID", raw.device_id),
      modalRow("Advertising ID", raw.advertising_id),
      modalRow("Cookie ID", raw.cookie_id),
      modalRow("Session ID", raw.session_id),
      modalRow("GA Client ID", raw.ga_client_id),
      modalRow("IP Address", raw.ip_address),
      modalRow("UTM Source", raw.utm_source),
      modalRow("UTM Medium", raw.utm_medium),
      modalRow("UTM Campaign", raw.utm_campaign),
      modalRow("Event Name", raw.event_name),
      modalRow("Event Time", fmt.dateTime(raw.event_time)),
    ].join("");

    $("#linked-raw-modal-identity-fields").html(identityFieldsHtml);
    $("#linked-raw-modal-technical-fields").html(technicalFieldsHtml);

    var payloadText = raw.event_payload
      ? JSON.stringify(raw.event_payload, null, 2)
      : "{}";
    $("#linked-raw-modal-event-payload").text(payloadText);

    $("#linked-raw-modal-content").removeClass("hidden");
  }

  function openLinkedRawModal(rawProfileId) {
    if (!currentProfileId || !rawProfileId) return;

    var $modal = $("#linked-raw-profile-modal");
    if (!$modal.length) return;

    setModalLoading();
    $modal.removeClass("hidden");
    $("body").addClass("overflow-hidden");

    api(
      "/master-profiles/" +
        currentProfileId +
        "/linked-raw-profiles/" +
        rawProfileId,
    )
      .done(function (detail) {
        renderLinkedRawModal(detail || {});
      })
      .fail(function (xhr) {
        showLinkedRawModalError(
          "Could not load linked raw profile details for this identity link.",
        );
        showApiError("loading linked raw profile detail", xhr);
      });
  }

  function closeLinkedRawModal() {
    $("#linked-raw-profile-modal").addClass("hidden");
    $("body").removeClass("overflow-hidden");
  }

  function loadContentItems(masterProfileId, itemType) {
    var params = { master_profile_id: masterProfileId, limit: 8 };
    if (itemType) params.item_type = itemType;
    api("/content-items/recommended", params)
      .done(function (items) {
        var vms = items.map(function (it) {
          return $.extend({}, it, {
            publishedLabel: fmt.date(it.published_at),
            ctaLabelOrDefault: it.cta_label || "View",
          });
        });
        $("#content-items-list").html(
          C360.templates.render("content-items", {
            hasItems: vms.length > 0,
            items: vms,
          }),
        );
      })
      .fail(function (xhr) {
        showApiError("loading personalized items", xhr);
      });
  }

  function requestTimeline() {
    if (!currentProfileId || timelineRequestLoading) return;
    timelineRequestLoading = true;
    updateTimelineLoadingState();
    api("/master-profiles/" + currentProfileId + "/timeline", timelineRequestParams())
      .done(renderTimeline)
      .fail(function (xhr) {
        showApiError("loading profile timeline", xhr);
      })
      .always(function () {
        timelineRequestLoading = false;
        updateTimelineLoadingState();
      });
  }

  function renderTimeline(timeline) {
    var vms = (timeline || []).map(timelineEntryVm);
    var html = vms
        .map(function (t) {
          var badges =
            timelineBadgeHtml(t.sourceLabel, "bg-indigo-50 text-indigo-700") +
            timelineBadgeHtml(t.domainLabel, "bg-slate-100 text-slate-600") +
            timelineBadgeHtml(t.categoryLabel, "bg-amber-50 text-amber-700") +
            timelineBadgeHtml(t.eventNameLabel, "bg-emerald-50 text-emerald-700") +
            timelineBadgeHtml(t.deviceTypeLabel, "bg-sky-50 text-sky-700");
          var pageDetails = "";
          if (t.pageTitle || t.pageUrl) {
            pageDetails +=
              '<div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600">' +
              '<span class="font-semibold text-slate-500">Page</span>' +
              (t.pageTitle
                ? '<span class="font-medium">' + escapeTimelineText(t.pageTitle) + "</span>"
                : "") +
              (t.pageUrlHref
                ? '<a class="max-w-full truncate text-indigo-600 hover:text-indigo-800 hover:underline" href="' +
                  escapeTimelineText(t.pageUrlHref) +
                  '" target="_blank" rel="noopener noreferrer" title="Open page">' +
                  escapeTimelineText(t.pageUrl) +
                  "</a>"
                : t.pageUrl
                  ? '<span class="max-w-full truncate text-slate-500" title="Page URL">' +
                    escapeTimelineText(t.pageUrl) +
                    "</span>"
                  : "") +
              "</div>";
          }
          if (t.referrerLabel) {
            pageDetails +=
              '<div class="mt-1 max-w-full truncate text-xs text-slate-400" title="Referrer">From ' +
              escapeTimelineText(t.referrerLabel) +
              "</div>";
          }
          var lineage = [];
          if (t.eventIdLabel) lineage.push("Event " + t.eventIdLabel);
          if (t.rawProfileIdLabel) lineage.push("Raw profile " + t.rawProfileIdLabel);
          if (t.dataSourceIdLabel) lineage.push("Source " + t.dataSourceIdLabel);
          var lineageHtml = lineage.length
            ? '<div class="mt-2 text-[11px] text-slate-400">' +
              lineage.map(escapeTimelineText).join(" &middot; ") +
              "</div>"
            : "";
          var eventDataHtml = t.eventDataLabel
            ? '<details class="mt-2 rounded-lg border border-slate-200 bg-white/70 px-2.5 py-1.5 text-xs text-slate-600">' +
              '<summary class="cursor-pointer select-none font-medium text-slate-500">Event data</summary>' +
              '<code class="mt-1 block max-w-full overflow-x-auto whitespace-pre-wrap break-words text-[11px] text-slate-600">' +
              escapeTimelineText(t.eventDataLabel) +
              "</code></details>"
            : "";
          return (
            '<li class="relative flex items-start group">' +
            '<div class="absolute -left-6 mt-1.5 w-3.5 h-3.5 rounded-full bg-white border-2 border-indigo-500 ring-4 ring-white group-hover:scale-125 group-hover:border-indigo-600 transition-all"></div>' +
            '<article class="w-full flex items-start justify-between gap-3 p-3.5 bg-slate-50/50 hover:bg-slate-50 rounded-xl border border-slate-100/80 transition-all">' +
            '<div class="min-w-0 flex-1">' +
            '<div class="text-sm font-semibold text-slate-800 flex items-center gap-2">' +
            '<span class="text-base">' +
            escapeTimelineText(t.icon) +
            '</span><span>' +
            escapeTimelineText(t.title) +
            "</span></div>" +
            '<div class="text-xs text-slate-400 mt-1 flex flex-wrap items-center gap-1.5"><span>' +
            escapeTimelineText(t.timeLabel) +
            '</span><span class="text-slate-300">&bull;</span><span class="font-medium text-slate-500">' +
            escapeTimelineText(t.channelLabel) +
            "</span></div>" +
            (badges ? '<div class="mt-2 flex flex-wrap gap-1.5">' + badges + "</div>" : "") +
            pageDetails +
            eventDataHtml +
            lineageHtml +
            "</div>" +
            (t.amountLabel
              ? '<span class="text-xs font-semibold bg-white border border-slate-200/80 text-slate-700 rounded-lg px-2.5 py-1 whitespace-nowrap shadow-sm">' +
                escapeTimelineText(t.amountLabel) +
                "</span>"
              : "") +
            "</article></li>"
          );
        })
        .join("");
    $("#profile-timeline-list").html(html);
    $("#profile-timeline-list").toggleClass("hidden", vms.length === 0);
    $("#profile-timeline-empty").toggleClass("hidden", vms.length > 0);
  }

  function reloadTimeline() {
    requestTimeline();
  }

  function load(masterProfileId) {
    closeLinkedRawModal();
    currentProfileId = masterProfileId;
    currentContentType = "";
    timelineLimit = 100;
    timelineDataSourceId = "";
    timelineRangePreset = "7d";
    timelineFromEventTime = "";
    timelineToEventTime = "";
    initializeTimelineRange(true);
    $(".content-tab-btn")
      .removeClass("bg-indigo-600 text-white")
      .addClass("bg-slate-100");
    $(".content-tab-btn[data-type='']")
      .removeClass("bg-slate-100")
      .addClass("bg-indigo-600 text-white");
    $("#detail-content").empty();
    $("#detail-loading").removeClass("hidden");

    var days = periodDays();
    $.when(
      api("/master-profiles/" + masterProfileId),
      api("/master-profiles/" + masterProfileId + "/engagement-summary", {
        days: days,
      }),
      api("/master-profiles/" + masterProfileId + "/channel-activity", {
        days: days,
      }),
      api("/master-profiles/" + masterProfileId + "/top-interests", {
        limit: 5,
      }),
      api("/master-profiles/" + masterProfileId + "/timeline", timelineRequestParams()),
      loadProfileLinks(masterProfileId),
      loadPersona(masterProfileId),
      loadPersonaHistory(masterProfileId),
      loadDomainProfiles(masterProfileId),
    )
      .done(
        function (
          profileRes,
          engagementRes,
          channelRes,
          interestsRes,
          timelineRes,
          profileLinks,
          persona,
          personaHistory,
          domainProfiles,
        ) {
          var vm = buildDetailVm(
            profileRes[0],
            engagementRes[0],
            channelRes[0],
            interestsRes[0],
            timelineRes[0],
            profileLinks,
            persona,
            personaHistory,
            domainProfiles,
          );
          $("#detail-loading").addClass("hidden");
          $("#detail-content").html(
            C360.templates.render("profile-details", vm),
          );
          initializeTimelineRange(false);
          populateDomainAttributeDomainSelect(profileRes[0].domain);
          loadTimelineDataSources();
          loadContentItems(masterProfileId, "");
        },
      )
      .fail(function (xhr) {
        $("#detail-loading").addClass("hidden");
        showApiError("loading profile detail", xhr);
      });
  }

  function reload() {
    if (currentProfileId) load(currentProfileId);
  }

  function bindEvents() {
    $(document).on("click", ".btn-copy-id", function () {
      var val = $(this).data("value");
      navigator.clipboard && navigator.clipboard.writeText(String(val));
      var btn = $(this);
      btn.text("copied!");
      setTimeout(function () {
        btn.text("copy");
      }, 1200);
    });

    $(document).off("change.profileTimeline", "#timeline-data-source-filter");
    $(document).on("change.profileTimeline", "#timeline-data-source-filter", function () {
      timelineDataSourceId = String($(this).val() || "");
      if (currentProfileId) reloadTimeline();
    });

    $(document).off("click.profileTimeline", "#btn-timeline-apply");
    $(document).on("click.profileTimeline", "#btn-timeline-apply", function () {
      if (readTimelineRange() && currentProfileId) reloadTimeline();
    });

    $(document).off("click.profileTimeline", "#btn-timeline-reset");
    $(document).on("click.profileTimeline", "#btn-timeline-reset", function () {
      setTimelineRangePreset("7d");
    });

    $(document).off("click.profileTimeline", ".timeline-range-preset");
    $(document).on("click.profileTimeline", ".timeline-range-preset", function () {
      setTimelineRangePreset(String($(this).data("range") || ""));
    });

    $(document).off("input.profileTimeline", "#timeline-from-event-time, #timeline-to-event-time");
    $(document).on("input.profileTimeline", "#timeline-from-event-time, #timeline-to-event-time", function () {
      timelineRangePreset = "custom";
      $("#timeline-range-error").addClass("hidden").text("");
      updateTimelineRangeUi(true);
    });

    $(document).on("submit", "#domain-attribute-form", function (e) {
      e.preventDefault();
      submitDomainAttributeForm();
    });

    $(document).on("click", ".btn-linked-raw-detail", function () {
      openLinkedRawModal($(this).data("raw-profile-id"));
    });

    $(document).on("click", "#btn-close-linked-raw-modal", closeLinkedRawModal);

    $(document).on("click", "#linked-raw-profile-modal", function (e) {
      if (e.target === this) closeLinkedRawModal();
    });

    $(document).on("keydown", function (e) {
      if (e.key === "Escape" && !$("#linked-raw-profile-modal").hasClass("hidden")) {
        closeLinkedRawModal();
      }
    });

    $(document).on("click", ".content-tab-btn", function () {
      // 1. Reset all tabs to the INACTIVE state
      // Removes the active white background/blue text and adds the gray text with hover effects
      $(".content-tab-btn")
        .removeClass("bg-white text-indigo-600 shadow-sm font-semibold")
        .addClass("text-slate-500 font-medium hover:text-slate-700 hover:bg-slate-200/50");

      // 2. Set the clicked tab to the ACTIVE state
      // Removes the gray text/hover effects and adds the active white background/blue text
      $(this)
        .removeClass("text-slate-500 font-medium hover:text-slate-700 hover:bg-slate-200/50")
        .addClass("bg-white text-indigo-600 shadow-sm font-semibold");

      // 3. Execute your existing content loading logic
      currentContentType = $(this).data("type") || "";
      
      if (currentProfileId) {
        loadContentItems(currentProfileId, currentContentType);
      }
    });

  }

  // Owns the "/profiles/:id" detail route (see router.js). navigate()
  // already updates location.hash before mount() runs, so `load` no
  // longer needs to write location.hash itself.
  C360.router.define("/profiles/:id", {
    section: "view-detail",
    tab: "profiles",
    mount: function (params) {
      load(params.id);
    },
  });

  C360.profileDetailView = { load: load, reload: reload, bindEvents: bindEvents };
})(window.C360);
