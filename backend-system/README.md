# `backend-system/` — Dagster Orchestration for the Customer 360 Backend

Last updated: August 26, 2026

This directory contains the backend data-processing and orchestration services for the **Customer 360 / CDP platform**. Each backend capability is managed as a **Dagster code location** and can contain its own jobs, ops, sensors, schedules, business logic, dependencies, and tests.

The `backend-system/` is separate from `customer360-api/`, which provides the request/response API layer for applications and administrative operations.

The architecture follows a domain-oriented CDP pipeline:

```text
RAW DATA
JSONL / S3 / MinIO
        |
        v
   data_synch
        |
        v
     Polars
 scan_ndjson()
        |
        +-------------------+
        |                   |
        v                   v
identity_resolution     analytics
        |                   |
        |                   +--> Customer Features
        |                   +--> Behavioral Metrics
        |                   +--> Event Aggregations
        |                   +--> RFM / Analytical Features
        |                   |
        +---------+---------+
                  |
                  v
            CUSTOMER 360
                  |
        +---------+---------+----------------+
        |                   |                |
        v                   v                v
     scoring          segmentation    personalization
        |                   |                |
        +-------------------+----------------+
                            |
                            v
                       campaign_activation
                            |
                  +---------+---------+
                  |                   |
                  v                   v
             email_engine      notification_engine
                  |                   |
                  +---------+---------+
                            |
                            v
                     Customer Events
                            |
                            v
                       RAW JSONL
                            |
                            +-------------------->
                                 analytics
```

The core design principle is:

> **Dagster orchestrates the CDP domain pipelines; Polars performs high-performance data transformation; S3/MinIO stores raw and curated analytical data; PostgreSQL serves the operational Customer 360 layer; and activation services generate new behavioral data that feeds back into analytics.**

The diagrams in this document show the intended logical data flow. Today, only
`identity_resolution`, `segmentation`, and `analytics` contain implemented
processing; the other six code locations are runnable placeholder jobs.

---

## Architecture

### 1. Domain-Oriented Dagster Architecture

The current workspace is organized into the following domains:

```text
backend-system/

├── data_synch/
├── identity_resolution/
├── analytics/
├── scoring/
├── segmentation/
├── personalization/
├── campaign_activation/
├── email_engine/
├── notification_engine/
└── logs/
```

Each directory represents a **business/data-processing domain**, not a separate infrastructure technology.
For example, Polars is not a separate Dagster domain. It is a processing engine used inside the appropriate Dagster assets and business logic, primarily for analytical transformations.

The resulting conceptual hierarchy is:

```text
Dagster
│
├── Data Ingestion
│   └── data_synch
│
├── Customer Identity
│   └── identity_resolution
│
├── Customer Analytics
│   └── analytics
│
├── Decisioning
│   ├── scoring
│   ├── segmentation
│   └── personalization
│
├── Activation
│   ├── campaign_activation
│   ├── email_engine
│   └── notification_engine
│
└── Observability
    └── logs
```

---

## 2. Data Architecture

The backend uses a layered data architecture.

```text
                 +----------------------+
                 |      DATA SOURCES    |
                 |                      |
                 | CRM / Web / Mobile   |
                 | POS / E-commerce     |
                 | Adjust / Zalo     |
                 | Forms / Surveys      |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |      data_synch       |
                 | Ingestion / Sync      |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |      RAW LAYER        |
                 |      S3 / MinIO       |
                 |                      |
                 |      JSONL / NDJSON   |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |       POLARS          |
                 |    scan_ndjson()      |
                 |                      |
                 | Normalize / Filter    |
                 | Join / Aggregate      |
                 | Feature Engineering   |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |    CURATED LAYER      |
                 |      Parquet          |
                 +----------+-----------+
                            |
              +-------------+-------------+
              |                           |
              v                           v
       +--------------+             +--------------+
       |  PostgreSQL  |             |   Analytics  |
       | Serving      |             |   Storage    |
       | Customer 360 |             | S3 / Parquet |
       +--------------+             +--------------+
```

### Raw Layer

The raw layer stores source data in JSONL/NDJSON format.

Example:

```json
{"customer_id":"C001","event":"purchase","revenue":1200,"event_timestamp":"2026-08-20T10:30:00"}
{"customer_id":"C002","event":"page_view","revenue":0,"event_timestamp":"2026-08-20T11:15:00"}
{"customer_id":"C001","event":"purchase","revenue":800,"event_timestamp":"2026-08-21T09:20:00"}
```

