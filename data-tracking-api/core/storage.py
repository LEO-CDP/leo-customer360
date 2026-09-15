"""S3-compatible object storage adapter for tracking logs."""

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, BotoCoreError

from core.config import Settings
from core.metrics import tracking_metrics


class ObjectStorageError(RuntimeError):
    """Raised when a tracking object cannot be written."""


@dataclass(frozen=True)
class StoredTrackingLog:
    data_source_id: UUID
    bucket: str
    object_key: str
    event_count: int
    received_at: datetime
    queue_message_id: str | None = None
    object_id: UUID | None = None
    content_sha256: str | None = None
    schema_version: int = 1


def build_tracking_object(
    data_source_id: UUID,
    events: list[dict[str, Any]],
    received_at: datetime,
    *,
    schema_version: int = 1,
    ingestion_version: str = "1.0",
) -> tuple[str, str, bytes]:
    """Build a deterministic, compressed Bronze object for one ingestion batch."""
    received_at = received_at.astimezone(timezone.utc)
    bucket = f"data-tracking-{data_source_id}"
    folder = received_at.strftime("%Y-%m-%d-%H")
    event_ids = [_event_id(event, data_source_id, index) for index, event in enumerate(events)]
    batch_id = uuid5(
        NAMESPACE_URL,
        f"c360:batch:{data_source_id}:{'|'.join(event_ids)}",
    )
    object_key = f"events/{folder}/{batch_id}.jsonl.gz"
    lines = [
        json.dumps(
            {
                "schema_version": schema_version,
                "ingestion_version": ingestion_version,
                "event_id": _event_id(event, data_source_id, index),
                "data_source_id": str(data_source_id),
                "event_time": _event_time(event, received_at),
                "received_at": received_at.isoformat(),
                "event_name": event.get("event_name"),
                "event_category": event.get("event_category", "GENERAL"),
                "event_dedup_key": _event_dedup_key(event),
                "identity": _identity(event),
                "master_profile_id": event.get("master_profile_id"),
                "payload": event,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for index, event in enumerate(events)
    ]
    raw_body = ("\n".join(lines) + "\n").encode("utf-8")
    return bucket, object_key, gzip.compress(raw_body, mtime=0)


class S3ObjectStorage:
    """Writes tracking batches to AWS S3 or a MinIO S3-compatible endpoint."""

    def __init__(self, settings: Settings):
        client_config = Config(
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"}
        )
        client_kwargs: dict[str, Any] = {
            "region_name": settings.s3_region,
            "config": client_config,
        }
        if settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_access_key_id:
            client_kwargs["aws_access_key_id"] = settings.s3_access_key_id
        if settings.s3_secret_access_key:
            client_kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
        if settings.s3_session_token:
            client_kwargs["aws_session_token"] = settings.s3_session_token

        self.client = boto3.client("s3", **client_kwargs)
        self.auto_create_buckets = settings.s3_auto_create_buckets
        self.schema_version = settings.event_schema_version
        self.ingestion_version = settings.event_ingestion_version
        self.max_object_size_bytes = max(1, int(settings.event_max_object_size_bytes))
        self.processed_prefix = settings.tracking_processed_prefix.strip("/") or "_processed"
        self._known_buckets: set[str] = set()
        self._known_buckets_lock = Lock()

    def check_connection(self) -> None:
        """Probe object-storage reachability for /health.

        Issues a cheap ListBuckets against the configured endpoint, which also
        validates credentials. Raises ObjectStorageError if the store cannot be
        reached so the health endpoint can report it (S3 is the critical sink).
        """
        try:
            self.client.list_buckets()
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError("Object storage is not reachable") from exc

    def store_tracking_logs(
        self,
        data_source_id: UUID,
        events: list[dict[str, Any]],
        received_at: datetime,
    ) -> StoredTrackingLog:
        bucket, object_key, body = build_tracking_object(
            data_source_id,
            events,
            received_at,
            schema_version=self.schema_version,
            ingestion_version=self.ingestion_version,
        )
        return self.store_prebuilt_tracking_object(
            data_source_id=data_source_id,
            bucket=bucket,
            object_key=object_key,
            body=body,
            event_count=len(events),
            received_at=received_at,
        )

    def store_prebuilt_tracking_object(
        self,
        data_source_id: UUID,
        bucket: str,
        object_key: str,
        body: bytes,
        event_count: int,
        received_at: datetime,
    ) -> StoredTrackingLog:
        """Write a prebuilt NDJSON object to S3-compatible storage."""
        if len(body) > self.max_object_size_bytes:
            raise ObjectStorageError("Tracking object exceeds the configured size limit")
        self._ensure_bucket(bucket)
        content_sha256 = hashlib.sha256(body).hexdigest()
        object_id = uuid5(NAMESPACE_URL, f"s3://{bucket}/{object_key}")
        processed_key = f"{self.processed_prefix}/{object_id}.json"
        if self._processed_marker_exists(bucket, processed_key):
            tracking_metrics.increment("tracking_processed_marker_hits_total")
            return StoredTrackingLog(
                data_source_id=data_source_id,
                bucket=bucket,
                object_key=object_key,
                event_count=event_count,
                received_at=received_at,
                object_id=object_id,
                content_sha256=content_sha256,
                schema_version=self.schema_version,
            )
        try:
            self.client.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=body,
                ContentType="application/x-ndjson",
                ContentEncoding="gzip",
                Metadata={
                    "data-source-id": str(data_source_id),
                    "event-count": str(event_count),
                    "schema-version": str(self.schema_version),
                    "ingestion-version": self.ingestion_version,
                    "sha256": content_sha256,
                },
            )
        except (BotoCoreError, ClientError) as exc:
            tracking_metrics.increment("tracking_s3_upload_failures_total")
            raise ObjectStorageError("Could not write tracking logs to object storage") from exc

        tracking_metrics.increment("tracking_s3_uploads_total")

        self._write_processed_marker(
            bucket=bucket,
            processed_key=processed_key,
            object_key=object_key,
            object_id=object_id,
            data_source_id=data_source_id,
            event_count=event_count,
            content_sha256=content_sha256,
        )
        stored = StoredTrackingLog(
            data_source_id=data_source_id,
            bucket=bucket,
            object_key=object_key,
            event_count=event_count,
            received_at=received_at,
            object_id=object_id,
            content_sha256=content_sha256,
            schema_version=self.schema_version,
        )
        return stored

    def _processed_marker_exists(self, bucket: str, processed_key: str) -> bool:
        try:
            self.client.head_object(Bucket=bucket, Key=processed_key)
            return True
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise ObjectStorageError("Could not read processed tracking state") from exc
        except BotoCoreError as exc:
            raise ObjectStorageError("Could not read processed tracking state") from exc

    def _write_processed_marker(
        self,
        *,
        bucket: str,
        processed_key: str,
        object_key: str,
        object_id: UUID,
        data_source_id: UUID,
        event_count: int,
        content_sha256: str,
    ) -> None:
        marker = json.dumps(
            {
                "object_id": str(object_id),
                "bucket": bucket,
                "object_key": object_key,
                "data_source_id": str(data_source_id),
                "event_count": event_count,
                "content_sha256": content_sha256,
                "status": "stored",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            self.client.put_object(
                Bucket=bucket,
                Key=processed_key,
                Body=marker,
                ContentType="application/json",
                Metadata={
                    "object-id": str(object_id),
                    "object-key": object_key,
                    "content-sha256": content_sha256,
                    "event-count": str(event_count),
                },
            )
        except (BotoCoreError, ClientError) as exc:
            tracking_metrics.increment("tracking_processed_marker_failures_total")
            raise ObjectStorageError(
                "Tracking object was uploaded but its processed state was not recorded"
            ) from exc
        tracking_metrics.increment("tracking_processed_marker_writes_total")

    def _ensure_bucket(self, bucket: str) -> None:
        if bucket in self._known_buckets:
            return

        with self._known_buckets_lock:
            if bucket in self._known_buckets:
                return

            self._ensure_bucket_remote(bucket)
            self._known_buckets.add(bucket)

    def _ensure_bucket_remote(self, bucket: str) -> None:
        try:
            self.client.head_bucket(Bucket=bucket)
            return
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise ObjectStorageError("Could not access the tracking bucket") from exc
            if not self.auto_create_buckets:
                raise ObjectStorageError("Tracking bucket does not exist") from exc
        except BotoCoreError as exc:
            raise ObjectStorageError("Could not access the tracking bucket") from exc

        create_kwargs: dict[str, Any] = {"Bucket": bucket}
        region = self.client.meta.region_name or "us-east-1"
        if region != "us-east-1":
            create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        try:
            self.client.create_bucket(**create_kwargs)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise ObjectStorageError("Could not create the tracking bucket") from exc
        except BotoCoreError as exc:
            raise ObjectStorageError("Could not create the tracking bucket") from exc


def build_storage(settings: Settings) -> S3ObjectStorage:
    """Create the configured storage adapter."""
    if settings.object_storage_mode == "minio" and not settings.s3_endpoint_url:
        raise RuntimeError("S3_ENDPOINT_URL is required when OBJECT_STORAGE_MODE=minio")
    return S3ObjectStorage(settings)


def _event_dedup_key(event: dict[str, Any]) -> str | None:
    direct_value = event.get("event_dedup_key")
    if isinstance(direct_value, str) and direct_value.strip():
        return direct_value.strip()
    properties = event.get("properties")
    if isinstance(properties, dict):
        value = properties.get("event_dedup_key")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _event_id(event: dict[str, Any], data_source_id: UUID, index: int) -> str:
    value = _string_value(event.get("event_id"))
    if value:
        return value
    payload = json.dumps(
        {"index": index, "event": event},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return str(
        uuid5(
            NAMESPACE_URL,
            f"c360:event:{data_source_id}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}",
        )
    )


def _identity(event: dict[str, Any]) -> dict[str, str | None]:
    return {
        "user_id": _string_value(event.get("user_id")),
        "session_id": _string_value(event.get("session_id")),
        "device_id": _string_value(event.get("device_id")),
        "anonymous_id": _string_value(event.get("anonymous_id")),
    }


def _event_time(event: dict[str, Any], received_at: datetime) -> str:
    value = event.get("event_time") or event.get("timestamp")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return received_at.isoformat()


def _string_value(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
