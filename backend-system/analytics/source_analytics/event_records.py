"""Event-envelope parsing and profile staging operations."""

import gzip
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Callable, Iterable, Iterator, Optional
from uuid import NAMESPACE_URL, UUID, uuid5


EVENT_CATEGORIES = frozenset(
    {
        "GENERAL",
        "EDUCATION",
        "COMMERCE",
        "FEEDBACK",
        "FINANCE",
        "STOCK_TRADING",
        "TRAVEL",
        "REAL_ESTATE",
        "SERVICE_INDUSTRY",
    }
)


class EventEnvelopeError(ValueError):
    """Raised when an S3 record cannot satisfy the governed event contract."""


class EventRecordService:
    """Decode, validate, normalize, and stage tracking events."""

    def __init__(self, db_schema: str) -> None:
        self.db_schema = db_schema

    @staticmethod
    def iter_jsonl_lines(body: Any, object_key: str) -> Iterator[Any]:
        """Yield decoded JSONL lines from plain or gzip-compressed bodies."""
        if object_key.endswith(".gz"):
            file_object = body if hasattr(body, "read") else BytesIO(body)
            with gzip.GzipFile(fileobj=file_object, mode="rb") as compressed:
                yield from compressed
            return
        if isinstance(body, (bytes, bytearray)):
            yield from BytesIO(body)
            return
        yield from (body.iter_lines() if hasattr(body, "iter_lines") else body)

    @staticmethod
    def parse_event_datetime(value: Any) -> Optional[datetime]:
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

    @staticmethod
    def text_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def identity_value(
        cls,
        identity: dict[str, Any],
        payload: dict[str, Any],
        key: str,
    ) -> Optional[str]:
        return cls.text_value(identity.get(key) or payload.get(key))

    @classmethod
    def extract_profile_signature(cls, record: dict[str, Any]) -> Optional[str]:
        """Extract a stable profile signature from one tracking record."""
        event = record.get("payload") or record.get("event")
        if not isinstance(event, dict):
            return None

        direct_keys = [
            "external_customer_id",
            "user_id",
            "email",
            "phone_number",
            "device_id",
            "advertising_id",
            "cookie_id",
            "session_id",
        ]
        for key in direct_keys:
            normalized = cls.text_value(event.get(key))
            if normalized:
                return f"{key}:{normalized.lower()}"

        identities = event.get("profile_identities")
        if isinstance(identities, dict):
            for key in direct_keys:
                normalized = cls.text_value(identities.get(key))
                if normalized:
                    return f"{key}:{normalized.lower()}"
        return None

    def normalize_event_record(
        self,
        record: dict[str, Any],
        data_source_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Normalize canonical or legacy JSONL into the event contract."""
        schema_version = record.get("schema_version")
        if schema_version not in (None, 1):
            raise EventEnvelopeError(f"unsupported schema_version: {schema_version}")
        payload = record.get("payload") or record.get("event")
        if not isinstance(payload, dict):
            if "event" in record and payload is not None:
                payload = {"event": payload}
            else:
                raise EventEnvelopeError("event payload must be an object")

        event_id = record.get("event_id") or payload.get("event_id")
        if not event_id:
            event_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"c360:legacy-event:{tenant_id}:{data_source_id}:"
                    f"{json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)}",
                )
            )
        try:
            event_id = str(UUID(str(event_id)))
        except (ValueError, TypeError) as exc:
            raise EventEnvelopeError("event_id must be a UUID") from exc

        event_time = self.parse_event_datetime(
            record.get("event_time") or payload.get("event_time")
        )
        if event_time is None:
            event_time = self.parse_event_datetime(record.get("received_at"))
        if event_time is None:
            raise EventEnvelopeError("event_time must be a valid UTC timestamp")

        event_name = (
            self.text_value(
                record.get("event_name")
                or payload.get("event_name")
                or payload.get("eventType")
            )
            or self.text_value(payload.get("event"))
            or "unknown"
        )
        event_category = (
            self.text_value(record.get("event_category") or payload.get("event_category"))
            or "GENERAL"
        ).upper()
        if event_category not in EVENT_CATEGORIES:
            event_category = "GENERAL"
        identity = record.get("identity")
        if not isinstance(identity, dict):
            identity = {}

        return {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "data_source_id": data_source_id,
            "domain": self.text_value(payload.get("domain")) or "unknown",
            "source_system": self.text_value(
                record.get("source_system") or payload.get("source_system")
            )
            or "tracking",
            "master_profile_id": self.text_value(
                record.get("master_profile_id") or payload.get("master_profile_id")
            ),
            "raw_profile_id": self.text_value(payload.get("raw_profile_id")),
            "external_customer_id": self.identity_value(
                identity, payload, "external_customer_id"
            ),
            "email": self.identity_value(identity, payload, "email"),
            "phone_number": self.identity_value(identity, payload, "phone_number"),
            "device_id": self.identity_value(identity, payload, "device_id"),
            "advertising_id": self.identity_value(identity, payload, "advertising_id"),
            "cookie_id": self.identity_value(identity, payload, "cookie_id"),
            "session_id": self.identity_value(identity, payload, "session_id"),
            "channel": self.text_value(payload.get("channel")),
            "platform": self.text_value(payload.get("platform")),
            "event_category": event_category,
            "event_name": event_name,
            "event_dedup_key": self.text_value(
                record.get("event_dedup_key") or payload.get("event_dedup_key")
            ),
            "event_value": payload.get("event_value"),
            "currency": self.text_value(payload.get("currency")),
            "entity_type": self.text_value(payload.get("entity_type")),
            "entity_id": self.text_value(payload.get("entity_id")),
            "transaction_id": self.text_value(payload.get("transaction_id")),
            "transaction_status": self.text_value(payload.get("transaction_status")),
            "location_name": self.text_value(payload.get("location_name")),
            "is_conversion": bool(payload.get("is_conversion", False)),
            "event_time": event_time.isoformat(),
            "received_at": record.get("received_at") or event_time.isoformat(),
            "payload": payload,
        }

    def iter_normalized_event_records(
        self,
        body: Any,
        object_key: str,
        data_source_id: str,
        tenant_id: str,
    ) -> Iterator[dict[str, Any]]:
        """Stream normalized records so one large object is not materialized."""
        for raw_line in self.iter_jsonl_lines(body, object_key):
            if not raw_line or not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except (TypeError, json.JSONDecodeError) as exc:
                raise EventEnvelopeError(f"Invalid JSONL in object {object_key}") from exc
            if not isinstance(record, dict):
                raise EventEnvelopeError(
                    f"JSONL record in object {object_key} must be an object"
                )
            payload = record.get("payload")
            if not record.get("event_time") and not (
                isinstance(payload, dict) and payload.get("event_time")
            ):
                hour_match = re.search(r"(\d{4}-\d{2}-\d{2}-\d{2})", object_key)
                if hour_match:
                    record = dict(record)
                    record["event_time"] = datetime.strptime(
                        hour_match.group(1), "%Y-%m-%d-%H"
                    ).replace(tzinfo=timezone.utc).isoformat()
            yield self.normalize_event_record(record, data_source_id, tenant_id)

    def read_normalized_event_records(
        self,
        body: Any,
        object_key: str,
        data_source_id: str,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        return list(
            self.iter_normalized_event_records(
                body, object_key, data_source_id, tenant_id
            )
        )

    def count_jsonl_records(self, body: Any, object_key: str) -> int:
        count = 0
        for raw_line in self.iter_jsonl_lines(body, object_key):
            if not raw_line or not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid JSONL in object {object_key}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record in object {object_key} must be an object")
            count += 1
        return count

    def count_records_and_signatures(
        self,
        body: Any,
        object_key: str,
    ) -> tuple[int, set[str]]:
        count = 0
        signatures: set[str] = set()
        for raw_line in self.iter_jsonl_lines(body, object_key):
            if not raw_line or not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid JSONL in object {object_key}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record in object {object_key} must be an object")
            count += 1
            signature = self.extract_profile_signature(record)
            if signature:
                signatures.add(signature)
        return count, signatures

    def summarize_bucket_metrics(
        self,
        s3_client: Any,
        bucket: str,
        object_iterator: Callable[[Any, str], Iterable[tuple[str, str]]],
    ) -> tuple[int, int, float]:
        total_tracked_event = 0
        days_with_events: set[str] = set()
        profile_signatures: set[str] = set()

        for hour, object_key in object_iterator(s3_client, bucket):
            response = s3_client.get_object(Bucket=bucket, Key=object_key)
            body = response["Body"]
            object_count = 0
            try:
                for raw_line in self.iter_jsonl_lines(body, object_key):
                    if not raw_line or not raw_line.strip():
                        continue
                    try:
                        record = json.loads(raw_line)
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ValueError(f"Invalid JSONL in object {object_key}") from exc
                    if not isinstance(record, dict):
                        raise ValueError(
                            f"JSONL record in object {object_key} must be an object"
                        )
                    object_count += 1
                    signature = self.extract_profile_signature(record)
                    if signature:
                        profile_signatures.add(signature)
            finally:
                close = getattr(body, "close", None)
                if close:
                    close()

            if object_count > 0:
                days_with_events.add(hour[:10])
                total_tracked_event += object_count

        avg_daily_event = (
            round(total_tracked_event / len(days_with_events))
            if days_with_events
            else 0
        )
        avg_events_per_profile = (
            round(total_tracked_event / len(profile_signatures), 2)
            if profile_signatures
            else 0.0
        )
        return total_tracked_event, avg_daily_event, avg_events_per_profile

    def upsert_raw_profile(self, cursor: Any, event: dict[str, Any]) -> str:
        """Upsert one deterministic raw profile from a normalized event."""
        identity_pairs = (
            ("external_customer_id", event.get("external_customer_id")),
            ("email", event.get("email")),
            ("phone_number", event.get("phone_number")),
            ("device_id", event.get("device_id")),
            ("advertising_id", event.get("advertising_id")),
            ("cookie_id", event.get("cookie_id")),
            ("session_id", event.get("session_id")),
        )
        identity_type, identity_value = next(
            ((key, value) for key, value in identity_pairs if value),
            ("event_id", event["event_id"]),
        )
        raw_profile_id = str(
            uuid5(
                NAMESPACE_URL,
                f"c360:raw-profile:{event['tenant_id']}:{event['data_source_id']}:{identity_type}:{identity_value}",
            )
        )
        event["raw_profile_id"] = raw_profile_id
        try:
            from psycopg2.extras import Json

            json_payload: Any = Json(event["payload"])
        except ImportError:
            json_payload = json.dumps(event["payload"], ensure_ascii=False)
        cursor.execute(
            f"""
            INSERT INTO {self.db_schema}.cdp_raw_profiles_stage (
                raw_profile_id, tenant_id, domain, source_system, channel,
                external_customer_id, email, phone_number, device_id,
                advertising_id, cookie_id, session_id, event_name, event_time,
                event_payload, status_code, processed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, 1, NULL
            )
            ON CONFLICT (raw_profile_id) DO UPDATE SET
                domain = EXCLUDED.domain,
                source_system = EXCLUDED.source_system,
                channel = EXCLUDED.channel,
                external_customer_id = EXCLUDED.external_customer_id,
                email = EXCLUDED.email,
                phone_number = EXCLUDED.phone_number,
                device_id = EXCLUDED.device_id,
                advertising_id = EXCLUDED.advertising_id,
                cookie_id = EXCLUDED.cookie_id,
                session_id = EXCLUDED.session_id,
                event_name = EXCLUDED.event_name,
                event_time = EXCLUDED.event_time,
                event_payload = EXCLUDED.event_payload,
                status_code = 1,
                processed_at = NULL
            """,
            (
                raw_profile_id,
                event["tenant_id"],
                event["domain"],
                event["source_system"],
                event.get("channel"),
                event.get("external_customer_id"),
                event.get("email"),
                event.get("phone_number"),
                event.get("device_id"),
                event.get("advertising_id"),
                event.get("cookie_id"),
                event.get("session_id"),
                event["event_name"],
                event["event_time"],
                json_payload,
            ),
        )
        return raw_profile_id