Raw data should remain as close as possible to the source representation so that it can be replayed, audited, or reprocessed.

### Curated Layer

After ingestion and normalization, analytical data should be converted to Parquet.

```text
JSONL
  |
  | Polars
  v
Parquet
```

Parquet is preferred for analytical workloads because it provides columnar storage, compression, typed schemas, and efficient selective reads.

### Serving Layer

PostgreSQL provides the operational Customer 360 serving layer used by the API and backend services.

Typical data includes:

* Master customer profiles
* Identity links
* Customer attributes
* Customer features
* Scores
* Segment membership
* Activation state

---

# Repository Structure

```text
backend-system/

├── workspace.yaml
│   # Dagster workspace: all active code locations
│
├── requirements-dev.txt
│   # Dagster webserver/daemon dependencies for local UI
│
├── start.sh
├── stop.sh
├── restart.sh
│   # Local development helpers
│
├── .dagster_home/
│   # Local Dagster metadata / run history
│
├── logs/
│   # Local Dagster logs
│
├── identity_resolution/
│   # Implemented: CIR / identity matching + merge
│   ├── dagster_defs.py
│   ├── worker.py
│   ├── identity_resolution/
│   ├── requirements.txt
│   └── tests/
│
├── segmentation/
│   # Implemented: segment recomputation
│   ├── dagster_defs.py
│   ├── segmentation/
│   ├── requirements.txt
│   └── tests/
│
├── analytics/
│   # Implemented: tracking-log aggregation
│   ├── dagster_defs.py
│   ├── source_analytics/
│   ├── requirements.txt
│   └── tests/
│
├── scoring/
│   # Placeholder service skeleton
│   └── dagster_defs.py
│
├── data_synch/
│   # Placeholder service skeleton
│   └── dagster_defs.py
│
├── email_engine/
│   # Placeholder service skeleton
│   └── dagster_defs.py
│
├── notification_engine/
│   # Placeholder service skeleton
│   └── dagster_defs.py
│
├── campaign_activation/
│   # Placeholder service skeleton
│   └── dagster_defs.py
│
├── personalization/
│   # Placeholder service skeleton
│   └── dagster_defs.py
│
├── Dockerfile
│   # Backend-system container image definition
│
└── .env
    # Optional local environment configuration
```

> `workspace.yaml` is listed once conceptually above; the repository should contain a single root workspace configuration.

---

# Current Service Status

| Service | Status | Dagster definitions | Responsibility |
| --- | --- | --- | --- |
| `identity_resolution` | Implemented | `identity_resolution_job`; `identity_resolution_poll_sensor` (running by default) | CIR matching, identity links, and master-profile merge |
| `segmentation` | Implemented | `segmentation_job`; `segmentation_poll_sensor` (running by default) | Active segment recomputation and profile tag synchronization |
| `analytics` | Implemented | `analytics_job`; `analytics_hourly_schedule` (running by default) | Hourly tracking JSONL aggregation from S3/MinIO |
| `scoring` | Placeholder | `scoring_job` | Customer scoring pipeline skeleton; currently sleeps and logs |
| `data_synch` | Placeholder | `data_synch_job` | Data ingestion and synchronization skeleton |
| `email_engine` | Placeholder | `email_engine_job` | Email activation pipeline skeleton |
| `notification_engine` | Placeholder | `notification_engine_job` | Push/SMS/in-app notification skeleton |
| `campaign_activation` | Placeholder | `campaign_activation_job` | Campaign activation skeleton |
| `personalization` | Placeholder | `scoring_job` (placeholder name) | Personalization and next-best-action skeleton |

The current implementation therefore represents the early production foundation of the CDP:

```text
IMPLEMENTED
    |
    +-- identity_resolution
    +-- segmentation
    +-- analytics

SCAFFOLDED
    |
    +-- scoring
    +-- data_synch
    +-- personalization
    +-- campaign_activation
    +-- email_engine
    +-- notification_engine
```

---

# Dagster Workspace

Each service folder is a separate Dagster code location.

A code location typically exposes a module-level:

```python
defs = Definitions(...)
```

object containing jobs, ops, sensors, schedules, or assets.

