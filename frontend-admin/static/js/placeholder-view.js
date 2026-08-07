/* Customer 360 Admin -- placeholder routes.
 *
 * Registers a full listing+detail route pair for every tab/entity that
 * doesn't have a real API-backed view yet: scoring models,
 * analytics reports and admin user logins. Each is wired into
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
  C360.router.redirect("/admin", "/admin/users");
})(window.C360);
