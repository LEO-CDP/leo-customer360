"""Tenant-scoped Polars queries over canonical S3/MinIO event objects."""

import gzip
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Optional
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
        lower_bound, upper_bound = self._query_bounds(event_time_from, days)

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

    def query_hourly_totals(
        self,
        db: Session,
        tenant_id: UUID,
        *,
        event_time_from: Optional[datetime] = None,
        days: int,
        master_profile_id: Optional[UUID] = None,
        domain: Optional[str] = None,
        channel: Optional[str] = None,
        event_category: Optional[str] = None,
        event_name: Optional[str] = None,
        data_source_id: Optional[UUID] = None,
    ) -> list[dict[str, Any]]:
        """Return complete UTC-hour totals without truncating raw events first."""
        counts = self._aggregate_event_counts(
            db,
            tenant_id,
            event_time_from=event_time_from,
            days=days,
            group_key=lambda row, event_time: event_time.replace(
                minute=0, second=0, microsecond=0
            ),
            master_profile_id=master_profile_id,
            domain=domain,
            channel=channel,
            event_category=event_category,
            event_name=event_name,
            data_source_id=data_source_id,
        )
        return [
            {"hour": hour, "total": total}
            for hour, total in sorted(counts.items())
        ]

    def query_daily_totals(
        self,
        db: Session,
        tenant_id: UUID,
        *,
        event_time_from: Optional[datetime] = None,
        days: int,
        master_profile_id: Optional[UUID] = None,
        domain: Optional[str] = None,
        channel: Optional[str] = None,
        event_category: Optional[str] = None,
        event_name: Optional[str] = None,
        data_source_id: Optional[UUID] = None,
    ) -> list[dict[str, Any]]:
        """Return a complete UTC-day series aggregated from hourly totals."""
        counts = self._aggregate_event_counts(
            db,
            tenant_id,
            event_time_from=event_time_from,
            days=days,
            group_key=lambda row, event_time: event_time.date(),
            master_profile_id=master_profile_id,
            domain=domain,
            channel=channel,
            event_category=event_category,
            event_name=event_name,
            data_source_id=data_source_id,
        )
        lower_bound, upper_bound = self._query_bounds(event_time_from, days)

        current_day = lower_bound.date()
        final_day = upper_bound.date()
        return [
            {
                "day": current_day + timedelta(days=offset),
                "total": counts.get(current_day + timedelta(days=offset), 0),
            }
            for offset in range((final_day - current_day).days + 1)
        ]

    def query_channel_totals(
        self,
        db: Session,
        tenant_id: UUID,
        *,
        event_time_from: Optional[datetime] = None,
        days: int,
        data_source_id: Optional[UUID] = None,
    ) -> list[dict[str, Any]]:
        """Return complete channel totals for the requested event window."""
        counts = self._aggregate_event_counts(
            db,
            tenant_id,
            event_time_from=event_time_from,
            days=days,
            group_key=lambda row, _event_time: row.get("channel") or "unknown",
            data_source_id=data_source_id,
        )
        return [
            {"channel": channel, "total": total}
            for channel, total in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]

    def _aggregate_event_counts(
        self,
        db: Session,
        tenant_id: UUID,
        *,
        event_time_from: Optional[datetime],
        days: int,
        group_key: Any,
        master_profile_id: Optional[UUID] = None,
        domain: Optional[str] = None,
        channel: Optional[str] = None,
        event_category: Optional[str] = None,
        event_name: Optional[str] = None,
        data_source_id: Optional[UUID] = None,
    ) -> Counter:
        lower_bound, upper_bound = self._query_bounds(event_time_from, days)
        counts: Counter = Counter()
        for row, event_time in self._iter_filtered_rows(
            db,
            tenant_id,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            master_profile_id=master_profile_id,
            domain=domain,
            channel=channel,
            event_category=event_category,
            event_name=event_name,
            data_source_id=data_source_id,
        ):
            counts[group_key(row, event_time)] += 1
        return counts

    def _iter_filtered_rows(
        self,
        db: Session,
        tenant_id: UUID,
        *,
        lower_bound: datetime,
        upper_bound: datetime,
        master_profile_id: Optional[UUID],
        domain: Optional[str],
        channel: Optional[str],
        event_category: Optional[str],
        event_name: Optional[str],
        data_source_id: Optional[UUID],
    ) -> Iterator[tuple[dict[str, Any], datetime]]:
        filters = {
            "master_profile_id": str(master_profile_id) if master_profile_id else None,
            "domain": domain,
            "channel": channel,
            "event_category": event_category,
            "event_name": event_name,
        }
        source_ids = self._tenant_source_ids(db, tenant_id, data_source_id)
        for source_id in source_ids:
            for object_key in self._list_object_keys(
                source_id, tenant_id, lower_bound, upper_bound
            ):
                for row in self._iter_object(source_id, tenant_id, object_key):
                    event_time = _parse_event_time(row.get("event_time"))
                    if event_time is None or not (lower_bound <= event_time <= upper_bound):
                        continue
                    if any(
                        value is not None and row.get(column) != value
                        for column, value in filters.items()
                    ):
                        continue
                    yield row, event_time

    def _query_bounds(
        self,
        event_time_from: Optional[datetime],
        days: int,
    ) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        bounded_days = min(max(1, int(days)), max(1, int(self.settings.event_query_max_days)))
        lower_bound = now - timedelta(days=bounded_days)
        if event_time_from is not None:
            lower_bound = max(lower_bound, _as_utc(event_time_from))
        return lower_bound, now

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
        current_day = lower_bound.date()
        final_day = upper_bound.date()
        while current_day <= final_day:
            day_text = current_day.isoformat()
            if self.settings.event_s3_bucket:
                prefix = (
                    f"{self.settings.event_s3_prefix.rstrip('/')}/"
                    f"tenant_id={tenant_id}/source_id={source_id}/event_hour={day_text}-"
                )
            else:
                prefix = f"{self.settings.event_s3_prefix.rstrip('/')}/{day_text}-"
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
            current_day += timedelta(days=1)

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

        return list(self._iter_object_body(body, object_key, tenant_id))

    def _iter_object(
        self,
        source_id: UUID,
        tenant_id: UUID,
        object_key: str,
    ) -> Iterator[dict[str, Any]]:
        bucket = self.settings.event_s3_bucket or f"data-tracking-{source_id}"
        try:
            response = self.s3.get_object(Bucket=bucket, Key=object_key)
            body = response["Body"].read()
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if error_code in {"404", "NoSuchBucket", "NotFound"} or status_code == 404:
                return
            raise EventQueryError(f"Could not read event object {object_key}") from exc
        except BotoCoreError as exc:
            raise EventQueryError(f"Could not read event object {object_key}") from exc

        yield from self._iter_object_body(body, object_key, tenant_id)

    @staticmethod
    def _iter_object_body(
        body: bytes,
        object_key: str,
        tenant_id: UUID,
    ) -> Iterator[dict[str, Any]]:
        if object_key.endswith(".gz"):
            try:
                body = gzip.decompress(body)
            except (EOFError, OSError) as exc:
                raise EventQueryError(f"Malformed compressed event object {object_key}") from exc
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
                yield row


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


def _parse_event_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _hour_start(value: datetime) -> datetime:
    """Floor a timestamp to the beginning of its UTC hour."""
    return _as_utc(value).replace(minute=0, second=0, microsecond=0)