The root `workspace.yaml` loads the code locations explicitly.

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
      relative_path: campaign_activation/dagster_defs.py
      location_name: campaign_activation

  - python_file:
      relative_path: personalization/dagster_defs.py
      location_name: personalization
```

This gives the Dagster UI a single workspace containing multiple isolated code locations.

Each location can have its own:

* Jobs
* Ops
* Assets
* Sensors
* Schedules
* Dependencies
* Tests
* Business logic

This provides service-level isolation while maintaining a unified orchestration interface.

---

# Active Implementations

## `identity_resolution`

The `identity_resolution` service runs the Customer Identity Resolution pipeline.

The Dagster job is:

```text
identity_resolution_job
```

The job is backed by:

```text
run_daily_identity_resolution()
```

from the service's business logic package.

A sensor named:

```text
identity_resolution_poll_sensor
```

can request repeated runs based on:

```text
CIR_POLL_INTERVAL_SECONDS
```

The sensor is enabled by default in the unified Dagster deployment and replaces
the legacy `worker.py` polling loop for normal workspace operation.

### Responsibility

The identity-resolution pipeline establishes the identity backbone of Customer 360:

```text
Raw Profiles
      |
      v
Identity Resolution
      |
      +-- Matching
      +-- Evidence
      +-- Confidence
      +-- Identity Links
      |
      v
Master Profile
```

Downstream services should use the resolved `master_profile_id` rather than independently attempting to resolve identities.

---

## `segmentation`

The `segmentation` service recomputes active segment membership and synchronizes tags into:

```text
cdp_master_profiles
```

The Dagster job is:

```text
segmentation_job
```

The polling sensor is:

```text
segmentation_poll_sensor
```

The sensor checks for profile changes and requests new runs only when activity has occurred since the last cursor checkpoint.

Conceptually:

```text
Customer 360
     |
     +-- Profile attributes
     +-- Customer features
     +-- Scores
     |
     v
Segmentation
     |
     +-- Rule evaluation
     +-- Segment membership
     +-- Profile tags
     |
     v
Customer Audience
```

---

## `analytics`

The `analytics` service is currently responsible for hourly tracking-log aggregation.

It processes the first ten data sources from:

```text
sys_data_source
```

on an hourly UTC schedule.

For each source, it:

1. Lists the corresponding `data-tracking-{data_source_id}` S3/MinIO bucket.
2. Counts JSONL records by hour.
3. Increments the Redis hash field:

   ```text
   tracked-event
   ```
4. Adds newly processed records to:

   ```text
   sys_data_source.total_tracked_event
   ```
5. Maintains immutable object checkpoints.
6. Uses Redis leases and cursors to avoid overlapping processing.
7. Resumes from the last processed object instead of scanning the entire bucket.

The architecture is:

```text
S3 / MinIO
     |
     | JSONL tracking objects
     v
 analytics
     |
     v
 JSONL parsing / aggregation layer
     |
     +--> Redis
     |     tracked-event
     |
     +--> PostgreSQL
     |     total_tracked_event
     |
     +--> Checkpoints
```

The checkpoint design makes retries idempotent.

Each source has a Redis lease and cursor. Overlapping runs skip locked sources and continue from the last processed object.

### Analytics API

The Customer 360 API exposes:

```text
POST /api/v1/analytics/source-analytics/process
```

for platform-admin manual processing.

Status can be queried through:

```text
GET /api/v1/analytics/source-analytics/status
```

This exposes per-source state, cursors, and UI triggerability.

Individual Dagster run status can be queried through:

```text
GET /api/v1/analytics/source-analytics/status/{run_id}
```

A duplicate manual submission returns:

```text
409 Conflict
```

### Future Analytics Direction

The current hourly tracking aggregation is the implemented analytics job. It
counts valid JSONL records and updates Redis/PostgreSQL state; it does not yet
materialize the broader customer-feature or Parquet pipeline described below.
That broader pipeline remains the target direction.

The target analytical architecture is:

```text
Raw JSONL
    |
    v
Polars scan_ndjson()
    |
    v
Normalized Events
    |
    +-- Event Aggregation
    +-- Customer Metrics
    +-- Behavioral Features
    +-- RFM
    +-- Product Affinity
    +-- Engagement Metrics
    +-- Channel Metrics
    +-- Temporal Features
    |
    v
Curated Parquet
    |
    v
Customer Features
    |
    +--> scoring
    +--> segmentation
    +--> personalization
