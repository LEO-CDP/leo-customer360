"""Read Zalo opt-out events from the S3 event lake for the consent projection.

Lists recent gzip/plain JSONL objects in each tenant's ``data-tracking-{tenant_id}``
bucket (Zalo events partition by tenant UUID via the webhook's ``_source_id``) and
yields the ``zalo-opt-out`` / ``zalo-failed`` records the webhook wrote. Downstream
``apply_optout`` is idempotent, so this re-scans a bounded recent window
(``CRM_ZALO_OPTOUT_LOOKBACK_HOURS``) each run rather than tracking a checkpoint.

Self-contained (boto3 only; mirrors the analytics code location's S3 client/env).
``extract_optout_events`` is the pure, testable filter.
"""

import gzip
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Iterable

from .config import (
    EVENT_RAW_PREFIX,
    OPTOUT_LOOKBACK_HOURS,
    S3_ACCESS_KEY_ID,
    S3_ENDPOINT_URL,
    S3_FORCE_PATH_STYLE,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
    S3_SESSION_TOKEN,
    S3_VERIFY_SSL,
)

ZALO_CHANNEL = "zalo"
OPTOUT_EVENT_NAMES = {"zalo-opt-out", "zalo-failed"}


def build_s3_client():
    import boto3
    from botocore.client import Config

    kwargs = {
        "region_name": S3_REGION,
        "verify": S3_VERIFY_SSL,
        "config": Config(s3={"addressing_style": "path" if S3_FORCE_PATH_STYLE else "auto"}),
    }
    if S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = S3_ENDPOINT_URL
    if S3_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = S3_ACCESS_KEY_ID
    if S3_SECRET_ACCESS_KEY:
        kwargs["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY
    if S3_SESSION_TOKEN:
        kwargs["aws_session_token"] = S3_SESSION_TOKEN
    return boto3.client("s3", **kwargs)


def _iter_lines(body, object_key: str):
    data = body.read() if hasattr(body, "read") else body
    if object_key.endswith(".gz"):
        with gzip.GzipFile(fileobj=BytesIO(data)) as gz:
            yield from gz
    else:
        yield from BytesIO(data)


def extract_optout_events(records: Iterable[dict]) -> list[dict]:
    """Filter parsed JSONL records to zalo opt-out events (pure, testable).

    Keeps only channel='zalo' opt-out/failed records that carry a
    suppression_reason + tenant/profile; shapes each for ``project_optout_events``.
    """
    events = []
    for record in records:
        if not isinstance(record, dict):
            continue
        props = record.get("properties") or {}
        if props.get("tracking_channel") != ZALO_CHANNEL:
            continue
        if record.get("event_name") not in OPTOUT_EVENT_NAMES:
            continue
        if not props.get("suppression_reason"):
            continue
        if not (props.get("tenant_id") and props.get("master_profile_id")):
            continue
        events.append({"properties": props})
    return events


def _read_bucket(s3_client, bucket: str, start_after: str | None, prefix: str | None = None) -> list[dict]:
    events: list[dict] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        kwargs = {"Bucket": bucket}
        if prefix:
            kwargs["Prefix"] = prefix
        if start_after:
            kwargs["StartAfter"] = start_after
        for page in paginator.paginate(**kwargs):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                if not (key.endswith(".jsonl") or key.endswith(".jsonl.gz")):
                    continue
                obj = s3_client.get_object(Bucket=bucket, Key=key)
                records = []
                for line in _iter_lines(obj["Body"], key):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except (ValueError, TypeError):
                        continue
                events.extend(extract_optout_events(records))
    except Exception as exc:  # noqa: BLE001 - missing bucket is normal (tenant never got events)
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    return events


def read_optout_events_for_tenants(tenant_ids: Iterable[str], s3_client=None) -> list[dict]:
    """Read recent zalo opt-out events across the given tenants' buckets."""
    s3_client = s3_client or build_s3_client()
    prefix = f"{EVENT_RAW_PREFIX}/"
    window_start = (datetime.now(timezone.utc) - timedelta(hours=OPTOUT_LOOKBACK_HOURS)).strftime("%Y-%m-%d-%H")
    # Prefix-scoped listing + prefix-qualified StartAfter so we page only the recent
    # hourly partitions ("<prefix>/YYYY-MM-DD-HH/...") instead of the whole bucket.
    start_after = f"{prefix}{window_start}"
    events: list[dict] = []
    for tenant_id in tenant_ids:
        events.extend(_read_bucket(s3_client, f"data-tracking-{tenant_id}", start_after, prefix))
    return events
