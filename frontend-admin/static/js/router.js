/* Customer 360 Admin -- client-side router.
 *
 * A small React-Router-inspired hash router: view modules (list-view.js,
 * profile-detail-view.js, segments-view.js, overview-view.js,
 * placeholder-view.js, ...) each register the paths they own via
 * `C360.router.define(pattern, config)` at load time -- there is no
 * central switch-statement anywhere that has to know about every view.
 * Adding a brand new listing/detail/editor view (e.g. scoring models,
 * data connectors, admin users) is just a new `define()` call in that
 * view's own file.
 *
 * Path patterns look like React Router's: "/segments", "/segments/:id".
 * Only a single dynamic ":name" segment per path part is supported, which
 * is all this app needs (list vs. detail/editor routes).
 *
 * `config` accepts:
 *   - section: id of the top-level <section> this route renders into. The
 *     router keeps track of every section id that's ever been registered
 *     and hides all of them but the active one on every navigation, so
 *     nobody has to maintain a hand-written list of "every other section
 *     to hide" (that list used to live, and grow stale, in main.js).
 *   - tab: nav tab (data-tab value) to highlight while this route is active.
 *   - mount(params): called with the route's params object whenever this
 *     route becomes (or stays, with new params) active.
 *
 * `C360.router.redirect(fromPath, toPath)` registers an unconditional
 * redirect, used for tabs whose default view lives under a sub-path (e.g.
 * "/datasources" -> "/datasources/connectors").
 */
window.C360 = window.C360 || {};

(function (C360) {
  "use strict";

  var routeTable = [];     // [{ pattern, regex, keys, section, tab, mount }], in registration order
  var redirectTable = {};  // exact path -> exact path
  var knownSections = [];  // every section id referenced by any registered route
  var defaultPath = "/";
  var activeLocation = null; // { path, route, params } for the currently mounted route

  // "/segments/:id" -> { regex: /^\/segments\/([^/]+)$/, keys: ["id"] }
  function compilePattern(pattern) {
    var keys = [];
    var parts = pattern.split("/").map(function (segment) {
      if (segment.charAt(0) === ":") {
        keys.push(segment.slice(1));
        return "([^/]+)";
      }
      return segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    });
    return { regex: new RegExp("^" + parts.join("/") + "$"), keys: keys };
  }

  function define(pattern, config) {
    config = config || {};
    var compiled = compilePattern(pattern);
    var route = {
      pattern: pattern,
      regex: compiled.regex,
      keys: compiled.keys,
      section: config.section,
      tab: config.tab,
      mount: config.mount
    };
    routeTable.push(route);
    if (route.section && knownSections.indexOf(route.section) === -1) knownSections.push(route.section);
    return route;
  }

  function redirect(fromPath, toPath) { redirectTable[fromPath] = toPath; }

  function matchPath(path) {
    for (var i = 0; i < routeTable.length; i++) {
      var route = routeTable[i];
      var m = path.match(route.regex);
      if (m) {
        var params = {};
        route.keys.forEach(function (key, idx) { params[key] = decodeURIComponent(m[idx + 1]); });
        return { route: route, params: params };
      }
    }
    return null;
  }

  function activateSection(sectionId) {
    knownSections.forEach(function (id) { $("#" + id).toggleClass("hidden", id !== sectionId); });
  }

  function activateTab(tab) {
    $(".tab-btn").removeClass("active");
    if (tab) $(".tab-btn[data-tab='" + tab + "']").addClass("active");
  }

  function currentPath() {
    var hash = (location.hash || "").replace(/^#/, "");
    return hash || defaultPath;
  }

  function isSameLocation(a, b) {
    return !!a && !!b && a.route === b.route && JSON.stringify(a.params) === JSON.stringify(b.params);
  }

  // Re-reads location.hash, matches it against the route table and mounts
  // the matching route. Skips remounting if the path resolves to the exact
  // same route+params already active (e.g. a no-op hash write), mirroring
  // how a React Router <Route> only re-renders when its params change.
  function resolve() {
    var path = currentPath();

    if (Object.prototype.hasOwnProperty.call(redirectTable, path)) {
      navigate(redirectTable[path], { replace: true });
      return;
    }

    var matched = matchPath(path);
    if (!matched) {
      navigate(defaultPath, { replace: true });
      return;
    }

    var next = { path: path, route: matched.route, params: matched.params };
    if (isSameLocation(activeLocation, next)) return;
    activeLocation = next;

    if (matched.route.section) activateSection(matched.route.section);
    activateTab(matched.route.tab);
    if (matched.route.mount) matched.route.mount(matched.params);
  }

  function navigate(path) {
    var newHash = "#" + path;
    if (location.hash === newHash) { resolve(); return; } // hashchange won't fire on a no-op write
    location.hash = newHash;
  }

  function start(initialDefaultPath) {
    if (initialDefaultPath) defaultPath = initialDefaultPath;
    $(window).on("hashchange", resolve);
    resolve();
  }

  C360.router = {
    define: define,
    redirect: redirect,
    navigate: navigate,
    start: start,
    current: function () { return activeLocation; }
  };
})(window.C360);
