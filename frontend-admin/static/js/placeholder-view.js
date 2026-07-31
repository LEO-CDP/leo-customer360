/* Customer 360 Admin -- placeholder routes.
 *
 * Registers a full listing+detail route pair for every tab/entity that
 * doesn't have a real API-backed view yet: journeys, scoring models,
 * analytics reports, data source connectors/importers, identity
 * resolution rules, and admin user logins. Each is wired into
 * C360.router exactly like the real views in list-view.js/segments-view.js
 * are, so replacing a placeholder with a real view later is just a matter
 * of swapping its `mount` function for a real loader in its own file --
 * no changes needed in main.js or router.js. */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  function mountPlaceholder(title) {
    return function (params) {
      $("#placeholder-title").text(params && params.id ? title + " \u2014 " + params.id : title);
    };
  }

  // path -> { tab, title } for every not-yet-implemented listing/detail/editor view.
  var ENTRIES = [
    { path: "/journeys", tab: "journeys", title: "Journeys" },
    { path: "/journeys/:id", tab: "journeys", title: "Journey Detail" },

    { path: "/scoring", tab: "scoring", title: "Scoring Models" },
    { path: "/scoring/:id", tab: "scoring", title: "Scoring Model Detail" },

    { path: "/datasources/connectors", tab: "datasources", title: "Data Sources \u00b7 Connectors" },
    { path: "/datasources/connectors/:id", tab: "datasources", title: "Data Connector Detail" },
    { path: "/datasources/importers", tab: "datasources", title: "Data Sources \u00b7 Importers" },
    { path: "/datasources/importers/:id", tab: "datasources", title: "Data Importer Detail" },
    { path: "/datasources/identity-rules", tab: "datasources", title: "Identity Resolution Rules" },
    { path: "/datasources/identity-rules/:id", tab: "datasources", title: "Identity Resolution Rule Editor" },

    { path: "/admin/users", tab: "admin", title: "Admin \u00b7 User Logins" },
    { path: "/admin/users/:id", tab: "admin", title: "User Login Detail" }
  ];

  ENTRIES.forEach(function (entry) {
    C360.router.define(entry.path, {
      section: "view-placeholder",
      tab: entry.tab,
      mount: mountPlaceholder(entry.title)
    });
  });

  // Tabs whose primary content lives under a sub-resource default to it.
  C360.router.redirect("/datasources", "/datasources/connectors");
  C360.router.redirect("/admin", "/admin/users");
})(window.C360);
