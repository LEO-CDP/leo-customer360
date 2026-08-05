# Customer 360 frontend-admin

`frontend-admin` is a FastAPI-served SPA shell built with Tailwind CSS (CDN),
jQuery, and Handlebars templates. It renders multiple API-backed views:

- Overview dashboard (`/overview`)
- Master profiles list + profile detail (`/profiles`, `/profiles/:id`)
- Segments list + segment detail (`/segments`, `/segments/:id`)
- Attribute catalog (`/attributes`)
- Analytics dashboard (`/analytics`)
- Campaign dashboard (`/campaigns`)
- Placeholders for not-yet-implemented routes (journeys/scoring/datasources/admin)

All business data is loaded from `customer360-api` via AJAX (`static/js/config.js`).
`app.py` in this folder only serves static assets and server-injected config.

## Recent change (important)

- Master profiles list pagination moved from `skip/limit + Load more` to
  `page/page_size + Previous/Next`.
- API contract for `GET /api/v1/master-profiles/` is now:
  - request params: `page`, `page_size` (plus existing filters)
  - response shape: `{ items: [...], pagination: {...} }`
- `static/js/data-table-view.js` now supports both:
  - legacy skip/limit list endpoints (default mode)
  - page-based envelope endpoints (`pagination: true`)

This was implemented in `static/js/list-view.js` only; other views continue to
work with existing endpoint shapes.

## Structure

```
app.py                             FastAPI app serving index + static files
jinja/index.html                   main HTML shell
jinja/config.js.j2                 server-rendered config payload

static/css/app.css                 frontend styles

static/js/config.js                API base/tenant config + ajax helper
static/js/data-table-view.js       shared list/table component (load-more + page mode)
static/js/templates.js             template loader + Handlebars registration
static/js/router.js                hash router
static/js/main.js                  bootstrap + route startup

static/js/list-view.js             profiles list route (/profiles)
static/js/profile-detail-view.js   profile detail route (/profiles/:id)
static/js/segments-view.js         segments routes (/segments, /segments/:id)
static/js/attributes-view.js       attributes route (/attributes)
static/js/overview-view.js         overview route (/overview)
static/js/analytics.js             analytics route (/analytics)
static/js/campaign-view.js         campaigns route (/campaigns)
static/js/placeholder-view.js      placeholder routes for unimplemented tabs

static/templates/tabs.html
static/templates/common/*          data-table head/rows, settings modal, placeholder
static/templates/dashboard/*       overview + analytics templates
static/templates/profile/*         profiles list + profile detail partials
static/templates/segment/*         segments list + detail templates
static/templates/metadata/*        attributes list template
static/templates/campaign/*        campaign dashboard template
```

Each profile detail card is its own partial, registered by
`static/js/templates.js` and included by `static/templates/profile/profile-details.html`.

## Admin & API session / auth flow

This UI is a thin, unauthenticated static client - **it does not implement a
login screen itself**. All session/auth handling actually happens in
`customer360-api` (see `../customer360-api/core/auth.py`), gated by that
service's `SSO_LOGIN` setting (`../customer360-api/.env` /
`core/config.py`):

- **`SSO_LOGIN=false` (local/dev - repo default).** There is no login.
  Every request this UI makes just carries an `X-Tenant-Id` header (see the
  `api()` helper in `static/js/config.js`), which `customer360-api` trusts
  directly (`core/auth.py::_apply_dev_tenant_headers`) to set the Postgres
  `app.tenant_id` session variable that Row-Level Security policies key off
  (see `database-schema.sql`). The "Admin" button (top right) opens
  `static/templates/common/settings-modal.html`, where you edit the API base URL and
  tenant id; `C360.config.save()` persists them to `localStorage`
  (`c360.apiBase` / `c360.tenantId`) and reloads the page. That's the entire
  "session management" this frontend has today -- a tenant selector, not a
  login.