```

---

# Polars Data Processing

Polars is the recommended DataFrame and analytical processing library for future
CDP transformations.

It is a dependency of `analytics`, `segmentation`, and `personalization`, not a
separate service. The current tracking-log aggregation implementation uses
Python JSONL parsing; the lazy Polars examples below describe the planned
analytical processing path.

The preferred processing pattern for large JSONL/NDJSON files is:

```python
import polars as pl

events = (
    pl.scan_ndjson(
        "s3://cdp-raw/events/*.jsonl"
    )
    .filter(
        pl.col("event_timestamp")
        >= pl.datetime(2026, 1, 1)
    )
)
```

Transformations can then be composed lazily:

```python
features = (
    events
    .group_by("master_profile_id")
    .agg(
        pl.len().alias("event_count"),
        pl.col("revenue")
        .sum()
        .alias("total_revenue"),
        pl.col("product_id")
        .n_unique()
        .alias("products_viewed"),
        pl.col("event_timestamp")
        .max()
        .alias("last_activity"),
    )
    .collect()
)
```

### Why `scan_ndjson()`

The CDP raw layer uses JSONL because it is suitable for ingestion and append-oriented event data.

For large datasets, the pipeline should avoid eagerly loading the entire source:

```python
pl.read_ndjson(...)
```

Instead, the preferred approach is:

```python
pl.scan_ndjson(...)
```

This allows Polars to construct a lazy execution plan and reduce unnecessary memory usage.

The target flow is:

```text
JSONL
  |
  | scan_ndjson()
  v
Polars LazyFrame
  |
  +-- Filter
  +-- Projection
  +-- Join
  +-- Group By
  +-- Aggregation
  |
  v
Curated DataFrame
  |
  v
Parquet
```

---

# Curated Analytical Data

JSONL should primarily remain the raw ingestion format.

After normalization, analytical data should be persisted as Parquet:

```text
RAW
JSONL
  |
  v
POLARS
  |
  v
CURATED
PARQUET
```

Typical curated datasets include:

```text
customer_features/
behavioral_features/
customer_events/
transaction_features/
product_affinity/
campaign_engagement/
channel_metrics/
```

This allows downstream Dagster jobs to reuse analytical datasets without repeatedly parsing raw JSONL.

---

# Customer 360 Data Flow

The Customer 360 model is the central semantic layer connecting identity, analytics, decisioning, and activation.

```text
                 Raw Profiles
                      |
                      v
             identity_resolution
                      |
                      v
               Master Profile
                      |
          +-----------+-----------+
          |                       |
          v                       v
   Identity Links           Customer Events
                                  |
                                  v
                              analytics
                                  |
                                  v
                        Customer Features
                                  |
             +--------------------+--------------------+
             |                    |                    |
             v                    v                    v
          scoring          segmentation       personalization
             |                    |                    |
             +--------------------+--------------------+
                                  |
                                  v
                              campaign_activation
                                  |
                       +----------+----------+
                       |                     |
                       v                     v
                 email_engine       notification_engine
```

The important dependency is:

> **Identity Resolution establishes who the customer is; Analytics establishes what the customer does; Scoring and Segmentation establish what the customer means to the business; Personalization and Campaign determine what action should be taken.**

---

# Decisioning and Activation

## `scoring`

The future scoring service should consume Customer 360 features rather than raw events directly.

Potential outputs include:

```text
rfm_score
clv_score
churn_score
purchase_propensity
engagement_score
product_affinity_score
```

The intended flow is:

```text
Customer Features
      |
      v
   scoring
      |
      v
Customer Scores
```

---

## `segmentation`

Segmentation converts customer features and scores into actionable audiences.

```text
Customer Features
       +
Customer Scores
       +
Profile Attributes
       |
       v
 segmentation
       |
       v
Customer Segments
```

Examples include:

```text
NEW_CUSTOMER
LOYAL_CUSTOMER
HIGH_VALUE_ACTIVE
HIGH_VALUE_AT_RISK
CHURN_RISK
PRICE_SENSITIVE
PRODUCT_INTEREST_A
```

---

## `personalization`

Personalization consumes Customer 360 information, scores, and segments.

Potential outputs include:

```text
Product Recommendation
Content Recommendation
Next Best Action
Offer Selection
Channel Selection
```

The intended flow is:

```text
Customer 360
     |
     +-- Features
     +-- Scores
     +-- Segments
     |
     v
