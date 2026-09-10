# CDP Data Tracking API

A small FastAPI ingestion service for CDP tracking events. Each accepted batch
is acknowledged after it is durably enqueued in Redis Streams, then written by
a background worker as newline-delimited JSON to an S3-compatible object:

```text
s3://data-tracking-[data_source_id]/yyyy-mm-dd-hh/[batch-uuid].jsonl
```

The folder uses the UTC time at which the API received the batch. Each line
contains `data_source_id`, `received_at`, and the original event under `event`.
Batches are immutable objects, which avoids concurrent append races in S3.
Redis Streams use consumer-group acknowledgements, so an object is acknowledged
only after the S3/MinIO write succeeds. An unacknowledged message can be
claimed by another tracking-api replica after a worker failure.

## Run locally with MinIO

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
window and receive `429` plus `Retry-After` when the limit is exceeded.

OpenAPI is available at `/docs`; liveness is available at `/health`.

## Queue configuration

`TRACKING_QUEUE_BACKEND=redis_stream` is the production default. The Redis
instance configured by `REDIS_HOST`, `REDIS_PORT`, and `REDIS_PASSWORD` carries
the `TRACKING_STREAM_NAME` stream and `TRACKING_STREAM_GROUP` consumer group.
`TRACKING_STREAM_MAX_LENGTH` is a hard queue capacity: a full stream rejects
new batches instead of trimming unacknowledged entries. Successfully processed
entries are deleted after acknowledgement. `TRACKING_STREAM_CLAIM_IDLE_MS`
controls when another worker may reclaim a stalled message.
`TRACKING_STREAM_RETRY_SECONDS` controls worker retry delay after an S3 failure.

`TRACKING_QUEUE_BACKEND=memory` is available for isolated local tests only. It
is process-local and rejects new batches when full; it must not be used when
durability across restarts or multiple replicas is required.
