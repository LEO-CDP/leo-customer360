# Customer 360 Admin Frontend

This folder contains the FastAPI-backed admin UI for the Customer 360 platform. The app is a single-page shell that renders the browser UI, loads shared templates, and fetches all business data live from `customer360-api` via AJAX.

The frontend is intentionally thin: it does not own the business logic or database access. It acts as a presentation layer over the API and handles routing, auth/session state, templates, filters, and chart rendering in the browser.

## Current app responsibilities

- Serves the SPA shell and static assets from `base-templates/index.html`
- Injects runtime config (`apiBase` + tenant id) from environment variables
- Handles auth/session flows for dev credentials and Keycloak SSO
- Loads and displays profiles, personas, attributes, scoring models, datasources, and admin metadata
- Renders charts, dashboards, and tables using jQuery + Handlebars + Chart.js
- Routes the browser via hash-based navigation without a framework build step

## UX of Menu 

For a marketing user, the natural journey is:

```
Who are my customers?
        ↓
Who are the important customer types?
        ↓
Which audience should I target?
        ↓
What campaign should I run?
        ↓
What happened?
```

```
OVERVIEW
   │
   ├── PROFILES
   ├── PERSONAS
   ├── SEGMENTS
   ├── CAMPAIGNS
   ├── ANALYTICS
   │
   ├── DATA
   │    ├── Data Sources
   │    └── Attributes
   │
   ├── INTELLIGENCE
   │    └── Scoring
   │
   └── ADMIN
```

| Order | Menu             | UX rationale                                              |
| ----: | ---------------- | --------------------------------------------------------- |
|     1 | **Overview**     | Entry point: health, KPIs, customer growth, activity      |
|     2 | **Profiles**     | Core value of a Customer 360: *Who are my customers?*     |
|     3 | **Personas**     | Understand customer archetypes: *Who are these people?*   |
|     4 | **Segments**     | Operationalize personas into actionable audiences         |
|     5 | **Campaigns**    | Activate audiences: *What do we do with them?*            |
|     6 | **Analytics**    | Measure outcomes: *Did it work?*                          |
|     7 | **Data Sources** | Technical/data configuration; less frequently visited     |
|     8 | **Attributes**   | Define/manage the customer data model                     |
|     9 | **Scoring**      | Advanced intelligence: propensity, CLV, churn, lead score |
|    10 | **Admin**        | Tenant, users, permissions, system configuration          |


## Current folder structure

```text
frontend-admin/
├── app.py                         # FastAPI entrypoint and config injection
├── Dockerfile
├── README.md
├── requirements.txt
├── start.sh
├── stop.sh
├── restart.sh
├── .env                          # optional local overrides for the browser app
├── logs/
├── base-templates/
│   └── index.html                # SPA shell, script includes, section containers
├── static/
│   ├── css/
│   │   └── app.css
│   ├── js/
│   │   ├── analytics.js
│   │   ├── attributes-view.js
│   │   ├── auth-view.js
│   │   ├── campaign-view.js
│   │   ├── data-source-view.js
│   │   ├── main.js
│   │   ├── overview-view.js
│   │   ├── persona-list-view.js
│   │   ├── placeholder-view.js
│   │   ├── profile-detail-view.js
│   │   ├── profile-list-view.js
│   │   ├── scoring-model-view.js
│   │   ├── segments-view.js
│   │   ├── system-user-view.js
│   │   └── common/
│   │       ├── config.js
│   │       ├── data-table-view.js
│   │       ├── formatters.js
│   │       ├── matrix-heatmap.js
│   │       ├── router.js
│   │       ├── templates.js
│   │       └── utils.js
│   ├── templates/
│   │   ├── tabs.html
│   │   ├── common/
│   │   ├── dashboard/
│   │   ├── metadata/
│   │   ├── persona/
│   │   ├── profile/
│   │   ├── segment/
│   │   ├── scoring/
│   │   ├── campaign/
│   │   └── admin/
│   └── ...
└── .venv/                        # created by start.sh if missing
```

## Runtime setup and config

The FastAPI app in `app.py` loads environment values from `frontend-admin/.env` when present, and falls back to defaults if none is set.

Relevant settings:

- `FRONTEND_API_HOSTNAME` (default: `http://localhost:8008`)
- `FRONTEND_TENANT_ID` (default: demo tenant UUID)
- `SSO_LOGIN` (default: `false` in local/dev)
- `HOST` / `PORT` for the frontend itself (`0.0.0.0:8890` by default)
- `BUILD_VERSION` is embedded into Docker images at build time in
        `yyyy-mm-dd-HH-MM` format. CI supplies the UTC value; local non-container
        development falls back to `dev`.