personalization
     |
     v
Personalized Decision
```

---

## `campaign_activation`

Campaign orchestration converts segments and personalization decisions into executable audiences.

```text
Segments
    +
Personalization
    |
    v
 campaign_activation
    |
    +-- Audience selection
    +-- Eligibility
    +-- Frequency control
    +-- Campaign configuration
    |
    v
Activation
```

---

## `email_engine`

The email engine will handle email-specific activation.

```text
Campaign
    |
    v
email_engine
    |
    +-- Recipient resolution
    +-- Template selection
    +-- Personalization
    +-- Delivery
    +-- Tracking
    |
    v
Email Provider
```

Email events should subsequently return to the CDP:

```text
Sent
Delivered
Opened
Clicked
Bounced
   |
   v
Raw JSONL
   |
   v
analytics
```

This creates a closed feedback loop.

---

## `notification_engine`

The notification engine is intended to support non-email activation channels such as:

```text
Push
SMS
In-App
Other Notification Channels
```

The intended flow is:

```text
Campaign
    |
    v
notification_engine
    |
    v
Notification Provider
    |
    v
Customer Response
    |
    v
Analytics
```

---

# Closed-Loop CDP Architecture

The complete Customer 360 architecture forms a feedback loop rather than a one-way ETL pipeline.

```text
                         +----------------+
                         |  DATA SOURCES  |
                         +-------+--------+
                                 |
                                 v
                         +----------------+
                         |  data_synch    |
                         +-------+--------+
                                 |
                                 v
                         +----------------+
                         |  RAW JSONL     |
                         |  S3 / MinIO    |
                         +-------+--------+
                                 |
                                 v
                         +----------------+
                         |     POLARS     |
                         | scan_ndjson()  |
                         +-------+--------+
                                 |
                  +--------------+--------------+
                  |                             |
                  v                             v
        +-------------------+          +----------------+
        | identity_resolution|          |   analytics    |
        +---------+---------+          +--------+-------+
                  |                             |
                  |                             v
                  |                    Customer Features
                  |                             |
                  +--------------+--------------+
                                 |
                                 v
                         +----------------+
                         | CUSTOMER 360   |
                         +-------+--------+
                                 |
                  +--------------+--------------+
                  |              |              |
                  v              v              v
              scoring      segmentation   personalization
                  |              |              |
                  +--------------+--------------+
                                 |
                                 v
                           +-----------+
                           | campaign_activation |
                           +-----+-----+
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
              email_engine          notification_engine
                    |                         |
                    +------------+------------+
                                 |
                                 v
                        Customer Response
                                 |
                                 v
                             RAW JSONL
                                 |
                                 +--------------+
                                                |
                                                v
                                            analytics
```

The resulting lifecycle is:

```text
INGEST
   ↓
RESOLVE
   ↓
UNDERSTAND
   ↓
SCORE
   ↓
SEGMENT
   ↓
PERSONALIZE
   ↓
ACTIVATE
   ↓
CAPTURE RESPONSE
   ↓
ANALYZE
   ↓
REPEAT
```

---

# Placeholder Code Locations

The following code locations currently provide the basic Dagster skeleton required for future implementation:

```text
scoring
data_synch
email_engine
notification_engine
campaign_activation
personalization
```

Each currently defines a basic `@op` + `@job` pattern and logs:

```text
started
done
```

This confirms that the Dagster code location can load and execute correctly before production business logic is introduced.

The placeholder structure is intentionally lightweight so each service can evolve independently.

---

# Local Development

The local development scripts run Dagster from the `backend-system` directory.

```bash
cd backend-system

