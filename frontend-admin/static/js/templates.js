/* Customer 360 Admin -- template loader.
 * Fetches every partial HTML fragment from static/templates/ over ajax,
 * compiles the data-driven ones with Handlebars, and registers the
 * profile-detail card partials so static/templates/profile/profile-details.html can
 * include them via {{> name}}. Keeping each card in its own file makes the
 * dashboard easy to extend without touching a single giant template. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var BASE = "static/templates/";

  // Logical template name -> actual file path (relative to BASE, no
  // extension). Must match static/templates/** exactly; unmapped names load
  // from BASE root ("<name>.html").
  var SOURCE_PATHS = {
    "data-table-head": "common/data-table-head",
    "data-table-rows": "common/data-table-rows",
    "placeholder": "common/placeholder",
    "settings-modal": "common/settings-modal",

    "overview-dashboard": "dashboard/overview-dashboard",
    "analytics": "dashboard/analytics",

    "attributes-list": "metadata/attributes-list",

    "profile-details": "profile/profile-details",
    "profiles-list": "profile/profiles-list",
    "content-items": "profile/content-items",
    "identity": "profile/identity",
    "channels": "profile/channels",
    "overview": "profile/overview",
    "segments": "profile/segments",
    "engagement": "profile/engagement",
    "activity": "profile/activity",
    "timeline": "profile/timeline",
    "scoring": "profile/scoring",
    "personalized-items": "profile/personalized-items",

    "segment-details": "segment/segment-details",
    "segments-list": "segment/segments-list",

    "campaign-dashboard": "campaign/campaign-dashboard"
  };

  // Rendered directly (compiled Handlebars template functions).
  var STANDALONE = [
    "data-table-head", "data-table-rows",
    "profile-details", "content-items", "overview-dashboard", "segment-details"
  ];

  // Injected as static HTML once (no Handlebars variables of their own).
  var STATIC_HTML = ["tabs", "settings-modal", "profiles-list", "placeholder", "segments-list", "attributes-list", "analytics", "campaign-dashboard"];

  // Registered as Handlebars partials so profile/profile-details.html can do {{> name}}.
  var PARTIALS = [
    "identity", "channels", "overview", "segments",
    "engagement", "activity", "timeline", "scoring", "personalized-items"
  ];

  var ALL_NAMES = STANDALONE.concat(STATIC_HTML, PARTIALS);

  var raw = {};
  var compiled = {};

  function loadAll() {
    var requests = ALL_NAMES.map(function (name) {
      var path = SOURCE_PATHS[name] || name;
      return $.get(BASE + path + ".html").done(function (text) {
        raw[name] = text;
        compiled[name] = Handlebars.compile(text);
      });
    });
    return $.when.apply($, requests).done(function () {
      PARTIALS.forEach(function (name) { Handlebars.registerPartial(name, raw[name]); });
    });
  }

  function render(name, context) {
    return compiled[name] ? compiled[name](context || {}) : "";
  }

  function html(name) {
    return raw[name] || "";
  }

  C360.templates = { loadAll: loadAll, render: render, html: html };
})(window.C360);
