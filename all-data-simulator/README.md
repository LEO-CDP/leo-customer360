# All Data Simulator

Synthetic data generators and integration checks for the Customer 360 demo and
UAT environments. This folder covers three separate workflows:

| Workflow | Entry point | Destination |
| --- | --- | --- |
| Adjust batch fixture | `adjust_faker.py` | CSV and optionally MinIO/S3 |
| GA4-style sample events | `google_analytics_faker.py` | JSON printed to stdout |
| Web tracking and analytics E2E | `web_user_simulator.py` or `run_tracking_analytics_e2e.sh` | Tracking API, MinIO/S3, Dagster, PostgreSQL |

The web simulator does not publish to Kafka. It sends one ordered event batch
per synthetic user to the tracking API; the tracking service writes NDJSON to
S3-compatible storage, and the analytics job aggregates those objects.

## Prerequisites

For the batch generators and Python tests, use Python 3.11 or newer. The
repository's local services must be running for API, MinIO, or analytics
checks. The full E2E script additionally requires `curl`, `jq`, and Docker;
the script uses `mc` and `psql` from the running MinIO and PostgreSQL
containers.

Create a local configuration file from [example.env](example.env):

```bash
cd all-data-simulator
cp example.env .env
```

Keep `.env` private. The web simulator also searches the repository-root
`.env`; the batch helper expects its `.env` in this directory because it runs
the fakers from `all-data-simulator`.

## Install and Run

The helper creates or reuses `all-data-simulator/.venv`, installs
[requirements.txt](requirements.txt), and runs both batch generators:

```bash
cd all-data-simulator
./run_data_simulator.sh
```

Run a generator directly after installation with the simulator interpreter:

```bash
.venv/bin/python adjust_faker.py
.venv/bin/python google_analytics_faker.py
```

The Adjust faker uses Gemini only when `GEMINI_API_KEY` is set. Without a key,
it uses local Faker-based fallback templates and does not need network access.
Optional Gemini settings are:

```dotenv
GEMINI_API_KEY=your-api-key
GEMINI_MODEL=gemini-3.5-flash
GEMINI_TIMEOUT_MS=20000
```

## Batch Fixtures

### Adjust

[adjust_faker.py](adjust_faker.py) generates Bank123 acquisition and in-app
journeys, including installs, logins, feature usage, and retargeting
re-engagement. It writes `bank123_adjust_in_app_events.csv` and attempts to
upload that file to the `ADJUST_S3_BUCKET` bucket through
[s3_data_util.py](s3_data_util.py). Upload failure is logged and does not fail
CSV generation.

The generated columns are documented in
[data-dictionary/adjust-dictionary.csv](data-dictionary/adjust-dictionary.csv).
Campaign, ad-set, and ad identifiers remain stable across generated rows.
The fixture includes organic and paid traffic from Google Ads, Facebook Ads,
TikTok, and Apple Search Ads.

Relevant settings:

```dotenv
MINIO_ROOT_USER=change_me_minio_root_user
MINIO_ROOT_PASSWORD=change_me_minio_root_password
MINIO_API_HOST_PORT=9000
MINIO_HOST_BIND=127.0.0.1
ADJUST_S3_BUCKET=adjust-data
```

### GA4-style sample events

[google_analytics_faker.py](google_analytics_faker.py) is a small standalone
faker. It prints five JSON events to stdout and does not currently write a
file or upload to `GA4_S3_BUCKET`. Its schema is a mobile-event approximation,
not an official GA4 BigQuery export.

## Web Tracking Simulator

[web_user_simulator.py](web_user_simulator.py) simulates an ecommerce journey:
ad impression, product view, price or support request, and purchase. It can
run with a local deterministic journey or ask an OpenAI-compatible model to
choose the actions. Each user produces one ordered batch for the tracking API.

First run a local dry run without external services:

```bash
cd all-data-simulator
.venv/bin/python web_user_simulator.py --offline --dry-run --users 2 --seed 7 --verbose
```

To send events to the local tracking API and verify the written MinIO object:

```bash
export TRACKING_API_URL=http://localhost:8010/api/v1/tracking/logs
export TRACKING_DATA_SOURCE_ID=15dc39d4-ae42-5c60-9c77-66f05dcae448
.venv/bin/python web_user_simulator.py --offline --users 10 --verbose
```

