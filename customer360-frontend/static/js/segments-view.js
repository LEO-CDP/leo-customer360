/* Customer 360 Admin -- Segments (Audience Builder) list + detail view. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var fmt = C360.fmt;
  var api = C360.config.api;
  var showApiError = C360.config.showApiError;

  var currentSegmentId = null;

  function processedByLabel(v) { return v === "ai_agent" ? "AI Agent" : "Human"; }
  function processedByBadgeClass(v) { return v === "ai_agent" ? "bg-purple-100 text-purple-700" : "bg-slate-100 text-slate-600"; }
  function activeLabel(v) { return v ? "Active" : "Inactive"; }
  function activeBadgeClass(v) { return v ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500"; }

  var segmentsById = {};
  var editingSegmentId = null;
  // True once the AI filled the builder in this form session; saved as processed_by='ai_agent'.
  var rulesFromAi = false;
  // "Describe with AI" state for this form session. Untrusted text: render with .text()/.val() only.
  var aiSoFar = null;
  var aiLastQuestion = null;
  var aiRequest = null;
  var aiRequestSequence = 0;
  var aiLockedControls = [];
  var queryBuilderReady = false;
  var segmentAttributes = [];
  var attributeLoadSequence = 0;
  var audienceTagsCatalog = [];
  var audienceTagsByTagName = {};
  var audienceTagsLoaded = false;
  var segmentFormAudienceTags = [];
  var workflowController = new C360.AgentWorkflowController({ api: api });

  function sqlQuote(value) {
    return "'" + String(value == null ? "" : value).replace(/'/g, "''") + "'";
  }

  function sqlValue(value, dataType) {
    var type = String(dataType || "TEXT").toUpperCase();
    if (type === "BOOLEAN" || type === "BOOL") {
      var booleanValue = String(value).trim().toLowerCase();
      if (value !== true && value !== false && booleanValue !== "true" && booleanValue !== "false" && booleanValue !== "1" && booleanValue !== "0") {
        throw new Error("Enter a valid boolean value.");
      }
      return value === true || booleanValue === "true" || booleanValue === "1" ? "TRUE" : "FALSE";
    }
    if (["SMALLINT", "INTEGER", "INT", "BIGINT", "SERIAL", "BIGSERIAL"].indexOf(type) !== -1) {
      var number = Number(value);
      if (!isFinite(number) || !Number.isInteger(number)) throw new Error("Enter a valid integer.");
      return String(number);
    }
    if (["NUMERIC", "DECIMAL", "REAL", "FLOAT", "DOUBLE", "DOUBLE PRECISION", "NUMBER"].indexOf(type) !== -1) {
      var decimal = Number(value);
      if (!isFinite(decimal)) throw new Error("Enter a valid number.");
      return String(decimal);
    }
    if (type === "JSON" || type === "JSONB") {
      try {
        JSON.parse(String(value));
      } catch (error) {
        throw new Error("Enter valid JSON.");
      }
      return sqlQuote(value) + (type === "JSONB" ? "::jsonb" : "::json");
    }
    if (type === "ARRAY") {
      throw new Error("Array attributes are not available for segment rules.");
    }
    if (type === "DATE" || type === "TIME" || type === "TIMESTAMP" || type === "TIMESTAMPTZ" || type === "DATETIME") {
      return sqlQuote(value);
    }
    if (!String(value == null ? "" : value).trim()) {
      throw new Error("Enter a value.");
    }
    if (typeof value === "number" && !isFinite(value)) {
      throw new Error("Enter a valid number.");
    }
    if (typeof value === "number") {
      return String(value);
    }
    return sqlQuote(value);
  }

  var DERIVED_ATTRIBUTES = {
    age_years: {
      id: "age_years",
      field: "date_of_birth",
      name: "Age (years)",
      description: "Calculated from Date of Birth each time the segment is recomputed.",
      attribute_group: "DYNAMIC (RELATIVE TO TODAY)",
      data_type: "INTEGER",
      domain_scope: "all",
      is_pii: false,
      source_type: "date",
      min: 0,
      max: 120
    },
    days_since_last_activity: {
      id: "days_since_last_activity",
      field: "last_activity_at",
      name: "Days since last activity",
      description: "Number of days elapsed since Last Activity At, calculated dynamically.",
      attribute_group: "DYNAMIC (RELATIVE TO TODAY)",
      data_type: "INTEGER",
      domain_scope: "all",
      is_pii: false,
      source_type: "timestamp",
      min: 0,
      max: 36500
    },
    days_as_customer: {
      id: "days_as_customer",
      field: "customer_since",
      name: "Days as customer",
      description: "Number of days since Customer Since date, calculated dynamically.",
      attribute_group: "DYNAMIC (RELATIVE TO TODAY)",
      data_type: "INTEGER",
      domain_scope: "all",
      is_pii: false,
      source_type: "date",
      min: 0,
      max: 36500
    }
  };

  function parseNonNegativeInt(val, label) {
    var num = Number(val);
    if (typeof val === "boolean" || String(val == null ? "" : val).trim() === "" || !isFinite(num) || !Number.isInteger(num) || num < 0) {
      throw new Error("Enter a valid non-negative integer for " + (label || "this rule") + ".");
    }
    return num;
  }

  function derivedRuleSql(rule, derivedSpec) {
    var col = derivedSpec.field;
    var operator = rule.operator;
    var todayExpr = "(now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date";
    if (operator === "is_null") return col + " IS NULL";
    if (operator === "is_not_null") return col + " IS NOT NULL";

    var values = Array.isArray(rule.value) ? rule.value : [rule.value];
    if (derivedSpec.id === "age_years") {
      if (operator === "in" || operator === "not_in") {
        values = values.filter(function (item) { return item !== null && typeof item !== "undefined" && String(item).trim() !== ""; });
        if (!values.length) throw new Error("Enter at least one value for " + derivedSpec.name + ".");
        var ageParts = values.map(function (item) {
          return derivedRuleSql({ operator: "equal", value: item }, derivedSpec);
        });
        return operator === "in" ? "(" + ageParts.join(" OR ") + ")" : "NOT (" + ageParts.join(" OR ") + ")";
      }
      if (operator === "between" || operator === "not_between") {
        if (values.length < 2) throw new Error("Enter two age values for a range rule.");
        var a = parseNonNegativeInt(values[0], derivedSpec.name);
        var b = parseNonNegativeInt(values[1], derivedSpec.name);
        if (a > b) { var tmpAge = a; a = b; b = tmpAge; }
        if (operator === "between") {
          return "(" + col + " <= (" + todayExpr + " - INTERVAL '" + a + " years') AND " +
            col + " > (" + todayExpr + " - INTERVAL '" + (b + 1) + " years'))";
        }
        return "(" + col + " > (" + todayExpr + " - INTERVAL '" + a + " years') OR " +
          col + " <= (" + todayExpr + " - INTERVAL '" + (b + 1) + " years'))";
      }
      var nAge = parseNonNegativeInt(values[0], derivedSpec.name);
      if (operator === "greater_or_equal") return col + " <= (" + todayExpr + " - INTERVAL '" + nAge + " years')";
      if (operator === "greater") return col + " <= (" + todayExpr + " - INTERVAL '" + (nAge + 1) + " years')";
      if (operator === "less") return col + " > (" + todayExpr + " - INTERVAL '" + nAge + " years')";
      if (operator === "less_or_equal") return col + " > (" + todayExpr + " - INTERVAL '" + (nAge + 1) + " years')";
      if (operator === "equal") {
        return "(" + col + " <= (" + todayExpr + " - INTERVAL '" + nAge + " years') AND " +
          col + " > (" + todayExpr + " - INTERVAL '" + (nAge + 1) + " years'))";
      }
      if (operator === "not_equal") {
        return "(" + col + " > (" + todayExpr + " - INTERVAL '" + nAge + " years') OR " +
          col + " <= (" + todayExpr + " - INTERVAL '" + (nAge + 1) + " years'))";
      }
      throw new Error("Unsupported operator for " + derivedSpec.name + ".");
    }

    var baseExpr = derivedSpec.source_type === "timestamp" ? "now()" : todayExpr;
    if (operator === "in" || operator === "not_in") {
      values = values.filter(function (item) { return item !== null && typeof item !== "undefined" && String(item).trim() !== ""; });
      if (!values.length) throw new Error("Enter at least one value for " + derivedSpec.name + ".");
      if (derivedSpec.source_type === "date") {
        var dateExprs = values.map(function (item) {
          var nVal = parseNonNegativeInt(item, derivedSpec.name);
          return "(" + todayExpr + " - INTERVAL '" + nVal + " days')";
        });
        return col + (operator === "in" ? " IN (" : " NOT IN (") + dateExprs.join(", ") + ")";
      }
      var orParts = values.map(function (item) {
        return derivedRuleSql({ operator: "equal", value: item }, derivedSpec);
      });
      return operator === "in" ? "(" + orParts.join(" OR ") + ")" : "NOT (" + orParts.join(" OR ") + ")";
    }
    if (operator === "between" || operator === "not_between") {
      if (values.length < 2) throw new Error("Enter two day values for a range rule.");
      var dLow = parseNonNegativeInt(values[0], derivedSpec.name);
      var dHigh = parseNonNegativeInt(values[1], derivedSpec.name);
      if (dLow > dHigh) { var tmpDay = dLow; dLow = dHigh; dHigh = tmpDay; }
      if (operator === "between") {
        return "(" + col + " <= (" + baseExpr + " - INTERVAL '" + dLow + " days') AND " +
          col + " >= (" + baseExpr + " - INTERVAL '" + dHigh + " days'))";
      }
      return "(" + col + " > (" + baseExpr + " - INTERVAL '" + dLow + " days') OR " +
        col + " < (" + baseExpr + " - INTERVAL '" + dHigh + " days'))";
    }
    var nDays = parseNonNegativeInt(values[0], derivedSpec.name);
    if (operator === "greater") return col + " < (" + baseExpr + " - INTERVAL '" + nDays + " days')";
    if (operator === "greater_or_equal") return col + " <= (" + baseExpr + " - INTERVAL '" + nDays + " days')";
    if (operator === "less") return col + " > (" + baseExpr + " - INTERVAL '" + nDays + " days')";
    if (operator === "less_or_equal") return col + " >= (" + baseExpr + " - INTERVAL '" + nDays + " days')";
    if (operator === "equal") {
      if (derivedSpec.source_type === "date") {
        return col + " = (" + todayExpr + " - INTERVAL '" + nDays + " days')";
      }
      return "(" + col + " <= (" + baseExpr + " - INTERVAL '" + nDays + " days') AND " +
        col + " > (" + baseExpr + " - INTERVAL '" + (nDays + 1) + " days'))";
    }
    if (operator === "not_equal") {
      if (derivedSpec.source_type === "date") {
        return col + " <> (" + todayExpr + " - INTERVAL '" + nDays + " days')";
      }
      return "(" + col + " > (" + baseExpr + " - INTERVAL '" + nDays + " days') OR " +
        col + " <= (" + baseExpr + " - INTERVAL '" + (nDays + 1) + " days'))";
    }
    throw new Error("Unsupported operator for " + derivedSpec.name + ".");
  }

  function ruleSql(rule, filtersById) {
    var ruleKey = rule && (rule.id || rule.field);
    if (!rule || !ruleKey || !filtersById[ruleKey]) throw new Error("Choose a valid profile attribute for every rule.");
    if (DERIVED_ATTRIBUTES[ruleKey]) {
      return derivedRuleSql(rule, DERIVED_ATTRIBUTES[ruleKey]);
    }
    var filter = filtersById[ruleKey];
    var field = filter.field;
    var operator = rule.operator;
    var values;
    if (Array.isArray(rule.value)) {
      values = rule.value;
    } else if ((operator === "in" || operator === "not_in") && typeof rule.value === "string") {
      values = rule.value.split(",").map(function (value) { return $.trim(value); });
    } else {
      values = [rule.value];
    }
    var value;
    var isArrayField = field === "audience_tags" || String(filter.data_type || "").toUpperCase() === "ARRAY";
    if (isArrayField) {
      if (operator === "is_null" || operator === "is_empty") {
        return "(" + field + " IS NULL OR cardinality(" + field + ") = 0)";
      }
      if (operator === "is_not_null" || operator === "is_not_empty") {
        return "(" + field + " IS NOT NULL AND cardinality(" + field + ") > 0)";
      }
      values = values.filter(function (item) {
        return item !== null && typeof item !== "undefined" && String(item).trim() !== "";
      });
      if (!values.length) throw new Error("Select at least one IAB audience tag for this rule.");
      var quotedTags = values.map(function (item) { return sqlQuote($.trim(String(item))); }).join(", ");
      var arrayExpr = "ARRAY[" + quotedTags + "]::text[]";
      if (operator === "in") return field + " && " + arrayExpr;
      if (operator === "equal") return field + " @> " + arrayExpr;
      if (operator === "not_in" || operator === "not_equal") {
        return "NOT (COALESCE(" + field + ", ARRAY[]::text[]) && " + arrayExpr + ")";
      }
      throw new Error("Unsupported array rule operator.");
    }
    if (operator === "is_null") return field + " IS NULL";
    if (operator === "is_not_null") return field + " IS NOT NULL";
    if (operator === "is_empty") return field + " = ''";
    if (operator === "is_not_empty") return field + " <> ''";
    if (operator === "in" || operator === "not_in") {
      values = values.filter(function (item) { return item !== null && typeof item !== "undefined" && String(item).trim() !== ""; });
      if (!values.length) throw new Error("Enter at least one value for each list rule.");
      value = values.map(function (item) { return sqlValue(item, filter.data_type); }).join(", ");
      return field + (operator === "in" ? " IN (" : " NOT IN (") + value + ")";
    }
    if (operator === "between" || operator === "not_between") {
      if (values.length < 2) throw new Error("Enter two values for a range rule.");
      return field + (operator === "between" ? " BETWEEN " : " NOT BETWEEN ") +
        sqlValue(values[0], filter.data_type) + " AND " + sqlValue(values[1], filter.data_type);
    }
    var operators = {
      equal: "=", not_equal: "<>", less: "<", less_or_equal: "<=",
      greater: ">", greater_or_equal: ">=", begins_with: "LIKE",
      contains: "LIKE", ends_with: "LIKE"
    };
    if (!operators[operator]) throw new Error("Unsupported rule operator.");
    var scalar = values[0];
    if (operator === "begins_with") scalar = String(scalar) + "%";
    if (operator === "contains") scalar = "%" + String(scalar) + "%";
    if (operator === "ends_with") scalar = "%" + String(scalar);
    return field + " " + operators[operator] + " " + sqlValue(scalar, filter.data_type);
  }

  function rulesSql(group, filtersById) {
    if (!group || !group.rules || !group.rules.length) throw new Error("Add at least one audience rule.");
    return "(" + group.rules.map(function (rule) {
      return rule.rules ? rulesSql(rule, filtersById) : ruleSql(rule, filtersById);
    }).join(" " + String(group.condition || "AND").toUpperCase() + " ") + ")";
  }

  function ensureAudienceTagsCatalog() {
    if (audienceTagsLoaded && audienceTagsCatalog.length) {
      return $.Deferred().resolve(audienceTagsCatalog).promise();
    }
    return api("/audience-tags/", { limit: 2000, seed_if_empty: true, has_parent: true }).then(
      function (items) {
        audienceTagsCatalog = Array.isArray(items) ? items : [];
        audienceTagsByTagName = {};
        audienceTagsCatalog.forEach(function (item) {
          if (item && item.tag_name) audienceTagsByTagName[item.tag_name] = item;
        });
        audienceTagsLoaded = true;
        return audienceTagsCatalog;
      },
      function () {
        return audienceTagsCatalog;
      }
    );
  }

  function buildAudienceTagSelectValues(extraRules) {
    var valuesMap = {};
    audienceTagsCatalog.forEach(function (item) {
      if (!item || !item.tag_name) return;
      var condensed = String(item.condensed_name || item.tag_name).replace(/\s*\|\s*$/, "");
      valuesMap[item.tag_name] = condensed + " (" + item.tag_name + ")";
    });
    segmentFormAudienceTags.forEach(function (tagName) {
      if (tagName && !valuesMap[tagName]) valuesMap[tagName] = tagName;
    });
    function collectRuleTags(node) {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node.rules)) {
        node.rules.forEach(collectRuleTags);
        return;
      }
      if ((node.id === "audience_tags" || node.field === "audience_tags") && node.value) {
        var list = Array.isArray(node.value) ? node.value : String(node.value).split(",");
        list.forEach(function (raw) {
          var t = $.trim(String(raw || ""));
          if (t && !valuesMap[t]) valuesMap[t] = t;
        });
      }
    }
    collectRuleTags(extraRules);
    if (!Object.keys(valuesMap).length) {
      valuesMap["demographic"] = "Demographic (demographic)";
    }
    return valuesMap;
  }

  function queryBuilderFilters(attributes, extraRules) {
    return attributes.filter(function (attribute) {
      if (attribute && attribute.field === "audience_tags") return true;
      return $.fn.queryBuilder.catalogType(attribute.data_type).category !== "array";
    }).map(function (attribute) {
      if (attribute.field === "audience_tags") {
        return {
          id: "audience_tags",
          field: "audience_tags",
          label: attribute.name || "IAB Audience Tags (audience_tags)",
          type: "string",
          input: "select",
          multiple: true,
          data_type: "ARRAY",
          values: buildAudienceTagSelectValues(extraRules),
          operators: ["in", "not_in", "equal", "not_equal", "is_null", "is_not_null"],
          optgroup: attribute.attribute_group || "MARKETING"
        };
      }
      var typeInfo = $.fn.queryBuilder.catalogType(attribute.data_type);
      var operators;
      if (typeInfo.category === "integer" || typeInfo.category === "number") {
        operators = ["equal", "not_equal", "less", "less_or_equal", "greater", "greater_or_equal", "between", "not_between", "in", "not_in", "is_null", "is_not_null"];
      } else if (typeInfo.category === "datetime") {
        operators = ["equal", "not_equal", "less", "less_or_equal", "greater", "greater_or_equal", "between", "not_between", "in", "not_in", "is_null", "is_not_null"];
      } else if (typeInfo.category === "boolean") {
        operators = ["equal", "not_equal", "is_null", "is_not_null"];
      } else if (typeInfo.category === "json") {
        operators = ["equal", "not_equal", "is_null", "is_not_null"];
      } else {
        operators = ["equal", "not_equal", "contains", "begins_with", "ends_with", "is_empty", "is_not_empty", "is_null", "is_not_null", "in", "not_in"];
      }
      var filterId = attribute.id || attribute.field;
      var filter = $.extend({}, typeInfo, {
        id: filterId,
        field: attribute.field,
        label: attribute.name || filterId,
        data_type: attribute.data_type || "TEXT",
        value_separator: ",",
        operators: operators,
        optgroup: attribute.attribute_group
      });
      if (DERIVED_ATTRIBUTES[filterId]) {
        var dSpec = DERIVED_ATTRIBUTES[filterId];
        filter.validation = $.extend({}, filter.validation, {
          min: dSpec.min,
          max: dSpec.max,
          step: 1
        });
      }
      var isDateField = filterId === attribute.field && (
        String(attribute.data_type || "").trim().toUpperCase() === "DATE" ||
        attribute.field === "last_activity_at"
      );
      if (isDateField) {
        filter.type = "date";
        filter.input = "date";
        filter.placeholder = "YYYY-MM-DD";
        filter.validation = $.extend({}, filter.validation, {
          format: "YYYY-MM-DD"
        });
      }
      if (filterId === "status_code") {
        filter.type = "integer";
        filter.input = "select";
        filter.values = { 1: "Active", 0: "Inactive" };
        filter.operators = ["equal", "not_equal", "is_null", "is_not_null"];
      }
      return filter;
    });
  }

  var FIELD_TO_DERIVED_ID = {
    date_of_birth: "age_years",
    last_activity_at: "days_since_last_activity",
    customer_since: "days_as_customer"
  };

  var INVERT_OPERATOR = {
    less: "greater",
    less_or_equal: "greater_or_equal",
    greater: "less",
    greater_or_equal: "less_or_equal",
    equal: "equal",
    not_equal: "not_equal"
  };

  function convertLeafRule(rule) {
    if (!rule || typeof rule !== "object") return rule;
    var rawId = String(rule.id || rule.field || "").trim();
    var rawField = String(rule.field || rawId).trim();
    if (DERIVED_ATTRIBUTES[rawId]) {
      var dSpec = DERIVED_ATTRIBUTES[rawId];
      var dVal = rule.value;
      if (Array.isArray(dVal)) {
        dVal = dVal.map(function (v) { return parseInt(v, 10); });
      } else if (dVal !== null && typeof dVal !== "undefined" && String(dVal).trim() !== "") {
        dVal = parseInt(dVal, 10);
      }
      return $.extend({}, rule, {
        id: rawId,
        field: dSpec.field,
        type: "integer",
        input: "number",
        value: dVal
      });
    }
    if (typeof rule.value === "string" && FIELD_TO_DERIVED_ID[rawField]) {
      var m = $.trim(rule.value).match(/^[+-]\s*(\d+)\s+(days?|years?)$/i);
      if (m) {
        var derivedId = FIELD_TO_DERIVED_ID[rawField];
        var spec = DERIVED_ATTRIBUTES[derivedId];
        var amount = parseInt(m[1], 10);
        var unit = m[2].toLowerCase();
        var op = String(rule.operator || "equal").toLowerCase();
        var invOp = INVERT_OPERATOR[op] || op;
        if (derivedId === "age_years") {
          var years = unit.indexOf("year") === 0 ? amount : Math.round(amount / 365.25);
          if (invOp === "greater") invOp = "greater_or_equal";
          else if (invOp === "less_or_equal") { invOp = "less"; years = Math.max(0, years - 1); }
          return {
            id: derivedId,
            field: spec.field,
            type: "integer",
            input: "number",
            operator: invOp,
            value: years
          };
        }
        var days = unit.indexOf("year") === 0 ? amount * 365 : amount;
        return {
          id: derivedId,
          field: spec.field,
          type: "integer",
          input: "number",
          operator: invOp,
          value: days
        };
      }
    }
    var id = rule.id || rule.field;
    var normalizedRule = $.extend({}, rule, { id: id, field: rule.field || id });
    if (id === "audience_tags" && typeof normalizedRule.value === "string") {
      normalizedRule.value = normalizedRule.value.split(",").map(function (v) { return $.trim(v); }).filter(Boolean);
    }
    return normalizedRule;
  }

  function normalizeSegmentRules(rules) {
    if (!rules || typeof rules !== "object") return rules;
    var condition = String(rules.condition || "AND").toUpperCase();
    var converted = (rules.rules || []).map(function (rule) {
      if (rule && rule.rules) return normalizeSegmentRules(rule);
      return convertLeafRule(rule);
    });
    if (condition === "AND") {
      var byDerived = {};
      converted.forEach(function (item, idx) {
        if (item && !item.rules && DERIVED_ATTRIBUTES[item.id]) {
          if (!byDerived[item.id]) byDerived[item.id] = [];
          byDerived[item.id].push({ idx: idx, rule: item });
        }
      });
      var toRemove = {};
      var replacements = {};
      Object.keys(byDerived).forEach(function (dId) {
        var pair = byDerived[dId];
        if (pair.length !== 2) return;
        var ops = {};
        ops[pair[0].rule.operator] = pair[0].rule.value;
        ops[pair[1].rule.operator] = pair[1].rule.value;
        var low = null;
        var high = null;
        if (dId === "age_years") {
          if (ops.greater_or_equal !== undefined && ops.less !== undefined) {
            low = parseInt(ops.greater_or_equal, 10);
            high = parseInt(ops.less, 10) - 1;
          } else if (ops.greater_or_equal !== undefined && ops.less_or_equal !== undefined) {
            low = parseInt(ops.greater_or_equal, 10);
            high = parseInt(ops.less_or_equal, 10);
          }
        } else {
          var lowOp = ops.greater_or_equal !== undefined ? "greater_or_equal" : (ops.greater !== undefined ? "greater" : null);
          var highOp = ops.less_or_equal !== undefined ? "less_or_equal" : (ops.less !== undefined ? "less" : null);
          if (lowOp && highOp) {
            low = parseInt(ops[lowOp], 10);
            high = parseInt(ops[highOp], 10);
          }
        }
        if (low !== null && high !== null && !isNaN(low) && !isNaN(high) && low <= high) {
          var spec = DERIVED_ATTRIBUTES[dId];
          replacements[pair[0].idx] = {
            id: dId,
            field: spec.field,
            type: "integer",
            input: "number",
            operator: "between",
            value: [low, high]
          };
          toRemove[pair[1].idx] = true;
        }
      });
      if (Object.keys(replacements).length) {
        converted = converted.reduce(function (acc, item, idx) {
          if (toRemove[idx]) return acc;
          acc.push(replacements[idx] || item);
          return acc;
        }, []);
      }
    }
    return $.extend({}, rules, {
      condition: condition,
      rules: converted
    });
  }

  function renderSegmentFormSelectedAudienceTags() {
    var $container = $("#segment-form-selected-audience-tags");
    var $count = $("#segment-form-audience-tags-count");
    $count.text(segmentFormAudienceTags.length + " selected");
    if (!$container.length) return;
    $container.empty();
    if (!segmentFormAudienceTags.length) {
      $container.append(
        $('<span class="text-xs italic text-slate-400">No IAB audience tags selected — choose options below to assign to this segment or filter profiles.</span>')
      );
      return;
    }
    segmentFormAudienceTags.forEach(function (tagName) {
      var meta = audienceTagsByTagName[tagName];
      var label = meta
        ? (meta.tier_6 || meta.tier_5 || meta.tier_4 || meta.tier_3 || meta.tier_2 || meta.tier_1 || tagName)
        : tagName;
      var $pill = $(
        '<span class="inline-flex items-center gap-1.5 rounded-lg bg-teal-50 px-2.5 py-1 text-xs font-medium text-teal-800 ring-1 ring-inset ring-teal-600/20"></span>'
      ).attr("title", meta && meta.condensed_name ? meta.condensed_name + " (" + tagName + ")" : tagName);
      $pill.append($('<span class="font-semibold"></span>').text(label));
      $pill.append($('<span class="font-mono text-[10px] text-teal-600"></span>').text(tagName));
      var $remove = $(
        '<button type="button" class="btn-segment-remove-audience-tag ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-teal-600 hover:bg-teal-200 hover:text-teal-900" aria-label="Remove tag">&times;</button>'
      ).attr("data-tag-name", tagName);
      $pill.append($remove);
      $container.append($pill);
    });
  }

  function renderSegmentFormAudienceTagOptions() {
    var $list = $("#segment-form-audience-tag-options");
    if (!$list.length) return;
    var tier1 = String($("#segment-form-audience-tier1-filter").val() || "").trim();
    var q = String($("#segment-form-audience-tag-search").val() || "").trim().toLowerCase();
    var selectedSet = {};
    segmentFormAudienceTags.forEach(function (t) { selectedSet[t] = true; });

    var filtered = audienceTagsCatalog.filter(function (item) {
      if (tier1 && item.tier_1 !== tier1) return false;
      if (!q) return true;
      var haystack = [
        item.tag_name,
        item.condensed_name,
        item.unique_id,
        item.tier_1,
        item.tier_2,
        item.tier_3,
        item.tier_4,
        item.tier_5,
        item.tier_6
      ].filter(Boolean).join(" ").toLowerCase();
      return haystack.indexOf(q) !== -1;
    });

    $list.empty();
    if (!filtered.length) {
      $list.append(
        $('<div class="px-3 py-3 text-slate-400 text-center"></div>').text(
          audienceTagsCatalog.length
            ? "No matching IAB taxonomy tags found."
            : "No IAB taxonomy tags found in database."
        )
      );
      return;
    }

    filtered.slice(0, 120).forEach(function (item) {
      var isSelected = !!selectedSet[item.tag_name];
      var cleanCondensed = String(item.condensed_name || item.tag_name).replace(/\s*\|\s*$/, "");
      var $row = $(
        '<button type="button" class="btn-segment-toggle-audience-tag w-full flex items-center justify-between gap-2 px-3 py-2 text-left transition-colors hover:bg-teal-50/70"></button>'
      )
        .attr("data-tag-name", item.tag_name)
        .toggleClass("bg-teal-50/60", isSelected);

      var $left = $('<div class="min-w-0 flex-1"></div>');
      var $top = $('<div class="flex flex-wrap items-center gap-1.5"></div>');
      $top.append($('<span class="font-medium text-slate-800"></span>').text(cleanCondensed));
      $top.append(
        $('<span class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600"></span>').text(
          item.tier_1 || "IAB"
        )
      );
      $left.append($top);
      $left.append(
        $('<div class="font-mono text-[11px] text-slate-400 truncate"></div>').text(
          item.tag_name + (item.unique_id ? " · ID #" + item.unique_id : "")
        )
      );

      var $badge = $(
        '<span class="shrink-0 rounded-md px-2 py-0.5 text-[11px] font-semibold"></span>'
      )
        .toggleClass("bg-teal-600 text-white", isSelected)
        .toggleClass("bg-slate-100 text-slate-600", !isSelected)
        .text(isSelected ? "✓ Selected" : "+ Select");

      $row.append($left, $badge);
      $list.append($row);
    });
  }

  function addSegmentFormAudienceTag(tagName) {
    var cleaned = $.trim(String(tagName || ""));
    if (!cleaned) return;
    if (segmentFormAudienceTags.indexOf(cleaned) === -1) {
      segmentFormAudienceTags.push(cleaned);
    }
    renderSegmentFormSelectedAudienceTags();
    renderSegmentFormAudienceTagOptions();
  }

  function removeSegmentFormAudienceTag(tagName) {
    var cleaned = $.trim(String(tagName || ""));
    segmentFormAudienceTags = segmentFormAudienceTags.filter(function (item) {
      return item !== cleaned;
    });
    renderSegmentFormSelectedAudienceTags();
    renderSegmentFormAudienceTagOptions();
  }

  function toggleSegmentFormAudienceTag(tagName) {
    var cleaned = $.trim(String(tagName || ""));
    if (!cleaned) return;
    if (segmentFormAudienceTags.indexOf(cleaned) !== -1) {
      removeSegmentFormAudienceTag(cleaned);
    } else {
      addSegmentFormAudienceTag(cleaned);
    }
  }

  function applySelectedAudienceTagsToQueryBuilder() {
    var $error = $("#segment-form-error");
    $error.addClass("hidden").text("");
    if (!segmentFormAudienceTags.length) {
      $error.removeClass("hidden").text("Select at least one IAB audience tag above before adding it to Audience Rules.");
      return;
    }
    if (!queryBuilderReady) {
      $error.removeClass("hidden").text("The rule builder is still loading.");
      return;
    }
    var $builder = $("#segment-query-builder");
    var currentRules = null;
    try {
      currentRules = $builder.queryBuilder("getRules", { allow_invalid: true });
    } catch (e) {
      currentRules = null;
    }
    var rulesList = (currentRules && Array.isArray(currentRules.rules)) ? currentRules.rules.slice() : [];
    rulesList = rulesList.filter(function (r) {
      return r && (r.id || r.field || (r.rules && r.rules.length));
    });
    var updated = false;
    rulesList = rulesList.map(function (r) {
      if (r && (r.id === "audience_tags" || r.field === "audience_tags")) {
        updated = true;
        return {
          id: "audience_tags",
          field: "audience_tags",
          operator: "in",
          value: segmentFormAudienceTags.slice()
        };
      }
      return r;
    });
    if (!updated) {
      rulesList.push({
        id: "audience_tags",
        field: "audience_tags",
        operator: "in",
        value: segmentFormAudienceTags.slice()
      });
    }
    var nextRules = {
      condition: (currentRules && currentRules.condition) || "AND",
      rules: rulesList
    };
    $builder.queryBuilder("setRules", normalizeSegmentRules(nextRules));
  }

  function loadSegmentAttributes(domain, rules) {
    var $builder = $("#segment-query-builder");
    var loadSequence = ++attributeLoadSequence;
    $("#segment-query-builder-loading").removeClass("hidden");
    if (queryBuilderReady && typeof $builder.queryBuilder === "function") $builder.queryBuilder("destroy");
    $builder.empty();
    queryBuilderReady = false;
    var params = domain && domain !== "all" ? { domain: domain } : {};
    return $.when(
      api("/segments/segmentable-profile-attributes", params),
      ensureAudienceTagsCatalog()
    )
      .done(function (attributesRes) {
        if (loadSequence !== attributeLoadSequence) return;
        var attributes = Array.isArray(attributesRes && attributesRes[0]) ? attributesRes[0] : (attributesRes || []);
        segmentAttributes = (attributes || []).slice();
        var hasAudienceTagsAttr = segmentAttributes.some(function (attr) {
          return attr && attr.field === "audience_tags";
        });
        if (!hasAudienceTagsAttr) {
          segmentAttributes.push({
            id: "audience_tags",
            field: "audience_tags",
            name: "IAB Audience Tags",
            description: "Standardized IAB Audience Taxonomy tag_name array on cdp_master_profiles",
            attribute_group: "MARKETING",
            data_type: "ARRAY",
            domain_scope: "all",
            is_pii: false
          });
        }
        Object.keys(DERIVED_ATTRIBUTES).forEach(function (derivedId) {
          var dSpec = DERIVED_ATTRIBUTES[derivedId];
          var exists = segmentAttributes.some(function (attr) {
            return attr && (attr.id || attr.field) === derivedId;
          });
          if (!exists) {
            segmentAttributes.push($.extend({}, dSpec));
          }
        });
        renderSegmentFormSelectedAudienceTags();
        renderSegmentFormAudienceTagOptions();
        $("#segment-form-attribute-count").text(segmentAttributes.length + " attributes available");
        if (typeof $builder.queryBuilder !== "function") {
          $("#segment-form-error").removeClass("hidden").text("jQuery QueryBuilder could not be loaded.");
          return;
        }
        if (!segmentAttributes.length) {
          $("#segment-form-error").removeClass("hidden").text("No segmentable profile attributes are available for this domain.");
          $("#segment-query-builder-loading").addClass("hidden");
          return;
        }
        try {
          $builder.queryBuilder({
            filters: queryBuilderFilters(segmentAttributes, rules),
            allow_empty: true,
            plugins: ["tw-tooltip-errors"]
          });
          queryBuilderReady = true;
          if (rules && rules.rules && rules.rules.length) {
            $builder.queryBuilder("setRules", normalizeSegmentRules(rules));
          }
        } catch (error) {
          queryBuilderReady = false;
          $("#segment-form-error").removeClass("hidden").text("Could not start the rule builder: " + error.message);
        }
        $("#segment-query-builder-loading").addClass("hidden");
      })
      .fail(function (xhr) {
        $("#segment-query-builder-loading").addClass("hidden");
        showApiError("loading segment attributes", xhr);
      });
  }

  function closeSegmentForm() {
    attributeLoadSequence += 1;
    aiRequestSequence += 1;
    if (aiRequest) {
      var request = aiRequest;
      aiRequest = null;
      request.abort();
    }
    setSegmentAiProcessing(false);
    $("#segment-form-modal").addClass("hidden");
    if (queryBuilderReady) {
      $("#segment-query-builder").queryBuilder("destroy");
      queryBuilderReady = false;
    }
  }

  function openSegmentForm(segment, focusAudienceTags) {
    editingSegmentId = segment ? segment.segment_id : null;
    $("#segment-form-title").text(segment ? "Edit segment" : "Create segment");
    $("#segment-form-save-label").text(segment ? "Save changes" : "Create segment");
    $("#segment-form-error").addClass("hidden").text("");
    $("#segment-form-name").val(segment ? segment.segment_name : "");
    $("#segment-form-tag").val(segment ? segment.segment_tag : "");
    $("#segment-form-description").val(segment ? (segment.description || "") : "");
    $("#segment-form-domain").val(segment ? (segment.domain || "all") : "all");
    segmentFormAudienceTags = (segment && Array.isArray(segment.audience_tags))
      ? segment.audience_tags.slice()
      : [];
    $("#segment-form-audience-tier1-filter").val("");
    $("#segment-form-audience-tag-search").val("");
    renderSegmentFormSelectedAudienceTags();
    rulesFromAi = false;
    $("#segment-form-ai-text").val("");
    $("#segment-form-ai-result").addClass("hidden");
    resetAiState();
    $("#segment-form-modal").removeClass("hidden");
    loadSegmentAttributes(segment ? segment.domain : "all", segment ? segment.json_rules : null);
    if (focusAudienceTags === true) {
      setTimeout(function () {
        var $search = $("#segment-form-audience-tag-search");
        if ($search.length) $search.trigger("focus");
      }, 60);
    }
  }

  // Sync the AI state into the UI; call after every state change.
  function renderAiState() {
    var hasState = !!(aiSoFar || aiLastQuestion);
    if (aiSoFar) {
      $("#segment-form-ai-sofar-wrap").removeClass("hidden");
      $("#segment-form-ai-sofar").val(aiSoFar);
    } else {
      $("#segment-form-ai-sofar-wrap").addClass("hidden");
      $("#segment-form-ai-sofar").val("");
    }
    if (aiLastQuestion) {
      $("#segment-form-ai-question").removeClass("hidden").text(aiLastQuestion);
    } else {
      $("#segment-form-ai-question").addClass("hidden").text("");
    }
    $("#btn-segment-form-ai-reset").toggleClass("hidden", !hasState);
    $("#segment-form-ai-label").text(hasState ? "Update rules" : "Generate rules");
  }

  // Clears the AI state only, not the builder or the name/tag fields.
  function resetAiState() {
    aiSoFar = null;
    aiLastQuestion = null;
    renderAiState();
  }

  function setSegmentAiProcessing(processing) {
    var $modal = $("#segment-form-modal");
    var $progress = $("#segment-form-ai-progress");
    var $aiButton = $("#btn-segment-form-ai");

    if (processing) {
      aiLockedControls = [];
      $modal.find("input, textarea, select, button").each(function () {
        if (this.id === "btn-segment-form-close") return;
        var $control = $(this);
        aiLockedControls.push({
          element: this,
          readonly: $control.prop("readonly"),
          disabled: $control.prop("disabled")
        });
        if (this.tagName === "INPUT" || this.tagName === "TEXTAREA") {
          $control.prop("readonly", true);
        } else {
          $control.prop("disabled", true);
        }
        $control.addClass("segment-ai-locked");
      });
      $modal.attr("aria-busy", "true");
      $progress.removeClass("hidden").attr("aria-busy", "true");
      $aiButton.addClass("opacity-60 cursor-not-allowed");
      $("#segment-form-ai-spinner").removeClass("hidden");
      $("#segment-form-ai-label").text("Processing...");
      return;
    }

    aiLockedControls.forEach(function (control) {
      var $control = $(control.element);
      $control.prop("readonly", control.readonly).prop("disabled", control.disabled)
        .removeClass("segment-ai-locked");
    });
    aiLockedControls = [];
    $modal.removeAttr("aria-busy");
    $progress.addClass("hidden").attr("aria-busy", "false");
    $aiButton.removeClass("opacity-60 cursor-not-allowed");
    $("#segment-form-ai-spinner").addClass("hidden");
  }

  function showAiResult(kind, message, suggestions) {
    var styles = {
      valid: "border border-emerald-200 bg-emerald-50 text-emerald-800",
      needs_clarification: "border border-amber-200 bg-amber-50 text-amber-800",
      rejected: "border border-red-200 bg-red-50 text-red-700"
    };
    // .text(), never .html(): untrusted.
    $("#segment-form-ai-result").removeClass("hidden " + Object.keys(styles).map(function (k) { return styles[k]; }).join(" "))
      .addClass(styles[kind] || styles.rejected);
    $("#segment-form-ai-message").text(message || "");
    var hasSuggestions = !!(suggestions && suggestions.length);
    $("#segment-form-ai-suggestions").toggleClass("hidden", !hasSuggestions)
      .text(hasSuggestions ? "Options: " + suggestions.join(", ") : "");
  }

  // Text -> validated json_rules; rulesSql() on save stays the only SQL path.
  function generateRulesFromText() {
    var description = $.trim($("#segment-form-ai-text").val());
    if (!description) {
      showAiResult("needs_clarification", "Describe the customers you want in this segment.");
      return;
    }
    if (!queryBuilderReady) {
      showAiResult("needs_clarification", "The rule builder is still loading. Try again in a moment.");
      return;
    }
    var domain = $("#segment-form-domain").val() || "all";
    // Pick up user edits to "So far".
    aiSoFar = $.trim($("#segment-form-ai-sofar").val()) || null;
    var currentRules = null;
    try {
      // null unless the builder is valid.
      var existingRules = $("#segment-query-builder").queryBuilder("getRules");
      if (existingRules && existingRules.rules && existingRules.rules.length) currentRules = existingRules;
    } catch (error) {
      currentRules = null;
    }
    var requestSequence = ++aiRequestSequence;
    setSegmentAiProcessing(true);
    aiRequest = api("/segments/from-description", {
      description: description,
      domain: domain,
      so_far: aiSoFar,
      last_question: aiLastQuestion,
      current_rules: currentRules
    }, "POST")
      .done(function (result) {
        if (requestSequence !== aiRequestSequence) return;
        aiSoFar = (result && result.so_far) || aiSoFar;
        aiLastQuestion = (result && result.validation_status === "needs_clarification") ? (result.question || null) : null;
        if (!result || result.validation_status !== "valid" || !result.ready_for_segment_persistence) {
          showAiResult(result && result.validation_status, (result && (result.question || result.interpretation)) ||
            "Could not turn that description into rules.", result && result.suggestions);
          return;
        }
        try {
          $("#segment-query-builder").queryBuilder("setRules", normalizeSegmentRules(result.json_rules));
        } catch (error) {
          showAiResult("rejected", "The generated rules could not be loaded: " + error.message);
          return;
        }
        rulesFromAi = true;
        if (!$.trim($("#segment-form-name").val()) && result.segment_name) $("#segment-form-name").val(result.segment_name);
        if (!$.trim($("#segment-form-tag").val()) && result.segment_tag) $("#segment-form-tag").val(result.segment_tag);
        if (!$.trim($("#segment-form-description").val())) $("#segment-form-description").val(description);
        $("#segment-form-ai-text").val("");
        showAiResult("valid", result.interpretation || "Rules generated. Review them below before saving.");
      })
      .fail(function (xhr) {
        if (requestSequence !== aiRequestSequence) return;
        var detail = xhr && xhr.responseJSON && xhr.responseJSON.detail;
        showAiResult("rejected", typeof detail === "string" ? detail : "The AI service could not generate rules right now.");
      })
      .always(function () {
        if (requestSequence !== aiRequestSequence) return;
        aiRequest = null;
        setSegmentAiProcessing(false);
        renderAiState();
      });
  }

  function submitSegmentForm() {
    var $error = $("#segment-form-error");
    $error.addClass("hidden").text("");
    var name = $.trim($("#segment-form-name").val());
    var tag = $.trim($("#segment-form-tag").val());
    if (!name || !tag) {
      $error.removeClass("hidden").text("Segment name and segment tag are required.");
      return;
    }
    if (!queryBuilderReady) {
      $error.removeClass("hidden").text("The rule builder is still loading.");
      return;
    }
    try {
      var rules = $("#segment-query-builder").queryBuilder("getRules", { allow_invalid: true, skip_empty: true });
      var hasRuleNodes = !!(rules && Array.isArray(rules.rules) && rules.rules.length);
      if (hasRuleNodes && !rules.valid) {
        throw new Error("Complete every audience rule before saving.");
      }
      if (!hasRuleNodes && !segmentFormAudienceTags.length) {
        throw new Error("Add at least one audience rule or select IAB audience tags.");
      }
      var filtersById = {};
      segmentAttributes.forEach(function (attribute) {
        var key = attribute.id || attribute.field;
        filtersById[key] = attribute;
        if (attribute.field && !filtersById[attribute.field]) {
          filtersById[attribute.field] = attribute;
        }
      });
      var sqlRules = hasRuleNodes ? rulesSql(rules, filtersById) : "";
      if (segmentFormAudienceTags.length && !/\baudience_tags\b/i.test(sqlRules)) {
        var quotedTags = segmentFormAudienceTags.map(function (item) {
          return sqlQuote($.trim(String(item)));
        }).join(", ");
        var audienceTagsSql = "(audience_tags && ARRAY[" + quotedTags + "]::text[])";
        sqlRules = sqlRules ? "(" + sqlRules + " AND " + audienceTagsSql + ")" : audienceTagsSql;
      }
      var payload = {
        segment_name: name,
        segment_tag: tag,
        audience_tags: segmentFormAudienceTags.slice(),
        domain: $("#segment-form-domain").val() || "all",
        description: $.trim($("#segment-form-description").val()) || null,
        json_rules: rules || { condition: "AND", rules: [] },
        sql_rules: sqlRules,
        processed_by: rulesFromAi ? "ai_agent" : "human",
        is_active: true
      };
      if (!editingSegmentId) payload.tenant_id = C360.config.current.tenantId;
      var wasEditing = !!editingSegmentId;
      var savedSegmentId = editingSegmentId;
      var request = editingSegmentId
        ? api("/segments/" + editingSegmentId, payload, "PATCH")
        : api("/segments/", payload, "POST");
      $("#btn-segment-form-save").prop("disabled", true).addClass("opacity-60");
      request.done(function (response) {
        closeSegmentForm();
        loadList(false, true);
        var targetSegmentId = savedSegmentId || (response && response.segment_id);
        if (targetSegmentId && targetSegmentId === currentSegmentId) loadDetail(targetSegmentId);
        showToast(wasEditing ? "Segment updated" : "Segment created", "success");
        if (wasEditing && targetSegmentId) {
          currentSegmentId = targetSegmentId;
          refreshSegmentDetail();
        }
      }).fail(function (xhr) {
        var detail = (xhr.responseJSON && xhr.responseJSON.detail) || "Could not save segment.";
        $error.removeClass("hidden").text(typeof detail === "string" ? detail : JSON.stringify(detail));
      }).always(function () {
        $("#btn-segment-form-save").prop("disabled", false).removeClass("opacity-60");
      });
    } catch (error) {
      $error.removeClass("hidden").text(error.message || "Please check the audience rules.");
    }
  }

  function segmentRowVm(s) {
    return $.extend({}, s, {
      domainLabel: fmt.domainLabel(s.domain),
      domainIcon: fmt.domainIcon(s.domain),
      domainIconBg: fmt.domainIconBg(s.domain),
      processedByLabel: processedByLabel(s.processed_by),
      processedByBadgeClass: processedByBadgeClass(s.processed_by),
      activeLabel: activeLabel(s.is_active),
      activeBadgeClass: activeBadgeClass(s.is_active),
      memberCountLabel: fmt.int(s.member_count),
      createdLabel: fmt.date(s.created_at)
    });
  }

  function segmentCreatedTimestamp(segment) {
    if (!segment || !segment.created_at) return 0;
    var parsed = Date.parse(segment.created_at);
    return isNaN(parsed) ? 0 : parsed;
  }

  function sortSegmentsList(items) {
    var mode = String($("#segments-sort-filter").val() || "created_desc");
    return (items || []).slice().sort(function (a, b) {
      if (mode === "created_asc" || mode === "created_desc") {
        var timeA = segmentCreatedTimestamp(a);
        var timeB = segmentCreatedTimestamp(b);
        if (timeA !== timeB) {
          return mode === "created_asc" ? timeA - timeB : timeB - timeA;
        }
      } else if (mode === "members_desc" || mode === "members_asc") {
        var countA = Number(a && a.member_count) || 0;
        var countB = Number(b && b.member_count) || 0;
        if (countA !== countB) {
          return mode === "members_asc" ? countA - countB : countB - countA;
        }
      } else if (mode === "name_asc" || mode === "name_desc") {
        var nameCmp = String((a && a.segment_name) || "").localeCompare(String((b && b.segment_name) || ""));
        if (nameCmp !== 0) {
          return mode === "name_asc" ? nameCmp : -nameCmp;
        }
      }
      // Fallback tie-breaker: Created On descending, then segment_name ascending
      var fallbackTime = segmentCreatedTimestamp(b) - segmentCreatedTimestamp(a);
      if (fallbackTime !== 0) return fallbackTime;
      return String((a && a.segment_name) || "").localeCompare(String((b && b.segment_name) || ""));
    });
  }

  // Shared data-table component instance backing the segments list (see
  // static/js/data-table-view.js + list-view.js for the same pattern).
  var listDtv = C360.DataTableView.create({
    columns: [
      {
        label: "Segment", type: "identity", nameField: "segment_name", subField: "segment_tag", subStyle: "tag",
        avatarField: "domainIcon", avatarBgField: "domainIconBg", avatarTextClass: "text-base"
      },
      { label: "Business Domain", field: "domainLabel", capitalize: true },
      { label: "Created By", type: "badge", field: "processedByLabel", classField: "processedByBadgeClass" },
      { label: "Matched Profiles", field: "memberCountLabel" },
      { label: "Lifecycle", type: "badge", field: "activeLabel", classField: "activeBadgeClass" },
      { label: "Created On", field: "createdLabel", muted: true }
    ],
    rowVm: segmentRowVm,
    rowId: function (vm) { return vm.segment_id; },
    rowSelectorClass: "segment-row",
    resourceLabel: "segment",
    clientSide: true,
    clientSideLimit: 500,
    clientSort: function (items) {
      return sortSegmentsList(items);
    },
    clientFilters: {
      q: function (vm, value) {
        var needle = String(value || "").toLowerCase().trim();
        if (!needle) return true;
        var tagsText = Array.isArray(vm.audience_tags) ? vm.audience_tags.join(" ").toLowerCase() : "";
        return (vm.segment_name || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.segment_tag || "").toLowerCase().indexOf(needle) !== -1 ||
          (vm.description || "").toLowerCase().indexOf(needle) !== -1 ||
          tagsText.indexOf(needle) !== -1;
      },
      domain: function (vm, value) { return vm.domain === value; },
      status: function (vm, value) {
        return value === "active" ? !!vm.is_active : !vm.is_active;
      },
      owner: function (vm, value) { return vm.processed_by === value; },
      members: function (vm, value) {
        var memberCount = Number(vm.member_count) || 0;
        return value === "empty" ? memberCount === 0 : memberCount > 0;
      }
    },
    fetch: function (params) {
      return api("/segments/", params).then(function (segments) {
        var sorted = sortSegmentsList(segments || []);
        sorted.forEach(function (segment) { segmentsById[segment.segment_id] = segment; });
        return sorted;
      });
    },
    onRowClick: function (id) { C360.router.navigate("/segments/" + id); },
    onEdit: function (id) { openSegmentForm(segmentsById[id]); },
    editLabel: "Edit",
    onError: function (xhr) { showApiError("loading segments", xhr); },
    el: {
      thead: "#segments-thead",
      tbody: "#segments-tbody",
      loading: "#segments-list-loading",
      empty: "#segments-list-empty",
      countLabel: "#segments-count-label",
      loadMoreBtn: "#btn-segments-load-more"
    }
  });

  // Matched-profiles sub-table on the segment detail page renders plain
  // profile rows -- reuse list-view.js's columns/rowVm instead of
  // duplicating that config. Re-created per segment-detail render since
  // segment-details.html (and its #segment-matched-* ids) is itself
  // re-rendered on every loadDetail() call.
  var matchedDtv = null;
  function createMatchedDtv() {
    return C360.DataTableView.create({
      // The matched-profile table is a dense drill-down inside a tab panel.
      // Keep the main profile list's comfortable spacing, but render its
      // Profile identity with the same compact treatment as Domain.
      columns: C360.profileListView.columns.map(function (column, index) {
        return index === 0 ? $.extend({}, column, { compact: true }) : column;
      }),
      compactTable: true,
      rowVm: C360.profileListView.rowVm,
      rowId: function (vm) { return vm.master_profile_id; },
      rowSelectorClass: "profile-row",
      resourceLabel: "matched profile",
      fetch: function (params) { return api("/segments/" + currentSegmentId + "/matched-profiles", params); },
      onRowClick: function (id) { C360.router.navigate("/profiles/" + id); },
      onError: function (xhr) { showApiError("loading matched profiles", xhr); },
      el: {
        thead: "#segment-matched-thead",
        tbody: "#segment-matched-tbody",
        loading: "#segment-matched-loading",
        empty: "#segment-matched-empty",
        countLabel: "#segment-matched-count-label",
        loadMoreBtn: "#btn-segment-matched-load-more"
      }
    });
  }

  function segmentDetailVm(s) {
    var displaySql = s.final_generated_sql || s.sql_rules || "";
    var audienceTags = Array.isArray(s.audience_tags) ? s.audience_tags : [];
    var audienceTagItems = audienceTags.map(function (tagName) {
      var meta = audienceTagsByTagName[tagName];
      var condensed = meta && meta.condensed_name ? String(meta.condensed_name).replace(/\s*\|\s*$/, "") : "";
      var parts = String(tagName || "").split("/").filter(Boolean);
      var lastPart = parts.length ? parts[parts.length - 1].replace(/-/g, " ") : tagName;
      var shortLabel = meta
        ? (meta.tier_6 || meta.tier_5 || meta.tier_4 || meta.tier_3 || meta.tier_2 || meta.tier_1 || lastPart)
        : fmt.titleCase(lastPart);
      return {
        tag_name: tagName,
        shortLabel: shortLabel,
        displayLabel: condensed || tagName
      };
    });
    return $.extend({}, s, {
      domainLabel: fmt.domainLabel(s.domain),
      processedByLabel: processedByLabel(s.processed_by) + (s.processed_by === "ai_agent" ? "" : " (SQL Query Builder)"),
      processedByBadgeClass: processedByBadgeClass(s.processed_by),
      activeLabel: activeLabel(s.is_active),
      activeBadgeClass: activeBadgeClass(s.is_active),
      memberCountLabel: fmt.int(s.member_count),
      lastComputedLabel: fmt.dateTime(s.last_computed_at),
      createdLabel: fmt.dateTime(s.created_at),
      updatedLabel: fmt.dateTime(s.updated_at),
      display_sql: fmt.formatSqlForDisplay(displaySql),
      hasSqlRules: !!displaySql,
      hasJsonRules: !!(s.json_rules && Object.keys(s.json_rules).length),
      hasAudienceTags: audienceTags.length > 0,
      audienceTagsCount: audienceTags.length,
      audienceTagItems: audienceTagItems
    });
  }

  function loadList(append, forceReload) {
    if (forceReload && listDtv.resetClientCache) listDtv.resetClientCache();
    return listDtv.load(append, forceReload);
  }

  function loadMatchedProfiles(segmentId, append) {
    if (!matchedDtv) return;
    return matchedDtv.load(append);
  }

  function activateSegmentInsightPanel(panelId, moveFocus) {
    var $tabs = $(".segment-insight-tab");
    var $panels = $(".segment-insight-panel");
    var $selected = $tabs.filter("[data-segment-panel='" + panelId + "']");
    if (!$selected.length) return;

    $tabs.each(function () {
      var selected = $(this).attr("data-segment-panel") === panelId;
      $(this)
        .attr("aria-selected", selected ? "true" : "false")
        .toggleClass("border-indigo-200 bg-indigo-50 text-indigo-700 shadow-sm", selected)
        .toggleClass("border-transparent text-slate-600", !selected);
    });
    $panels.each(function () {
      $(this).toggleClass("hidden", $(this).attr("id") !== panelId);
    });
    if (moveFocus) $selected.trigger("focus");
  }

  function bindSegmentInsightTabs() {
    var $tabs = $(".segment-insight-tab");
    $tabs.off(".segmentInsightTabs");
    $tabs.on("click.segmentInsightTabs", function () {
      activateSegmentInsightPanel($(this).attr("data-segment-panel"), false);
    });
    $tabs.on("keydown.segmentInsightTabs", function (event) {
      var $current = $(this);
      var $allTabs = $(".segment-insight-tab");
      var index = $allTabs.index($current);
      var nextIndex = index;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") nextIndex = (index + 1) % $allTabs.length;
      if (event.key === "ArrowUp" || event.key === "ArrowLeft") nextIndex = (index - 1 + $allTabs.length) % $allTabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = $allTabs.length - 1;
      if (nextIndex === index) return;
      event.preventDefault();
      activateSegmentInsightPanel($allTabs.eq(nextIndex).attr("data-segment-panel"), true);
    });
    activateSegmentInsightPanel("segment-panel-sql", false);
  }

  function loadDetail(segmentId) {
    currentSegmentId = segmentId;
    $("#segment-detail-content").empty();
    $("#segment-detail-loading").removeClass("hidden");

    $.when(
      api("/segments/" + segmentId),
      ensureAudienceTagsCatalog()
    )
      .done(function (segmentRes) {
        var segment = Array.isArray(segmentRes) ? segmentRes[0] : segmentRes;
        segmentsById[segment.segment_id] = segment;
        $("#segment-detail-loading").addClass("hidden");
        $("#segment-detail-content").html(C360.templates.render("segment-details", segmentDetailVm(segment)));
        bindSegmentInsightTabs();
        workflowController.load(segmentId);
        matchedDtv = createMatchedDtv();
        matchedDtv.bindLoadMore();
        loadMatchedProfiles(segmentId, false);
      })
      .fail(function (xhr) {
        $("#segment-detail-loading").addClass("hidden");
        showApiError("loading segment detail", xhr);
      });
  }

  function showList() {
    $("#segment-view-detail").addClass("hidden");
    $("#segment-view-list").removeClass("hidden");
  }

  function showDetail(segmentId) {
    $("#segment-view-list").addClass("hidden");
    $("#segment-view-detail").removeClass("hidden");
    loadDetail(segmentId);
  }

  function load() {
    showList();
    loadList(false, true);
  }

  // Polling config for the async recompute-all job (see
  // POST /segments/admin/recompute-all + GET /segments/admin/recompute-status/{run_id}).
  // Dagster runs a full-table scan per active segment out-of-process, so
  // completion time depends on cdp_master_profiles size (could be 1M+ rows
  // in production) -- poll instead of blocking, and give up gracefully
  // after REFRESH_POLL_MAX_ATTEMPTS rather than polling forever.
  var REFRESH_POLL_INTERVAL_MS = 2000;
  var REFRESH_POLL_MAX_ATTEMPTS = 30; // ~1 minute at 2s/attempt


  // Builds a short " (N of M steps failed, ran Xs)" / " (ran Xs)" suffix
  // from the recompute-status response (see
  // core.utils.dagster_client.DagsterService.get_status) so the toast shows
  // more than a bare "success"/"failure" -- duration and, on failure, how
  // many steps failed (point the user at the Dagster UI for the full stack
  // trace rather than trying to surface it here).
  function formatRunDetail(result) {
    var parts = [];
    if (result.steps_failed) {
      var total = (result.steps_succeeded || 0) + result.steps_failed;
      parts.push(result.steps_failed + " of " + total + " steps failed");
    }
    if (typeof result.duration_seconds === "number") {
      parts.push("ran " + Math.round(result.duration_seconds) + "s");
    }
    return parts.length ? " (" + parts.join(", ") + ")" : "";
  }

  function setRefreshButtonBusy(selector, busy, label) {
    var $btn = $(selector);
    if (!$btn.length) return;
    var $label = $btn.children("span").last();
    if (busy) {
      if (!$btn.data("original-class")) { $btn.data("original-class", $btn.attr("class")); }
      if (!$btn.data("original-label")) { $btn.data("original-label", $label.length ? $label.text() : $btn.text()); }
      $btn.prop("disabled", true).attr("aria-busy", "true")
        .addClass("bg-slate-300 cursor-wait")
        .removeClass("hover:bg-slate-100 hover:bg-slate-50");
      if ($label.length) $label.text(label || "Refreshing...");
      else $btn.text(label || "Refreshing...");
    } else {
      $btn.prop("disabled", false).removeAttr("aria-busy")
        .attr("class", $btn.data("original-class") || $btn.attr("class"));
      if ($label.length) $label.text($btn.data("original-label") || "Refresh");
      else $btn.text($btn.data("original-label") || "Refresh");
    }
  }

  function pollRecomputeStatus(runId, attempt, options) {
    options = options || {};
    var buttonSelector = options.buttonSelector || "#btn-segments-refresh";
    api("/segments/admin/recompute-status/" + runId)
      .done(function (result) {
        if (result.status === "success") {
          setRefreshButtonBusy(buttonSelector, false);
          showToast("\u2713 Segment refresh completed" + formatRunDetail(result), "success");
          if (options.onSuccess) options.onSuccess();
          return;
        }
        if (result.status === "failure") {
          setRefreshButtonBusy(buttonSelector, false);
          showToast("\u2717 Segment refresh job failed" + formatRunDetail(result), "error");
          return;
        }
        // Still running: keep polling until REFRESH_POLL_MAX_ATTEMPTS is hit.
        if (attempt >= REFRESH_POLL_MAX_ATTEMPTS) {
          setRefreshButtonBusy(buttonSelector, false);
          showToast("Segment refresh is still running in the background; check back shortly.", "info");
          return;
        }
        setTimeout(function () { pollRecomputeStatus(runId, attempt + 1, options); }, REFRESH_POLL_INTERVAL_MS);
      })
      .fail(function (xhr) {
        setRefreshButtonBusy(buttonSelector, false);
        showApiError("checking segment refresh status", xhr);
      });
  }

  function refreshAllSegments() {
    setRefreshButtonBusy("#btn-segments-refresh", true, "Submitting...");

    // Fire-and-return: this only submits a Dagster run and gets a run_id
    // back immediately (see backend docstring on the endpoint) -- the
    // actual recompute happens out-of-process, so this call never blocks
    // on cdp_master_profiles size.
    api("/segments/admin/recompute-all", {}, "POST")
      .done(function (response) {
        setRefreshButtonBusy("#btn-segments-refresh", true, "Refreshing...");
        showToast("Segment refresh job submitted (run " + response.run_id + ")...", "info");
        pollRecomputeStatus(response.run_id, 1, {
          buttonSelector: "#btn-segments-refresh",
          onSuccess: function () { loadList(false, true); }
        });
      })
      .fail(function (xhr) {
        setRefreshButtonBusy("#btn-segments-refresh", false);
        showApiError("submitting segment refresh job", xhr);
      });
  }

  function clearListFilters() {
    [
      "#segments-search-input",
      "#segments-domain-filter",
      "#segments-status-filter",
      "#segments-owner-filter",
      "#segments-members-filter"
    ].forEach(function (selector) { $(selector).val(""); });
    $("#segments-sort-filter").val("created_desc");
    listDtv.clearFilters();
  }

  function refreshSegmentDetail() {
    var segmentId = currentSegmentId;
    if (!segmentId) return;

    var buttonSelector = "#btn-segment-detail-refresh";
    setRefreshButtonBusy(buttonSelector, true, "Submitting...");
    api("/segments/" + encodeURIComponent(segmentId) + "/recompute", {}, "POST")
      .done(function (response) {
        setRefreshButtonBusy(buttonSelector, true, "Refreshing...");
        showToast("Segment refresh job submitted (run " + response.run_id + ")...", "info");
        pollRecomputeStatus(response.run_id, 1, {
          buttonSelector: buttonSelector,
          onSuccess: function () {
            if (currentSegmentId === segmentId && !$("#segment-view-detail").hasClass("hidden")) {
              loadDetail(segmentId);
            } else {
              loadList(false, true);
            }
          }
        });
      })
      .fail(function (xhr) {
        setRefreshButtonBusy(buttonSelector, false);
        showApiError("submitting segment refresh job", xhr);
      });
  }

  function bindEvents() {
    listDtv.bindRowClick();
    listDtv.bindLoadMore();
    listDtv.bindSearch("#segments-search-input", "q", 300);
    listDtv.bindSelect("#segments-domain-filter", "domain");
    listDtv.bindSelect("#segments-status-filter", "status");
    listDtv.bindSelect("#segments-owner-filter", "owner");
    listDtv.bindSelect("#segments-members-filter", "members");
    $(document).on("change", "#segments-sort-filter", function () {
      loadList(false, false);
    });
    // Matched-profiles rows share the ".profile-row" click delegation
    // already bound once by C360.profileListView.bindEvents() (both tables render
    // the same profile columns/rowVm) -- only "load more" needs re-binding
    // here since #segment-matched-* is fresh DOM on every loadDetail().
    $(document).on("click", "#btn-back-to-segments", function () { C360.router.navigate("/segments"); });
    $(document).on("click", "#btn-segments-refresh", function () { refreshAllSegments(); });
    $(document).on("click", "#btn-segments-clear-filters", clearListFilters);
    $(document).on("click", "#btn-segment-detail-refresh", function () { refreshSegmentDetail(); });
    $(document).on("click", "#btn-copy-sql", function () {
      var sql = $("#segment-sql-content").text().trim();
      if (!sql) return;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(sql);
      }
      var $btn = $(this);
      var $text = $btn.find(".copy-sql-text");
      $text.text("Copied!");
      setTimeout(function () {
        $text.text("Copy SQL");
      }, 1500);
    });
    $(document).on("click", "#btn-segments-create", function () { openSegmentForm(null, false); });
    $(document).on("click", "#btn-segment-detail-edit", function () { openSegmentForm(segmentsById[currentSegmentId], false); });
    $(document).on("click", "#btn-segment-detail-manage-tags", function () { openSegmentForm(segmentsById[currentSegmentId], true); });
    $(document).on("click", ".btn-segment-toggle-audience-tag", function () {
      toggleSegmentFormAudienceTag($(this).attr("data-tag-name"));
    });
    $(document).on("click", ".btn-segment-remove-audience-tag", function () {
      removeSegmentFormAudienceTag($(this).attr("data-tag-name"));
    });
    $(document).on("click", "#btn-segment-audience-tags-clear", function () {
      segmentFormAudienceTags = [];
      renderSegmentFormSelectedAudienceTags();
      renderSegmentFormAudienceTagOptions();
    });
    $(document).on("input", "#segment-form-audience-tag-search", function () {
      renderSegmentFormAudienceTagOptions();
    });
    $(document).on("change", "#segment-form-audience-tier1-filter", function () {
      renderSegmentFormAudienceTagOptions();
    });
    $(document).on("click", "#btn-segment-add-custom-audience-tag", function () {
      var val = $.trim($("#segment-form-audience-tag-search").val());
      if (!val) return;
      addSegmentFormAudienceTag(val);
      $("#segment-form-audience-tag-search").val("");
      renderSegmentFormAudienceTagOptions();
    });
    $(document).on("click", "#btn-segment-apply-audience-tags-rule", applySelectedAudienceTagsToQueryBuilder);
    $(document).on("click", ".btn-segment-detail-remove-audience-tag", function () {
      var segment = segmentsById[currentSegmentId];
      if (!segment || !currentSegmentId) return;
      var tagToRemove = $.trim(String($(this).attr("data-tag-name") || ""));
      if (!tagToRemove) return;
      var nextTags = (segment.audience_tags || []).filter(function (t) { return t !== tagToRemove; });
      api("/segments/" + encodeURIComponent(currentSegmentId) + "/audience-tags", {
        audience_tags: nextTags,
        recompute_profiles: true
      }, "POST")
        .done(function (updated) {
          segmentsById[currentSegmentId] = updated || segment;
          showToast("Removed IAB tag '" + tagToRemove + "' from segment", "success");
          loadDetail(currentSegmentId);
        })
        .fail(function (xhr) {
          showApiError("updating segment IAB audience tags", xhr);
        });
    });
    $(document).on("click", "#btn-segment-form-save", submitSegmentForm);
    $(document).on("click", "#btn-segment-form-ai", generateRulesFromText);
    $(document).on("click", "#btn-segment-form-ai-reset", resetAiState);
    $(document).on("click", "#btn-segment-form-cancel, #btn-segment-form-close", closeSegmentForm);
    $(document).on("click", "#segment-form-modal", function (e) {
      if (e.target === this) closeSegmentForm();
    });
    $(document).on("change", "#segment-form-domain", function () {
      if (!$("#segment-form-modal").hasClass("hidden")) {
        rulesFromAi = false;  // the builder reloads empty; any AI rules are gone
        resetAiState();
        loadSegmentAttributes($(this).val(), null);
      }
    });
  }

  // Owns the "/segments" (list) and "/segments/:id" (detail) routes (see
  // router.js). Both share the single "view-segments" section; showList()/
  // showDetail() toggle the two sub-panels nested inside it, the same way a
  // React Router layout route renders a child <Outlet/>.
  C360.router.define("/segments", {
    section: "view-segments",
    tab: "segments",
    mount: function () { load(); }
  });
  C360.router.define("/segments/:id", {
    section: "view-segments",
    tab: "segments",
    mount: function (params) { showDetail(params.id); }
  });

  C360.segmentsView = { load: load, bindEvents: bindEvents };
})(window.C360);