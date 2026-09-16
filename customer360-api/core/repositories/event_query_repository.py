"""Tenant-scoped Polars queries over canonical S3/MinIO event objects."""

import gzip
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from uuid import UUID

import boto3
import polars as pl
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.config import Settings
from core.models.system import SysDataSource


class EventQueryError(RuntimeError):
    """Raised when the event lake cannot be queried."""


class EventDataSourceError(EventQueryError):
    """Raised when a requested data source is not valid for the tenant."""


class EventQueryRepository:
    """Read immutable tracking objects without using PostgreSQL event storage."""

    def __init__(self, settings: Settings, s3_client: Any | None = None):
        self.settings = settings
        self.s3 = s3_client or self._build_s3_client()

    def query(
        self,
        db: Session,
        tenant_id: UUID,
        *,
        event_time_from: Optional[datetime] = None,
        days: int,
        limit: int | None,
        master_profile_id: Optional[UUID] = None,
        domain: Optional[str] = None,
        channel: Optional[str] = None,
        event_category: Optional[str] = None,
        event_name: Optional[str] = None,
        data_source_id: Optional[UUID] = None,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        bounded_days = min(max(1, int(days)), max(1, int(self.settings.event_query_max_days)))
        lower_bound = now - timedelta(days=bounded_days)
        if event_time_from is not None:
            lower_bound = max(lower_bound, _as_utc(event_time_from))
        upper_bound = now

        source_ids = self._tenant_source_ids(db, tenant_id, data_source_id)
        rows: list[dict[str, Any]] = []
        for source_id in source_ids:
            for object_key in self._list_object_keys(source_id, tenant_id, lower_bound, upper_bound):
                rows.extend(self._read_object(source_id, tenant_id, object_key))

        if not rows:
            return []

        try:
            frame = pl.from_dicts(rows, infer_schema_length=None)
            frame = frame.with_columns(
                [
                    pl.col("event_time").str.to_datetime(time_zone="UTC", strict=False),
                    pl.col("created_at").str.to_datetime(time_zone="UTC", strict=False),
                ]
            ).filter(
                pl.col("event_time").is_not_null()
                & (pl.col("event_time") >= lower_bound)
                & (pl.col("event_time") <= upper_bound)
            )

            filters = {
                "master_profile_id": str(master_profile_id) if master_profile_id else None,
                "domain": domain,
                "channel": channel,
                "event_category": event_category,
                "event_name": event_name,
            }
            for column, value in filters.items():
                if value is not None:
                    frame = frame.filter(pl.col(column) == value)

            result = frame.sort(["event_time", "event_id"], descending=[True, True])
            if limit is not None:
                result = result.head(limit)
            return result.to_dicts()
        except pl.exceptions.PolarsError as exc:
            raise EventQueryError("Could not process event records") from exc

    def _build_s3_client(self) -> Any:
        client_kwargs: dict[str, Any] = {
            # Must be a valid AWS region token; boto3 rejects any other value.
            "region_name": self.settings.event_s3_region,
            "verify": self.settings.event_s3_verify_ssl,
            "config": Config(
                s3={
                    "addressing_style": (
                        "path" if self.settings.event_s3_force_path_style else "auto"
                    ),
                },
            ),
        }
        if self.settings.event_s3_endpoint_url:
            client_kwargs["endpoint_url"] = self.settings.event_s3_endpoint_url
        if self.settings.event_s3_access_key_id:
            client_kwargs["aws_access_key_id"] = self.settings.event_s3_access_key_id
        if self.settings.event_s3_secret_access_key:
            client_kwargs["aws_secret_access_key"] = self.settings.event_s3_secret_access_key
        if self.settings.event_s3_session_token:
            client_kwargs["aws_session_token"] = self.settings.event_s3_session_token
        try:
            return boto3.client("s3", **client_kwargs)
        except (BotoCoreError, ClientError):
            # Raise a clean error; the botocore message echoes region/keys and would
            # leak them into the 500 traceback, so drop the cause chain (from None).
            raise EventQueryError("Invalid S3 client configuration") from None

    @staticmethod
    def _tenant_source_ids(
        db: Session,
        tenant_id: UUID,
        requested_source_id: Optional[UUID] = None,
    ) -> list[UUID]:
        statement = select(SysDataSource.data_source_id).where(
            SysDataSource.tenant_id == tenant_id,
            SysDataSource.status == 1,
        )
        if requested_source_id is not None:
            statement = statement.where(SysDataSource.data_source_id == requested_source_id)
        try:
            rows = db.execute(statement).all()
        except SQLAlchemyError as exc:
            raise EventQueryError("Could not validate event data sources") from exc

        if requested_source_id is not None and not rows:
            raise EventDataSourceError(
                "Data source is invalid, inactive, or not owned by the tenant"
            )

        source_ids: list[UUID] = []
        for row in rows:
            try:
                source_id = row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
            except (AttributeError, TypeError, ValueError) as exc:
                raise EventQueryError("Invalid data source ID stored in PostgreSQL") from exc
            if source_id not in source_ids:
                source_ids.append(source_id)
        return source_ids

    def _list_object_keys(
        self,
        source_id: UUID,
        tenant_id: UUID,
        lower_bound: datetime,
        upper_bound: datetime,
    ) -> Iterable[str]:
        bucket = self.settings.event_s3_bucket or f"data-tracking-{source_id}"
        current_date = lower_bound.date()
        final_date = upper_bound.date()
        while current_date <= final_date:
            date_text = current_date.isoformat()
            if self.settings.event_s3_bucket:
                prefix = (
                    f"{self.settings.event_s3_prefix.rstrip('/')}/"
                    f"tenant_id={tenant_id}/source_id={source_id}/event_date={date_text}/"
                )
            else:
                prefix = f"{self.settings.event_s3_prefix.rstrip('/')}/{date_text}-"
            try:
                paginator = self.s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    for item in page.get("Contents", []):
                        key = str(item.get("Key", ""))
                        if key.endswith(".jsonl") or key.endswith(".jsonl.gz"):
                            yield key
            except ClientError as exc:
                error_code = str(exc.response.get("Error", {}).get("Code", ""))
                status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if error_code in {"404", "NoSuchBucket", "NotFound"} or status_code == 404:
                    return
                raise EventQueryError(
                    f"Could not list event objects for source {source_id}"
                ) from exc
            except BotoCoreError as exc:
                raise EventQueryError(
                    f"Could not list event objects for source {source_id}"
                ) from exc
            current_date += timedelta(days=1)

    def _read_object(
        self,
        source_id: UUID,
        tenant_id: UUID,
        object_key: str,
    ) -> list[dict[str, Any]]:
        bucket = self.settings.event_s3_bucket or f"data-tracking-{source_id}"
        try:
            response = self.s3.get_object(Bucket=bucket, Key=object_key)
            body = response["Body"].read()
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if error_code in {"404", "NoSuchBucket", "NotFound"} or status_code == 404:
                return []
            raise EventQueryError(f"Could not read event object {object_key}") from exc
        except BotoCoreError as exc:
            raise EventQueryError(f"Could not read event object {object_key}") from exc

        if object_key.endswith(".gz"):
            try:
                body = gzip.decompress(body)
            except (EOFError, OSError) as exc:
                raise EventQueryError(f"Malformed compressed event object {object_key}") from exc
        rows: list[dict[str, Any]] = []
        for line in body.splitlines():
            if not line.strip():
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventQueryError(f"Malformed event object {object_key}") from exc
            if not isinstance(envelope, dict):
                continue
            row = _flatten_envelope(envelope, tenant_id)
            if row is not None:
                rows.append(row)
        return rows


def _flatten_envelope(envelope: dict[str, Any], tenant_id: UUID) -> dict[str, Any] | None:
    payload = envelope.get("payload") or envelope.get("event")
    if not isinstance(payload, dict):
        return None
    event_id = envelope.get("event_id") or payload.get("event_id")
    event_time = envelope.get("event_time") or payload.get("event_time")
    if not event_id or not event_time:
        return None
    identity = envelope.get("identity") or {}
    if not isinstance(identity, dict):
        identity = {}

    def first_value(name: str) -> Any:
        return envelope.get(name) if envelope.get(name) is not None else payload.get(name)

    return {
        "event_id": str(event_id),
        "event_time": str(event_time),
        "tenant_id": str(tenant_id),
        "domain": str(first_value("domain") or "unknown"),
        "master_profile_id": _string_or_none(first_value("master_profile_id")),
        "raw_profile_id": _string_or_none(first_value("raw_profile_id")),
        "external_customer_id": _string_or_none(payload.get("external_customer_id")),
        "device_id": _string_or_none(identity.get("device_id") or payload.get("device_id")),
        "session_id": _string_or_none(identity.get("session_id") or payload.get("session_id")),
        "source_system": _string_or_none(payload.get("source_system")) or "tracking",
        "channel": _string_or_none(payload.get("channel")),
        "platform": _string_or_none(payload.get("platform")),
        "event_category": str(envelope.get("event_category") or payload.get("event_category") or "GENERAL"),
        "event_name": _string_or_none(envelope.get("event_name") or payload.get("event_name")),
        "is_conversion": bool(payload.get("is_conversion", False)),
        "entity_type": _string_or_none(payload.get("entity_type")),
        "entity_id": _string_or_none(payload.get("entity_id")),
        "event_value": payload.get("event_value"),
        "currency": _string_or_none(payload.get("currency")),
        "transaction_id": _string_or_none(payload.get("transaction_id")),
        "transaction_status": _string_or_none(payload.get("transaction_status")),
        "location_name": _string_or_none(payload.get("location_name")),
        "event_payload": payload,
        "created_at": str(envelope.get("received_at") or event_time),
    }


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
