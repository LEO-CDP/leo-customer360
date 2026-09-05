"""S3-compatible object storage adapter for tracking logs."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, BotoCoreError

from core.config import Settings


class ObjectStorageError(RuntimeError):
    """Raised when a tracking object cannot be written."""


@dataclass(frozen=True)
class StoredTrackingLog:
    data_source_id: UUID
    bucket: str
    object_key: str
    event_count: int
    received_at: datetime


def build_tracking_object(
    data_source_id: UUID,
    events: list[dict[str, Any]],
    received_at: datetime,
) -> tuple[str, str, bytes]:
    """Build the bucket, hourly key, and NDJSON body for one ingestion batch."""
    received_at = received_at.astimezone(timezone.utc)
    bucket = f"data-tracking-{data_source_id}"
    folder = received_at.strftime("%Y-%m-%d-%H")
    object_key = f"{folder}/{uuid4()}.jsonl"
    lines = [
        json.dumps(
            {
                "data_source_id": str(data_source_id),
                "received_at": received_at.isoformat(),
                "event": event,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for event in events
    ]
    return bucket, object_key, ("\n".join(lines) + "\n").encode("utf-8")


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
        bucket, object_key, body = build_tracking_object(data_source_id, events, received_at)
        self._ensure_bucket(bucket)
        try:
            self.client.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=body,
                ContentType="application/x-ndjson",
                Metadata={"data-source-id": str(data_source_id)},
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError("Could not write tracking logs to object storage") from exc

        return StoredTrackingLog(
            data_source_id=data_source_id,
            bucket=bucket,
            object_key=object_key,
            event_count=len(events),
            received_at=received_at,
        )

    def _ensure_bucket(self, bucket: str) -> None:
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
