"""S3-backed event projections for resolved master profiles."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from leo_customer360_dao.config import Settings


class MasterProfileEventStoreError(RuntimeError):
    """Raised when a master-profile event projection cannot be read or written."""


class MasterProfileEventStore:
    """Read/write one JSON event projection per resolved master profile."""

    def __init__(self, settings: Settings, s3_client: Any | None = None):
        self.settings = settings
        self.bucket = settings.master_profile_s3_bucket
        self.s3 = s3_client or self._build_s3_client()

    def object_key(self, master_profile_id: UUID) -> str:
        return f"{master_profile_id}.json"

    def put_events(
        self,
        tenant_id: UUID,
        master_profile_id: UUID,
        events: list[dict[str, Any]],
    ) -> None:
        self._ensure_bucket()
        ordered = sorted(events, key=self._event_sort_key, reverse=True)
        body = json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self.object_key(master_profile_id),
                Body=body,
                ContentType="application/json",
                Metadata={
                    "tenant-id": str(tenant_id),
                    "master-profile-id": str(master_profile_id),
                    "event-count": str(len(ordered)),
                },
            )
        except (BotoCoreError, ClientError) as exc:
            raise MasterProfileEventStoreError(
                f"Could not write master profile event projection {master_profile_id}"
            ) from exc

    def query(
        self,
        tenant_id: UUID,
        master_profile_id: UUID,
        *,
        from_event_time: Optional[datetime] = None,
        to_event_time: Optional[datetime] = None,
        data_source_id: Optional[UUID] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        events = self._read_events(tenant_id, master_profile_id)
        lower_bound, upper_bound = self._bounds(from_event_time, to_event_time)
        filtered = []
        for event in events:
            if data_source_id is not None and str(event.get("data_source_id")) != str(
                data_source_id
            ):
                continue
            event_time = self._parse_datetime(event.get("event_time"))
            if event_time is None or not (lower_bound <= event_time <= upper_bound):
                continue
            filtered.append(event)
        filtered.sort(key=self._event_sort_key, reverse=True)
        return filtered[:limit] if limit is not None else filtered

    def _read_events(
        self,
        tenant_id: UUID,
        master_profile_id: UUID,
    ) -> list[dict[str, Any]]:
        try:
            response = self.s3.get_object(
                Bucket=self.bucket,
                Key=self.object_key(master_profile_id),
            )
            raw = response["Body"].read()
            payload = json.loads(raw.decode("utf-8"))
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if error_code in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"} or status_code == 404:
                return []
            raise MasterProfileEventStoreError(
                f"Could not read master profile event projection {master_profile_id}"
            ) from exc
        except (BotoCoreError, json.JSONDecodeError, UnicodeDecodeError, KeyError) as exc:
            raise MasterProfileEventStoreError(
                f"Master profile event projection {master_profile_id} is invalid"
            ) from exc

        if not isinstance(payload, list):
            raise MasterProfileEventStoreError(
                f"Master profile event projection {master_profile_id} must be a JSON array"
            )
        return [event for event in payload if isinstance(event, dict)]

    def _ensure_bucket(self) -> None:
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise MasterProfileEventStoreError(
                    f"Could not access master profile bucket {self.bucket}"
                ) from exc
        try:
            self.s3.create_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as exc:
            raise MasterProfileEventStoreError(
                f"Could not create master profile bucket {self.bucket}"
            ) from exc

    def _build_s3_client(self) -> Any:
        kwargs: dict[str, Any] = {
            "region_name": self.settings.event_s3_region,
            "verify": self.settings.event_s3_verify_ssl,
            "config": Config(
                s3={
                    "addressing_style": (
                        "path" if self.settings.event_s3_force_path_style else "auto"
                    )
                }
            ),
        }
        if self.settings.event_s3_endpoint_url:
            kwargs["endpoint_url"] = self.settings.event_s3_endpoint_url
        if self.settings.event_s3_access_key_id:
            kwargs["aws_access_key_id"] = self.settings.event_s3_access_key_id
        if self.settings.event_s3_secret_access_key:
            kwargs["aws_secret_access_key"] = self.settings.event_s3_secret_access_key
        if self.settings.event_s3_session_token:
            kwargs["aws_session_token"] = self.settings.event_s3_session_token
        try:
            return boto3.client("s3", **kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise MasterProfileEventStoreError("Invalid master profile S3 configuration") from exc

    @classmethod
    def _parse_datetime(cls, value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _event_sort_key(cls, event: dict[str, Any]) -> datetime:
        return cls._parse_datetime(event.get("event_time")) or datetime.min.replace(
            tzinfo=timezone.utc
        )

    @classmethod
    def _bounds(
        cls,
        from_event_time: Optional[datetime],
        to_event_time: Optional[datetime],
    ) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        default_start = now - timedelta(days=7)
        values = [cls._parse_datetime(value) for value in (from_event_time, to_event_time)]
        supplied = [value for value in values if value is not None]
        if not supplied:
            return default_start, now
        if len(supplied) == 1:
            return min(supplied[0], now), max(supplied[0], now)
        return min(supplied), max(supplied)