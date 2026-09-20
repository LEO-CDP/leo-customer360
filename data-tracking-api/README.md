# CDP Data Tracking API

A small FastAPI ingestion service for CDP tracking events. Each accepted batch
is acknowledged after it is durably enqueued in Redis Streams, then written by
a background worker as gzip-compressed newline-delimited JSON to an
S3-compatible Bronze object:

```text
s3://data-tracking-[data_source_id]/yyyy-mm-dd-hh/[deterministic-batch-uuid].jsonl.gz
```

The folder uses the UTC time at which the API received the batch. Each line is
a versioned envelope containing `schema_version`, `ingestion_version`,
`event_id`, `event_time`, `received_at`, `event_dedup_key`, identity fields,
`device_type` (`mobile`, `tablet`, `desktop`, or `unknown`), and the original
event under `payload`. `device_type` is derived from the HTTP `User-Agent` with
`ua_parser`; the raw User-Agent is not persisted. `event_id` is preserved when supplied,
otherwise it is derived deterministically from the source event or a canonical
fallback hash. The batch key is derived from the ordered event IDs, so a retry
of the same HTTP request reuses the same logical object.

Batches are immutable objects, which avoids concurrent append races in S3.
Uploads include event count, schema version, ingestion version, and SHA-256
metadata. Redis messages carry compressed bodies as base64 and remain pending
until the raw S3 write and `_processed/<object-id>.json` state marker succeed.
The state marker is the durable idempotency authority; Redis idempotency keys
are a bounded duplicate-publication optimization and may expire safely.
Redis Streams use consumer-group acknowledgements, so an object is acknowledged
only after the S3/MinIO write succeeds. An unacknowledged message can be
claimed by another tracking-api replica after a worker failure.

## Run locally with MinIO

The service targets Python 3.12. Its Docker image uses `python:3.12-slim`, and
local development should use Python 3.12 or newer within the dependency ranges
declared in `requirements.txt`.

The root `dev-docker-compose.yml` starts `customer360-minio` and this service
on port `8010` when the service is enabled in the compose file:

```bash
docker compose -f dev-docker-compose.yml up -d --build tracking-api
```

MinIO settings are injected by Compose. For a host-run process, set
`OBJECT_STORAGE_MODE=minio`, `S3_ENDPOINT_URL=http://localhost:9000`,
`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `REDIS_HOST=localhost`, and
`REDIS_PORT=6580`.

## Production configuration

Set `OBJECT_STORAGE_MODE=s3` and provide `S3_REGION`. The service uses the
standard boto3 credential chain when `S3_ACCESS_KEY_ID` and
`S3_SECRET_ACCESS_KEY` are omitted, which supports IAM roles. Set
`S3_AUTO_CREATE_BUCKETS=false` when buckets are provisioned by infrastructure.

## API

`POST /api/v1/tracking/logs`

```json
{
  "data_source_id": "11111111-1111-1111-1111-111111111111",
  "session_id": "session-123",
  "anonymous_id": "anon-123",
  "device_id": "device-456",
  "device_fingerprint": "fingerprint-789",
  "user_id": "user-456",
  "metadata": {"source": "web", "campaign": {"name": "spring"}},
  "events": [
    {
      "event_name": "page_view",
      "page_url": "https://example.test/",
      "properties": {"experiment": {"variant": 2}}
    }
  ]
}
```

Returns `202 Accepted` with the bucket, object key, queue message ID, event
count, receive timestamp, and the number of session entries refreshed in Redis.
At least one supported identity must exist at the batch or event level:
`session_id`, `anonymous_id`, `device_id`, `device_fingerprint`, or `user_id`.
Identity values are trimmed, bounded, and rejected when blank or containing
control characters. They are copied into every event that does not already
define that identity. Event properties remain dynamic JSON, including nested
objects, arrays, and metadata.

The bundled web SDK emits the same canonical identity fields as the API:
`anonymous_id` and `device_fingerprint`. These fields are stored directly in
the event for downstream identity resolution. An absent browser fingerprint is
treated as an absent optional value.

Session cache keys use the form
`data-tracking-api:session:[data_source_id]:[session_id]`, expire according to
`TRACKING_SESSION_TTL_SECONDS`, and contain only `last_seen_at`, `event_count`,
and optional `user_id` metadata.

Requests whose `User-Agent` contains one of the configured bot patterns
(`googlebot`, `bingbot`, `ahrefsbot`, and similar) return `202` with
`accepted=false` and `filtered=true`; no S3 object or Redis rate-limit token is
created. Legitimate clients are limited per source IP using an atomic Redis
window and receive `429` plus `Retry-After` when the limit is exceeded. An
explicit comma-separated `TRACKING_RATE_LIMIT_WHITELIST` of IP addresses or
CIDRs may exempt trusted local/dev clients from consuming rate-limit tokens;
the direct client IP is used and forwarded headers are not trusted.

OpenAPI is available at `/docs`; liveness is available at `/health`.
Operational queue status is available at `/api/v1/tracking/queue-status` and
reports only the bounded queue depth and oldest pending age.
Prometheus-compatible operational metrics are available at `/metrics` (and
through the deployed `/data/metrics` path). Metrics include queue depth,
oldest pending age, queue capacity, request/event/object limits, accepted and
rejected ingestion totals, and S3 upload failures. Configure the wire-level
request cap with `TRACKING_MAX_REQUEST_BODY_BYTES`.

## Canonical envelope and MinIO state configuration

The first cutover keeps the existing per-source bucket layout so current
analytics consumers can migrate without a bucket-wide rewrite. The durable
processed-state markers are written into the same source bucket. Configure the
state prefix and Redis idempotency retention as needed:

```text
TRACKING_PROCESSED_PREFIX=_processed
TRACKING_IDEMPOTENCY_TTL_SECONDS=172800
EVENT_MAX_OBJECT_SIZE_BYTES=16777216
```

The public tracking service has no PostgreSQL driver, credentials, connection,
or write path. Environment-level `tenant_id=.../source_id=...` keys and Silver
compaction remain subsequent rollout phases.

## Email tracking

The data-tracking service also owns the public email callbacks:


The reverse proxy exposes the same routes under `/data`, so production email
links should use `EMAIL_PUBLIC_BASE_URL=https://<host>/data/api/v1`. Valid
email tokens are converted into the same `TrackingLogRequest` flow as web SDK
events. Each callback is written as an immutable NDJSON event in the tenant's
S3 tracking partition, where Dagster can process campaign, recipient, and
suppression metadata asynchronously. The data-tracking service no longer
writes email engagement or suppression rows through customer360-api.

