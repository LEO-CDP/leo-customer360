# `backend-system/` — Dagster orchestration for the Customer 360 backend

This directory contains the backend services that run as Dagster code locations for the Customer 360 platform. It is separate from `customer360-api/`, which is the request/response API layer.

The current workspace includes both real service implementations and placeholder service skeletons that are ready for future business logic.

```
backend-system/
├── workspace.yaml                     # Dagster workspace: all active code locations
├── requirements-dev.txt              # Dagster webserver/daemon dependencies for local UI
├── start.sh / stop.sh / restart.sh    # local dev helpers for Dagster UI + daemon
├── .dagster_home/                    # local Dagster metadata / run history
├── logs/                             # local Dagster logs
│
├── identity_resolution/              # implemented service: CIR / identity matching + merge
│   ├── dagster_defs.py               # identity_resolution_job + sensor
│   ├── worker.py                     # containerized entrypoint used by Docker/local loop
│   ├── identity_resolution/          # business logic package
│   ├── requirements.txt
│   └── tests/
│
├── segmentation/                     # implemented service: segment recomputation
│   ├── dagster_defs.py               # segmentation_job + sensor
│   ├── segmentation/                 # business logic package
│   ├── requirements.txt
│   └── tests/
│
├── scoring/                          # placeholder service skeleton
│   └── dagster_defs.py
├── analytics/                        # placeholder service skeleton
│   └── dagster_defs.py
├── data_synch/                       # placeholder service skeleton
│   └── dagster_defs.py
├── email_engine/                     # placeholder service skeleton
│   └── dagster_defs.py
├── notification_engine/              # placeholder service skeleton
│   └── dagster_defs.py
├── campaign_orchestration/           # placeholder service skeleton
│   └── dagster_defs.py
├── personalization/                 # placeholder service skeleton
│   └── dagster_defs.py
├── Dockerfile                        # backend-system container image definition
├── .env                              # optional local env file for Dagster / DB settings
└── workspace.yaml
```

## Current service status

| Service | Status | Notes |
|---|---|---|
| `identity_resolution` | Implemented | Real identity resolution job and poll sensor |
| `segmentation` | Implemented | Recomputes active segment membership and profile tags |
| `scoring` | Placeholder | Minimal job skeleton only |
| `analytics` | Placeholder | Minimal job skeleton only |
| `data_synch` | Placeholder | Minimal job skeleton only |
| `email_engine` | Placeholder | Minimal job skeleton only |
| `notification_engine` | Placeholder | Minimal job skeleton only |
| `campaign_orchestration` | Placeholder | Minimal job skeleton only |
| `personalization` | Placeholder | Minimal job skeleton only |

---

## How the Dagster workspace is wired

Each service folder is a separate Dagster code location. A code location has a Python file such as `dagster_defs.py` that exposes a module-level `defs = Definitions(...)` object with jobs, ops, sensors, and/or schedules.

The top-level `workspace.yaml` loads each code location explicitly:

```yaml
load_from:
  - python_file:
      relative_path: identity_resolution/dagster_defs.py
      location_name: identity_resolution
  - python_file:
      relative_path: scoring/dagster_defs.py
      location_name: scoring
  - python_file:
      relative_path: segmentation/dagster_defs.py
      location_name: segmentation
  - python_file:
      relative_path: analytics/dagster_defs.py
      location_name: analytics
  - python_file:
      relative_path: data_synch/dagster_defs.py
      location_name: data_synch
  - python_file:
      relative_path: email_engine/dagster_defs.py
      location_name: email_engine
  - python_file:
      relative_path: notification_engine/dagster_defs.py
      location_name: notification_engine
  - python_file:
      relative_path: campaign_orchestration/dagster_defs.py
      location_name: campaign_orchestration
  - python_file:
      relative_path: personalization/dagster_defs.py
      location_name: personalization
```

This means the Dagster UI shows a single workspace with multiple code locations, each isolated in its own Python process.

### Active implementations

#### `identity_resolution`

This service runs the Customer Identity Resolution pipeline. The Dagster job is `identity_resolution_job`, backed by `run_daily_identity_resolution()` from the service's business logic. A sensor named `identity_resolution_poll_sensor` can request repeated runs based on `CIR_POLL_INTERVAL_SECONDS`, but it is stopped by default in the current setup because the containerized `worker.py` loop already drives the same logic in production-style runs.

#### `segmentation`

This service recomputes active segment membership and synchronizes tags into `cdp_master_profiles`. The Dagster job is `segmentation_job` and the sensor is `segmentation_poll_sensor`. The poll sensor checks for profile changes and only requests new runs when activity has occurred since the last cursor checkpoint.

### Placeholder code locations

The following folders currently provide the basic Dagster skeletons needed for future services:

- `scoring`
- `analytics`
- `data_synch`
- `email_engine`
- `notification_engine`
- `campaign_orchestration`
- `personalization`

Each of these files defines a simple `@op` + `@job` pattern and logs `started` / `done` to confirm the Dagster run is healthy before business logic is added.

---

## Local development

The local dev scripts are meant for running Dagster locally from this directory:

```bash
cd backend-system
./start.sh
```

This creates or reuses a shared `.venv`, installs the required dependencies for the service set being used during local development, and launches the Dagster webserver and daemon.

Then open:

```text
http://localhost:3000
```

You should see the registered code locations in the Dagster UI. The services that perform real work (`identity_resolution` and `segmentation`) require the Customer 360 database stack to be running first.

Useful commands:

```bash
./stop.sh
./restart.sh
```

Important notes:

- Dagster metadata is stored under `backend-system/.dagster_home` by default.
- `logs/dagster.log` contains the local run logs.
- The root `.env` file can be used as the single config source for local environment variables.

---

## Environment and scheduling variables

The local Dagster setup uses a few environment variables that control polling behavior and host binding:

| Variable | Default | Purpose |
|---|---|---|
| `DAGSTER_UI_HOST` | `127.0.0.1` | Bind address for the Dagster webserver |
| `DAGSTER_UI_PORT` | `3000` | Host port for the Dagster UI |
| `DAGSTER_HOME` | `backend-system/.dagster_home` | Persistent run/event storage for local dev |
| `CIR_POLL_INTERVAL_SECONDS` | `30` | Interval used by the identity resolution poll sensor |
| `SEGMENTATION_POLL_INTERVAL_SECONDS` | `10` | Interval used by the segmentation poll sensor |

---

## Adding a new backend service

When adding a new service, the usual pattern is:

1. Create the service folder with its own Python package and `requirements.txt`.
2. Add a `dagster_defs.py` file exposing at least one `@op` and one `@job`.
3. Register the code location in `workspace.yaml`.
4. Ensure the local startup flow installs the additional dependency set if needed.
5. Add tests under the service folder and then connect the service to any upstream/downstream API or orchestration flow.

The existing placeholder jobs are the best templates for new service scaffolds.

---

## Summary

`backend-system/` is the orchestration layer for the Customer 360 platform: it gives each backend capability a Dagster code location, keeps the jobs observable in one UI, and provides the foundation for future production deployment patterns. The repo currently contains one active identity-resolution implementation and one active segmentation implementation, with the rest of the services intentionally scaffolded and ready to be filled with real logic.