./start.sh
```

The startup process creates or reuses the shared `.venv`, installs the required dependencies for the configured service set, and launches the Dagster webserver and daemon.

Open:

```text
http://localhost:3000
```

The Dagster UI should display all registered code locations.

The services performing real database work, particularly:

```text
identity_resolution
segmentation
```

require the Customer 360 database stack to be running first.

Useful commands:

```bash
./stop.sh
```

```bash
./restart.sh
```

---

# Local Runtime Files

Dagster metadata is stored under:

```text
backend-system/.dagster_home
```

Local Dagster logs are stored under:

```text
backend-system/logs/
```

The main local log file is:

```text
backend-system/logs/dagster.log
```

The root `.env` file can be used as the common configuration source for local environment variables.

---

# Environment Variables

The local Dagster setup currently uses the following variables.

| Variable                             | Default                        | Purpose                                             |
| ------------------------------------ | ------------------------------ | --------------------------------------------------- |
| `DAGSTER_UI_HOST`                    | `127.0.0.1`                    | Bind address for the Dagster webserver              |
| `DAGSTER_UI_PORT`                    | `3000`                         | Host port for the Dagster UI                        |
| `DAGSTER_HOME`                       | `backend-system/.dagster_home` | Persistent run/event storage for local development  |
| `CIR_POLL_INTERVAL_SECONDS`          | `30`                           | Polling interval for the identity-resolution sensor |
| `SEGMENTATION_POLL_INTERVAL_SECONDS` | `10`                           | Polling interval for the segmentation sensor        |

Placeholder jobs also accept service-specific sleep settings, each defaulting
to `2` seconds:

```text
SCORING_PLACEHOLDER_SLEEP_SECONDS
DATA_SYNCH_PLACEHOLDER_SLEEP_SECONDS
EMAIL_ENGINE_PLACEHOLDER_SLEEP_SECONDS
NOTIFICATION_ENGINE_PLACEHOLDER_SLEEP_SECONDS
CAMPAIGN_ACTIVATION_PLACEHOLDER_SLEEP_SECONDS
PERSONALIZATION_PLACEHOLDER_SLEEP_SECONDS
```

Analytics additionally reads the database, Redis, S3/MinIO, and
`ANALYTICS_DATA_SOURCE_LIMIT` settings used by its tracking-log processor.

---

# Dependencies

Each code location uses its own `requirements.txt`; the dependency sets are
intentionally not identical:

| Services | Declared dependency profile |
| --- | --- |
| `identity_resolution` | PostgreSQL driver, test tooling, dotenv, Gemini SDK, Pydantic, and Dagster |
| `analytics`, `segmentation`, `personalization` | PostgreSQL, S3/MinIO, Redis, dotenv, test tooling, Pandas, Polars, and Dagster |
| `scoring` | Dagster and `pymc-marketing` |
| `data_synch`, `email_engine`, `notification_engine`, `campaign_activation` | Dagster only |

The analytical-service dependency set includes:

```text
dagster>=1.9,<2
psycopg2-binary>=2.9,<3
boto3>=1.34,<2
redis>=5.0,<6
python-dotenv>=1.0,<2
pytest>=7.4,<9
pandas>=2.2,<3
polars>=1.0,<2
```

### Processing responsibilities

| Library       | Responsibility                                        |
| ------------- | ----------------------------------------------------- |
| Dagster       | Orchestration and scheduling                          |
| Polars        | High-performance analytical transformation            |
| Pandas        | Compatibility and smaller/ad-hoc analytical workloads |
| Boto3         | S3/MinIO integration                                  |
| Psycopg2      | PostgreSQL connectivity                               |
| Redis         | Cache, leases, cursors and low-latency state          |
| Python-dotenv | Local environment configuration                       |
| Pytest        | Automated testing                                     |

Polars and Pandas serve different purposes and can coexist. Polars should be
preferred for future large-scale production transformations, especially
JSONL/Parquet processing.

---

# Adding a New Backend Service

When adding a new service, follow the existing domain-oriented pattern.

## Step 1 — Create the service directory

```text
new_service/
├── dagster_defs.py
├── requirements.txt
├── new_service/
└── tests/
```

## Step 2 — Define the Dagster entry point

Create:

```text
new_service/dagster_defs.py
```

and expose:

```python
defs = Definitions(...)
```

with at least one job or asset.

## Step 3 — Register the code location

Add the service to:

```text
workspace.yaml
```

For example:

```yaml
- python_file:
    relative_path: new_service/dagster_defs.py
    location_name: new_service
```

## Step 4 — Add dependencies

Add service-specific dependencies to:

```text
new_service/requirements.txt
```

Do not add domain-specific dependencies globally unless multiple services genuinely require them.

## Step 5 — Add tests

Place tests under:

```text
new_service/tests/
```

## Step 6 — Connect upstream and downstream dependencies

Document:

```text
Input
  |
  v
New Service
  |
  v
Output
```

and identify which existing Customer 360 domains consume or produce the data.

---

# Recommended Domain Dependencies

The target dependency model is:

```text
data_synch
     |
     +----------------------+
     |                      |
     v                      v