### Email click example

The email engine signs both the tracking token (`u`) and the destination URL
(`k`). The public link uses the same `/data/api/v1` prefix as the tracking-log
endpoint:

```text
GET https://c360.example.com/data/api/v1/track/email/click
    ?u=<signed-tenant-campaign-profile-token>
    &url=https%3A%2F%2Fexample.test%2Foffer
    &k=<hmac-of-the-exact-url>
```

Only signed `http` and `https` destinations are followed. Invalid or missing
signatures redirect to `/`, while a valid click is persisted to S3 as the
following canonical event shape:

```json
{
  "data_source_id": "11111111-1111-1111-1111-111111111111",
  "user_id": "master-profile-456",
  "metadata": {"source": "email", "campaign_id": "campaign-123"},
  "events": [
    {
      "event_name": "email-clicked",
      "page_url": "https://example.test/offer",
      "properties": {
        "tracking_channel": "email",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "campaign_id": "campaign-123",
        "master_profile_id": "master-profile-456",
        "event_dedup_key": "campaign-123:master-profile-456:email-clicked",
        "url": "https://example.test/offer"
      }
    }
  ]
}
```

### Email webhook example

Provider callbacks must include the HMAC signature in `X-Webhook-Signature`.
The provider body remains small and provider-specific; the data-tracking API
normalizes it into the same request envelope before S3 storage:

```http
POST /data/api/v1/track/email/webhook?provider=ses
Content-Type: application/json
X-Webhook-Signature: <hmac-sha256-of-raw-body>
```

```json
{
  "token": "<signed-tenant-campaign-profile-token>",
  "event": "bounce",
  "email": "recipient@example.test",
  "bounce_type": "hard",
  "message_id": "provider-message-123"
}
```

The normalized event has `event_name: "email-bounced"` and stores
`suppression_reason: "hard_bounce"` under `events[0].properties`. Dagster can
then process suppression and campaign analytics from the immutable S3 object.

## Queue configuration

`TRACKING_QUEUE_BACKEND=redis_stream` is the production default. The Redis
instance configured by `REDIS_HOST`, `REDIS_PORT`, and `REDIS_PASSWORD` carries
the `TRACKING_STREAM_NAME` stream and `TRACKING_STREAM_GROUP` consumer group.
`TRACKING_STREAM_MAX_LENGTH` is a hard queue capacity: a full stream rejects
new batches instead of trimming unacknowledged entries. Successfully processed
entries are deleted after acknowledgement. `TRACKING_STREAM_CLAIM_IDLE_MS`
controls when another worker may reclaim a stalled message.
`TRACKING_STREAM_RETRY_SECONDS` controls worker retry delay after an S3 failure.
The stream worker uses a dedicated Redis client whose socket timeout is longer
than `TRACKING_STREAM_BLOCK_MS`; the request-side rate-limit/session client
keeps its short timeout. This prevents an idle `XREADGROUP BLOCK` from being
treated as a Redis outage.

`TRACKING_QUEUE_BACKEND=memory` is available for isolated local tests only. It
is process-local and rejects new batches when full; it must not be used when
durability across restarts or multiple replicas is required.