- **`SSO_LOGIN=true` (production).** `customer360-api` requires every
  request except `/health` to carry `Authorization: Bearer <token>`. The
  token is validated against Keycloak's introspection endpoint and cached in
  Redis for its remaining TTL (`auth:token:<token>` --
  `_introspect_with_keycloak`/`_cache_token`), then `(tenant_id, user_id)` is
  resolved either from custom claims on the token or by looking up/
  auto-provisioning a `sys_user` row keyed by the token's `sub` claim
  (`_get_or_create_user_on_login`); that resolved identity is itself cached
  in Redis for 5 minutes (`auth:identity:<sub>`) so it isn't re-derived on
  every call. **This frontend does not yet drive that flow** - there's no
  Keycloak redirect/login page and no bearer-token storage here. Wiring it
  up means: adding a login step (redirect to Keycloak, handle the callback,
  store the access token), then adding an `Authorization` header next to
  `X-Tenant-Id` inside `api()` in `static/js/config.js`. Every view module
  calls that one function for its ajax requests, so it's the single choke
  point for auth headers -- the natural home for a real login page is the
  already-registered (placeholder) `/admin/users` route, see
  [Routing](#routing-how-it-works-and-how-to-add-a-new-view) below.

What actually runs today end to end (dev mode):

```mermaid
sequenceDiagram
    %% Added double quotes around aliases with spaces, dashes, or special characters
    participant Browser
    participant Admin as "frontend-admin (app.py)"
    participant API as "customer360-api"
    participant DB as PostgreSQL

    %% Initial frontend load
    Browser->>Admin: GET /
    Admin-->>Browser: index.html + static/js/*.js
    
    %% Configuration load
    Browser->>Admin: GET /static/js/config.js
    Admin-->>Browser: jinja/config.js.j2 rendered (apiBase, tenantId from env)
    
    %% Data fetch
    Browser->>API: GET /api/v1/master-profiles/ (header X-Tenant-Id)
    
    %% API processing lifecycle
    activate API
    API->>API: auth_middleware -- SSO_LOGIN=false, trust X-Tenant-Id
    
    %% SYNTAX FIX: Removed the semicolon (;) which breaks the Mermaid parser.
    %% ARCHITECTURE FIX: Keep SET LOCAL so the tenant scope doesn't leak in connection pools.
    API->>DB: SET LOCAL app.tenant_id and SELECT ... (RLS-scoped)
    
    activate DB
    DB-->>API: rows
    deactivate DB
    
    API-->>Browser: JSON
    deactivate API
```

## Routing: how it works, and how to add a new view

The whole UI is one `index.html` page; navigation is a small,
React-Router-inspired hash router (`static/js/router.js`) - same idea as
`<Route path="...">` + params, just implemented with plain jQuery and no
build step.

- A **path** looks like a React Router path: `"/segments"`,
  `"/segments/:id"` (`:id` is captured as a param).
- Every view module registers the path(s) it owns with
  `C360.router.define(pattern, config)` **at script-load time**. `main.js`
  never lists individual views -- that's the whole point: adding a view
  never touches `main.js` or `router.js`.
- `config` is `{ section, tab, mount }`:
  - `section`: id of the `<section>` this route renders into. The router
    remembers every section id ever registered and hides all-but-the-
    active-one on every navigation, so nobody maintains a hand-written
    "hide every other view" list.
  - `tab`: which nav button (`data-tab`) to highlight while the route is active.
  - `mount(params)`: called to actually load/render the view.
- `C360.router.redirect(fromPath, toPath)` covers a tab whose real content
  lives one level down (e.g. `/datasources` -> `/datasources/connectors`).
- `C360.router.navigate(path)` changes `location.hash`, which re-matches and
  mounts the new route -- this is how row clicks/back buttons navigate,
  instead of code directly toggling elements.

**Real example** -- the Segments list + detail routes, from the bottom of
`static/js/segments-view.js` (registered after `load`/`showDetail` are
defined earlier in the file):

```js
// Owns the "/segments" (list) and "/segments/:id" (detail) routes. Both
// share the single "view-segments" section; showList()/showDetail() toggle
// the two sub-panels nested inside it, the same way a React Router layout
// route renders a child <Outlet/>.
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
```

and navigating into the detail route, from that same file's `bindEvents()`:

```js
$(document).on("click", ".segment-row", function () {
  C360.router.navigate("/segments/" + $(this).data("id"));
});
```

**Adding a brand new view** (e.g. turning the "Scoring Models" placeholder
into a real listing + detail view -- `static/js/placeholder-view.js`
currently owns `/scoring` and `/scoring/:id` as placeholders):

1. Create `static/js/scoring-view.js` modeled on `segments-view.js`: a
   `load()`/`loadList()` that calls `C360.config.api(...)` and renders a
   Handlebars template into a new `<section id="view-scoring">` in
  `index.html` (add the section + its shell template under
  `static/templates/`, same pattern as `view-segments`/`segment/segments-list.html`).
2. At the bottom of that file, register its routes:
   ```js
   C360.router.define("/scoring", { section: "view-scoring", tab: "scoring", mount: load });
   C360.router.define("/scoring/:id", { section: "view-scoring", tab: "scoring", mount: function (p) { loadDetail(p.id); } });
   ```
3. Delete the two `/scoring*` entries from the `ENTRIES` array in
   `static/js/placeholder-view.js` (they'd otherwise never match, since
   `placeholder-view.js` loads after `scoring-view.js` -- see next step --
   and the router uses first-match-wins).
4. Add `<script src="static/js/scoring-view.js"></script>` to `index.html`
   (anywhere after `router.js`; order among view modules doesn't matter) and
   call `C360.scoringView.bindEvents()` from `main.js`'s bootstrap, next to
   the other `bindEvents()` calls.

No changes to `router.js` or the tab-click handling in `main.js` are ever
needed for a new view -- the route table living inside each view's own file
is the entire mechanism.

## Run

1. Start `customer360-api` (see `../customer360-api/start.sh`) so it's listening on
   `http://localhost:8008`.
2. Start this app with `./start.sh` (installs/reuses a venv, then runs
   `uvicorn app:app` on `http://localhost:8890`, backgrounded; logs to
   `logs/app.log`, PID in `.uvicorn.pid`). Stop it with `./stop.sh`, or
   restart with `./restart.sh`. `FRONTEND_API_HOSTNAME`/`FRONTEND_TENANT_ID`
   (from `.env`, see `../.env`) are baked into `static/js/config.js` on every
   request by `app.py` (via `jinja/config.js.j2`) -- must be a hostname
   reachable from the **browser**, not just this container.

    Alternatively, serve the folder with any plain static file server (opening
   via `file://` will be blocked by the browser's CORS policy for the
   `static/templates/*.html` and API `fetch`/XHR calls); `static/js/config.js`
   on disk has hardcoded defaults for exactly this case:
   ```bash
    cd frontend-admin
   python3 -m http.server 8890
   ```
3. Open `http://localhost:8890/index.html`, then click the "Admin" button
   (top right) to change the API base URL or the `X-Tenant-Id` dev header
   (used for Postgres Row-Level Security when `SSO_LOGIN=false`) if your
   setup differs from the defaults -- see
   [Admin & API session / auth flow](#admin--api-session--auth-flow) above.

## DataTableView integration notes

`static/js/data-table-view.js` is intentionally shared by multiple features.
Current usage patterns:

- Profiles (`static/js/list-view.js`): `pagination: true`
  - sends `page` + `page_size`
  - expects `{ items, pagination }`
  - uses `bindPagination()` with Previous/Next buttons

- Segments (`static/js/segments-view.js`): default mode
  - sends `skip` + `limit`
  - expects a plain list
  - uses `bindLoadMore()`

- Attributes (`static/js/attributes-view.js`): `clientSide: true`
  - fetches one list and filters/searches locally

- Campaign dashboard table (`static/js/campaign-view.js`): default mode
  - internally translates `skip/limit` to campaign API `page/page_size`
  - unwraps API response to a plain `items` array before returning to the shared component

When adding or changing a table view, choose one mode explicitly and keep
request/response shape consistent with that mode.