Build the image with an explicit version:

```bash
docker build \
        --build-arg BUILD_VERSION="$(date -u +%Y-%m-%d-%H-%M)" \
        -t customer360-frontend:local \
        frontend-admin
```

The displayed build version is read once when the application starts from the
image's `BUILD_VERSION` value. It is not regenerated for each request.

These values are injected into the browser at render time through `window.C360_SERVER_CONFIG`, and then read by `static/js/common/config.js`.

## Authentication and session flow

The app has a real client-side auth layer, not a static-only tenant selector.

### Dev mode

When `SSO_LOGIN=false`/`false`-style values are used, the UI shows a local credential form via `auth-view.js` and posts to `POST /auth/login` on `customer360-api`.

- `C360.config.login(username, password)` sends the call
- the response is saved into localStorage as the current dev session
- `C360.config.api()` adds `X-Tenant-Id` and `Authorization: Bearer <token>` automatically if a token is present
- user metadata is kept under keys like `c360.accessToken`, `c360.devUser`, `c360.tenantId`

### SSO mode

When `SSO_LOGIN=true`, the UI shows a Keycloak sign-in flow.

- `auth-view.js` loads system metadata from `/api/v1/metadata`
- the app redirects the browser to the OIDC authorize URL using the metadata from the API
- the callback URL is handled by `tryHandleSsoCallback()`
- the returned code is exchanged with `POST /auth/callback`
- the resulting `access_token`/`id_token` are stored in localStorage

This app therefore performs the client-side session handling expected by the API, while the real token verification still happens in `customer360-api`.

## API client

The single source of truth for cross-view HTTP traffic is `static/js/common/config.js`.

`C360.config.api(path, params, method)` does the following:

- builds the URL using the configured `apiBase`
- sends `X-Tenant-Id` on every request
- sends `Authorization` when a bearer token exists
- sends `X-User-Id` when available
- uses JSON for POST/PATCH/PUT bodies instead of form-encoded payloads

The helper is reused across profiles, segments, attributes, scoring models, datasources, and admin management screens.

## Routing model

The app uses a small hash-router implemented in `static/js/common/router.js`.

Each view registers its own routes at load time with `C360.router.define(pattern, config)`, for example:

```js
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

This lets each module own its routes without touching a central switch statement in `main.js`.

## Current route and view map

The current UI includes these primary sections:

- Overview: `overview-view.js`
- Profiles: `profile-list-view.js`, `profile-detail-view.js`
- Segments: `segments-view.js`
- Attributes: `attributes-view.js`
- Data Sources: `data-source-view.js`
- Scoring: `scoring-model-view.js`
- Campaigns: `campaign-view.js`
- Personas: `persona-list-view.js`
- Admin: `system-user-view.js`
- Placeholder/default routes: `placeholder-view.js`

The main layout in `base-templates/index.html` defines the sections for these views and then loads each JavaScript module in order. `main.js` starts the app after the templates are loaded and the auth gate has completed.

## Template and shared UI model

The UI uses a shared component pattern:

- `common/templates.js` loads and registers Handlebars templates
- `common/data-table-view.js` provides reusable list/table behavior
- `common/formatters.js` centralizes display-formatting helpers
- `common/router.js` handles hash-based route changes
- templates live under `static/templates/` and are rendered into the section containers in `index.html`

This is the basis for profiles, segments, datasources, scoring models, and system-user lists.

## Start / stop / restart

From this folder, the app is started via:

```bash
./start.sh
```

This script:

- creates a local `.venv` if missing
- installs `requirements.txt`
- loads `.env` values when available
- starts `uvicorn app:app` in the background on `http://0.0.0.0:8890`
- writes logs to `logs/app.log` and the PID to `.uvicorn.pid`

Useful commands:

```bash
./stop.sh
./restart.sh
```

The frontend expects `customer360-api` to be running and reachable at the configured API hostname, typically `http://localhost:8008`.

## Development notes

- The app is served from the root of the local service, not from a bundle or SPA framework.
- All browser requests are made to `customer360-api` over HTTP; there is no separate backend here.
- The app uses `localStorage` for tenant and session persistence in the browser.
- `app.py` sets response headers via `SecurityHeadersMiddleware` and serves static files from `/static`.
- The health endpoint is exposed at `/health` and reports service status.

## Summary

The code in this folder is a modern, API-driven admin console for Customer 360. It is built around a FastAPI shell, shared JS utilities, hash-based routing, and browser-side data fetching from the backend API. The README here should be treated as a living document and kept in sync with the current view modules and auth flow in `app.py`, `static/js/common/*`, and the individual view files under `static/js/`.

