/* Customer 360 Admin -- Profile Detail view (dashboard). */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var currentProfileId = null;
  var currentContentType = "";
  var timelineLimit = 8;

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

  function periodDays() {
    return parseInt($("#data-period-select").val(), 10) || 90;
  }

  function timelineEntryVm(t) {
    var icon =
      t.kind === "transaction"
        ? "💳"
        : t.kind === "contact"
          ? "💬"
          : fmt.CATEGORY_ICONS[(t.subtitle || "").toUpperCase()] || "🔔";
    return {
      icon: icon,
      title: t.title,
      channelLabel: fmt.titleCase(t.channel) || "—",
      timeLabel: fmt.dateTime(t.occurred_at),
      amountLabel: t.amount ? fmt.money(t.amount, t.currency) : null,
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

  function loadMoreTimeline() {
    timelineLimit += 8;
    api("/master-profiles/" + currentProfileId + "/timeline", {
      limit: timelineLimit,
    }).done(function (timeline) {
      var vms = (timeline || []).map(timelineEntryVm);
      var html = vms
        .map(function (t) {
          return (
            '<li class="flex gap-3"><div class="w-2 h-2 mt-1.5 rounded-full bg-indigo-500 flex-shrink-0"></div>' +
            '<div class="flex-1 flex items-start justify-between gap-3"><div><div class="text-sm font-medium">' +
            t.icon +
            " " +
            $("<div>").text(t.title).html() +
            "</div>" +
            '<div class="text-xs text-slate-400">' +
            t.timeLabel +
            " &middot; " +
            $("<div>").text(t.channelLabel).html() +
            "</div></div>" +
            (t.amountLabel
              ? '<span class="text-xs bg-slate-100 rounded-full px-2 py-1 whitespace-nowrap">' +
                $("<div>").text(t.amountLabel).html() +
                "</span>"
              : "") +
            "</div></li>"
          );
        })
        .join("");
      $("#detail-content").find("ol").first().html(html);
    });
  }

  function load(masterProfileId) {
    closeLinkedRawModal();
    currentProfileId = masterProfileId;
    currentContentType = "";
    timelineLimit = 8;
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
      api("/master-profiles/" + masterProfileId + "/timeline", {
        limit: timelineLimit,
      }),
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
          populateDomainAttributeDomainSelect(profileRes[0].domain);
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

    $(document).on("click", "#btn-timeline-more", loadMoreTimeline);

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

    $("#data-period-select").on("change", reload);
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