The data-source ID above is the deterministic `C360 Tracker` row seeded by
the full demo-data script. Override it only with an ID registered for the
tenant. The simulator reads these settings from `.env` as well as the process
environment:

| Variable | Purpose | Default |
| --- | --- | --- |
| `TRACKING_API_URL` | Tracking ingestion endpoint | `https://c360.example.com/data/api/v1/tracking/logs` |
| `TRACKING_DATA_SOURCE_ID` | Registered source UUID | Module fallback: `11111111-1111-1111-1111-111111111111`; use the seeded ID locally |
| `MINIO_ENDPOINT` | S3-compatible endpoint without scheme | Derived from `MINIO_HOST_BIND` and `MINIO_API_HOST_PORT` |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO read-back credentials | None |
| `TRACKING_S3_VERIFY_WAIT_SECONDS` | Initial storage flush wait | `5` |
| `TRACKING_S3_VERIFY_RETRY_SECONDS` | Delay between read-back attempts | `2` |
| `TRACKING_S3_VERIFY_ATTEMPTS` | Number of read-back attempts | `3` |
| `TRACKING_S3_VERIFY_ENABLED` | Enable MinIO verification | `true` |

Use `--no-s3-verify` for a remote S3 setup or when read-back is not available.
Use `--s3-wait-seconds 10` when storage visibility is slower. If no
`LEO_OPENAI_API_KEY` or `OPENAI_API_KEY` is configured, the simulator falls
back to the deterministic offline journey. For model-directed journeys,
configure:

```dotenv
LEO_OPENAI_API_KEY=your-api-key
LEO_OPENAI_MODEL_NAME=gpt-5.6-luna
LEO_OPENAI_BASE_URL=https://your-openai-compatible-gateway.example/v1
OPENAI_REASONING_EFFORT=none
```

The `OPENAI_REASONING_EFFORT=none` setting is required by the documented
`gpt-5.6-luna` tool-calling setup. Use `--model` to override the model.

## Tracking and Analytics E2E

[run_tracking_analytics_e2e.sh](run_tracking_analytics_e2e.sh) is a local
integration check for one deterministic event batch. It verifies the tracking
API response, exact MinIO NDJSON contents, successful `analytics_hourly_schedule`
completion, the Customer 360 data-source summary, and the matching PostgreSQL
row.

Start the local services first, then run from this directory:

```bash
cd all-data-simulator
./run_tracking_analytics_e2e.sh
```

The script loads `ENV_FILE` (default: repository-root `.env`) and defaults to
the local API, MinIO container `customer360-minio`, PostgreSQL container
`customer360-postgres`, demo tenant `11111111-1111-1111-1111-111111111111`,
and seeded C360 Tracker source
`15dc39d4-ae42-5c60-9c77-66f05dcae448`. Override the containers or source when
using a different Compose project:

```bash
ENV_FILE=/path/to/.env \
MINIO_CONTAINER=my-minio \
POSTGRES_CONTAINER=my-postgres \
TRACKING_DATA_SOURCE_ID=15dc39d4-ae42-5c60-9c77-66f05dcae448 \
./run_tracking_analytics_e2e.sh
```

Analytics endpoints require either `CUSTOMER360_API_TOKEN` (or `LEO_API_TOKEN`)
or `CUSTOMER360_USERNAME` and `CUSTOMER360_PASSWORD`. Local development can
reuse `DEFAULT_ROOT_USERNAME` and `DEFAULT_ROOT_PASSWORD`. The main API base
URL is configured with `CUSTOMER360_API_URL` and defaults to
`http://localhost:8008/api/v1` in the shell template.

Useful scenario overrides are `E2E_S3_WAIT_SECONDS`,
`E2E_ANALYTICS_POLL_SECONDS`, `E2E_ANALYTICS_TIMEOUT_SECONDS`,
`E2E_SESSION_ID`, `E2E_USER_ID`, and `E2E_ORDER_ID`. If analytics returns
`409` because another run is active, the script reuses the active run ID and
waits for its result.

## Tests and Diagnostics

The test module uses `pytest`, which is intentionally not part of the runtime
requirements. Install it into the local simulator environment when running
tests:

```bash
cd all-data-simulator
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q test_web_user_simulator.py
```

Use `--help` to inspect the simulator's command-line options:

```bash
.venv/bin/python web_user_simulator.py --help
```