identity_resolution      analytics
     |                      |
     +----------+-----------+
                |
                v
          Customer 360
                |
       +--------+--------+
       |        |        |
       v        v        v
    scoring segmentation personalization
       |        |        |
       +--------+--------+
                |
                v
             campaign_activation
                |
        +-------+-------+
        |               |
        v               v
 email_engine   notification_engine
        |               |
        +-------+-------+
                |
                v
          Customer Events
                |
                v
             analytics
```

This dependency model should be treated as the **logical data flow**. Individual Dagster code locations remain independently deployable and observable.

---

# Idempotency and Reliability

CDP pipelines must be designed to tolerate retries and repeated execution.

The `analytics` implementation already follows this principle through:

* Immutable object checkpoints
* Redis leases
* Per-source cursors
* Duplicate-run protection
* Incremental processing
* Resume-from-cursor behavior

The same principles should be applied to future services.

For example:

```text
Input Event
    |
    v
Deterministic Processing
    |
    v
Idempotent Output
```

A Dagster retry should not result in duplicated customer features, duplicated campaign audiences, or duplicated activation events.

---

# Observability

The `logs` area and Dagster's native run/event metadata provide operational visibility into pipeline execution.

At minimum, each production service should expose:

* Run status
* Start/end timestamps
* Records processed
* Records rejected
* Processing duration
* Source identifier
* Cursor/checkpoint
* Error information
* Retry information

The target operational model is:

```text
Dagster
   |
   +-- Run Metadata
   +-- Asset Status
   +-- Job Status
   +-- Sensor Status
   +-- Schedule Status
   |
   v
Operational Visibility
```

---

# Future Evolution

The current repository is intentionally positioned between a working backend orchestration layer and a broader production CDP platform.

The expected evolution is:

```text
CURRENT
------------------------------------------------

data_synch          Placeholder
identity_resolution Implemented
analytics           Implemented
scoring             Placeholder
segmentation        Implemented
personalization     Placeholder
campaign_activation Placeholder
email_engine        Placeholder
notification_engine Placeholder


TARGET
------------------------------------------------

Data Sources
     |
     v
data_synch
     |
     v
Raw JSONL / S3
     |
     v
Polars
     |
     +-------------------+
     |                   |
     v                   v
Identity Resolution   Analytics
     |                   |
     +---------+---------+
               |
               v
          Customer 360
               |
       +-------+-------+
       |       |       |
       v       v       v
    Scoring Segmentation Personalization
       |       |       |
       +-------+-------+
               |
               v
            campaign_activation
               |
       +-------+-------+
       |               |
       v               v
     Email         Notification
       |               |
       +-------+-------+
               |
               v
         Customer Events
               |
               v
            Analytics
```

The target architecture is therefore a **closed-loop Customer Data Platform**, rather than a collection of independent batch jobs.

---

# Summary

`backend-system/` is the **Dagster orchestration and data-processing layer** for the Customer 360 platform.

It separates the CDP into domain-oriented services:

```text
data_synch
identity_resolution
analytics
scoring
segmentation
personalization
campaign_activation
email_engine
notification_engine
```

The architecture uses:

```text
Dagster
    = orchestration

S3 / MinIO
    = raw and analytical object storage

JSONL
    = raw ingestion format

Polars
    = analytical transformation engine

Parquet
    = curated analytical format

PostgreSQL
    = Customer 360 serving layer

Redis
    = low-latency state, cache, leases and cursors
```

The central data flow is:

```text
RAW DATA
    ↓
DATA SYNC
    ↓
JSONL / S3
    ↓
POLARS
    ↓
IDENTITY + ANALYTICS
    ↓
CUSTOMER 360
    ↓
SCORING
    ↓
SEGMENTATION
    ↓
PERSONALIZATION
    ↓
CAMPAIGN
    ↓
EMAIL / NOTIFICATION
    ↓
CUSTOMER RESPONSE
    ↓
ANALYTICS
```

The current implementation already provides the foundation through **identity resolution, segmentation, and source analytics**, while the remaining domains are scaffolded for future business logic.

The long-term objective is to evolve `backend-system/` into a reliable, observable, and scalable **Customer 360 CDP processing platform** where customer data continuously flows from ingestion through identity resolution, analytical intelligence, decisioning, activation, and feedback.