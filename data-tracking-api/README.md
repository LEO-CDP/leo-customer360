# CDP Data Tracking API

A small FastAPI ingestion service for CDP tracking events. Each accepted batch
is written as newline-delimited JSON to an S3-compatible object:

```text
s3://data-tracking-[data_source_id]/yyyy-mm-dd-hh/[batch-uuid].jsonl
```

The folder uses the UTC time at which the API received the batch. Each line
contains `data_source_id`, `received_at`, and the original event under `event`.
Batches are immutable objects, which avoids concurrent append races in S3.

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
  "user_id": "user-456",
  "events": [
    {"event_name": "page_view", "page_url": "https://example.test/"}
  ]
}
```

Returns the bucket, object key, event count, receive timestamp, and the number
of session entries refreshed in Redis. Session cache keys use the form
`data-tracking-api:session:[data_source_id]:[session_id]`, expire according to
`TRACKING_SESSION_TTL_SECONDS`, and contain only `last_seen_at`, `event_count`,
and optional `user_id` metadata.

Requests whose `User-Agent` contains one of the configured bot patterns
(`googlebot`, `bingbot`, `ahrefsbot`, and similar) return `201` with
`accepted=false` and `filtered=true`; no S3 object or Redis rate-limit token is
created. Legitimate clients are limited per source IP using an atomic Redis
window and receive `429` plus `Retry-After` when the limit is exceeded.

OpenAPI is available at `/docs`; liveness is available at `/health`.
