# All Data Simulator for Customer 360 demo and UAT testing

Generates realistic synthetic mobile-app raw event data for the Bank123 demo
app, used to seed/UAT-test the Customer 360 pipeline's batch and streaming
ingestion paths.

## Setup & Usage

```bash
./run_data_simulator.sh
```

This creates/reuses a local virtual environment in `.venv`, installs
[requirements.txt](requirements.txt), then runs `appsflyer_faker.py` and
`google_analytics_faker.py` in sequence -- all scoped to this folder.

### Optional: Gemini-powered generation

Both fakers can use Google Gemini to generate more varied campaigns/personas
instead of the built-in offline fallback templates. Configure via a local
`.env` file (not committed):

```
GEMINI_API_KEY=your-api-key
GEMINI_MODEL=gemini-3.5-flash      # optional, defaults to gemini-3.5-flash
GEMINI_TIMEOUT_MS=20000            # optional, per-request timeout (ms)
```

Without a `GEMINI_API_KEY`, the fakers run fully offline using local
Faker-driven fallback data -- safe for CI/sandboxed environments with no
network egress.

### Optional: upload output to MinIO/S3

[s3_data_util.py](s3_data_util.py) provides a reusable `S3DataUtil` class that
copies a local file into an S3-compatible bucket (creating the bucket if it
doesn't exist yet). `appsflyer_faker.py` uses it automatically after writing
its CSV, uploading to the bucket named by `APPSFLYER_S3_BUCKET`. Configure via
`.env` (defaults match the project's dev MinIO stack, see `.env.example` /
`dev-docker-compose.yml`):

```
MINIO_ROOT_USER=change_me_minio_root_user
MINIO_ROOT_PASSWORD=change_me_minio_root_password
MINIO_API_HOST_PORT=9000       # optional, defaults to 9000
MINIO_HOST_BIND=127.0.0.1      # optional, normalized to localhost
APPSFLYER_S3_BUCKET=appsflyer-data
GA4_S3_BUCKET=ga4-data         # reserved for the GA4 faker, not wired up yet
```

If MinIO is unreachable or credentials are missing, the upload is skipped
with a warning -- it never fails the local CSV generation.

## Batch Data Source: Appsflyer to MinIO S3 bucket: appsflyer-data

[appsflyer_faker.py](appsflyer_faker.py) simulates Bank123 user acquisition
and in-app-event journeys (install, login, feature usage, retargeting
re-engagement) as raw AppsFlyer Pull API report rows. Field names/semantics
are grounded in [appsflyer-raw-data-field-dictionary.csv](appsflyer-raw-data-field-dictionary.csv).

- Run directly: `python appsflyer_faker.py`
- Output: `bank123_appsflyer_in_app_events.csv` (schema matches a real
  AppsFlyer Pull API raw-data export: attribution, cost, device, and
  in-app-event columns)
- Simulates both organic and paid (Google Ads, Facebook Ads, TikTok, Apple
  Search Ads) installs, plus retargeting re-engagement journeys
- Campaign/adset/ad IDs are stable per entity across all users/events, as in
  a real report
- After writing the CSV, uploads it to the `APPSFLYER_S3_BUCKET` MinIO bucket
  via `S3DataUtil` (see "Optional: upload output to MinIO/S3" above)

## Batch Data Source: Google Analytics to MinIO S3 bucket: ga4-data

[google_analytics_faker.py](google_analytics_faker.py) is a minimal stub
generating sample GA4-style mobile events. Not yet fleshed out to match a
real GA4 export schema.

TODO: align event/field schema with an official GA4 BigQuery export
field dictionary (see `appsflyer-raw-data-field-dictionary.csv` for the
pattern used by the AppsFlyer faker), write output to a batch file instead of
stdout, and upload it via `S3DataUtil` to the `GA4_S3_BUCKET` bucket.

## Streaming Data Source: Website User to Apache Kafka topics: web-trueview-data, web-action-data

TODO
