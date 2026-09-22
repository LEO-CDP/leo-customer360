"""Build master-profile event JSON projections from S3 RAW objects."""

from __future__ import annotations

import gzip
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from psycopg2.extras import RealDictCursor

from leo_customer360_dao.config import Settings
from leo_customer360_dao.repositories.master_profile_event_repository import (
    MasterProfileEventStore,
)

from .rls import set_tenant_context

logger = logging.getLogger(__name__)

_IDENTITY_FIELDS = (
    "external_customer_id",
    "user_id",
    "anonymous_id",
    "email",
    "phone_number",
    "device_id",
    "advertising_id",
    "cookie_id",
    "session_id",
    "device_fingerprint",
)


@dataclass(frozen=True)
class _RawMatcher:
    raw_profile_id: str
    master_profile_id: str
    data_source_id: str
    identifiers: frozenset[str]


class MasterProfileEventProjector:
    """Materialize one full event JSON file per resolved master profile."""

    def __init__(
        self,
        db_connection: Any,
        schema: str = "customer360",
        store: MasterProfileEventStore | None = None,
    ):
        self.conn = db_connection
        self.schema = schema
        self.settings = Settings()
        self.store = store or MasterProfileEventStore(self.settings)

    def project_profiles(
        self,
        tenant_id: str,
        master_profile_ids: set[str],
    ) -> None:
        if not master_profile_ids:
            return
        bucket = getattr(self.store, "bucket", "<configured-store>")
        logger.info(
            "Projecting %d master profiles for tenant %s into bucket %s",
            len(master_profile_ids),
            tenant_id,
            bucket,
        )
        matchers = self._load_matchers(tenant_id, master_profile_ids)
        if not matchers:
            logger.warning(
                "No active raw-profile matchers found for tenant %s; "
                "master-profile projections will contain no events",
                tenant_id,
            )
        events_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen_by_master: dict[str, set[str]] = defaultdict(set)
        by_source: dict[str, list[_RawMatcher]] = defaultdict(list)
        for matcher in matchers:
            by_source[matcher.data_source_id].append(matcher)

        for data_source_id, source_matchers in by_source.items():
            identifier_index: dict[str, set[_RawMatcher]] = defaultdict(set)
            raw_profile_index: dict[str, set[_RawMatcher]] = defaultdict(set)
            for matcher in source_matchers:
                raw_profile_index[matcher.raw_profile_id].add(matcher)
                for identifier in matcher.identifiers:
                    identifier_index[identifier].add(matcher)

            for object_key in self._iter_source_objects(data_source_id):
                for envelope in self._iter_object(data_source_id, object_key):
                    matched = self._match_event(envelope, identifier_index, raw_profile_index)
                    for matcher in matched:
                        event_id = str(envelope.get("event_id") or object_key)
                        if event_id in seen_by_master[matcher.master_profile_id]:
                            continue
                        seen_by_master[matcher.master_profile_id].add(event_id)
                        projected = dict(envelope)
                        projected["tenant_id"] = tenant_id
                        projected.setdefault("data_source_id", matcher.data_source_id)
                        projected["master_profile_id"] = matcher.master_profile_id
                        projected.setdefault("raw_profile_id", matcher.raw_profile_id)
                        events_by_master[matcher.master_profile_id].append(projected)

        for master_profile_id in master_profile_ids:
            events = sorted(
                events_by_master.get(master_profile_id, []),
                key=self._event_sort_key,
                reverse=True,
            )
            self.store.put_events(
                UUID(tenant_id),
                UUID(master_profile_id),
                events,
            )
            logger.info(
                "Wrote master profile projection tenant=%s master_profile_id=%s events=%d bucket=%s",
                tenant_id,
                master_profile_id,
                len(events),
                bucket,
            )

    def _load_matchers(
        self,
        tenant_id: str,
        master_profile_ids: set[str],
    ) -> list[_RawMatcher]:
        placeholders = ",".join(["%s"] * len(master_profile_ids))
        query = f"""
            SELECT
                raw.raw_profile_id,
                link.master_profile_id,
                raw.data_source_id,
                raw.external_customer_id,
                raw.email,
                raw.phone_number,
                raw.device_id,
                raw.advertising_id,
                raw.cookie_id,
                raw.session_id,
                raw.event_payload
            FROM {self.schema}.cdp_profile_links AS link
            JOIN {self.schema}.cdp_raw_profiles_stage AS raw
              ON raw.tenant_id = link.tenant_id
             AND raw.raw_profile_id = link.raw_profile_id
            WHERE link.tenant_id = %s
              AND link.master_profile_id IN ({placeholders})
              AND link.status = 'ACTIVE'
              AND raw.data_source_id IS NOT NULL
        """
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            set_tenant_context(cursor, tenant_id)
            cursor.execute(query, (tenant_id, *sorted(master_profile_ids)))
            rows = cursor.fetchall()

        matchers: list[_RawMatcher] = []
        for row in rows:
            payload = row.get("event_payload") if isinstance(row.get("event_payload"), dict) else {}
            identifiers = set()
            for field in _IDENTITY_FIELDS:
                value = row.get(field) or payload.get(field)
                if value:
                    identifiers.add(str(value).strip().casefold())
            if not identifiers:
                continue
            matchers.append(
                _RawMatcher(
                    raw_profile_id=str(row["raw_profile_id"]),
                    master_profile_id=str(row["master_profile_id"]),
                    data_source_id=str(row["data_source_id"]),
                    identifiers=frozenset(identifiers),
                )
            )
        return matchers

    def _iter_source_objects(self, data_source_id: str) -> Iterator[str]:
        bucket = f"data-tracking-{data_source_id}"
        try:
            paginator = self.store.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix="events/"):
                for item in page.get("Contents", []):
                    key = str(item.get("Key", ""))
                    if key.endswith(".jsonl") or key.endswith(".jsonl.gz"):
                        yield key
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchBucket", "NotFound"}:
                return
            raise
        except BotoCoreError:
            raise

    def _iter_object(self, data_source_id: str, object_key: str) -> Iterator[dict[str, Any]]:
        bucket = f"data-tracking-{data_source_id}"
        body = None
        reader = None
        try:
            body = self.store.s3.get_object(Bucket=bucket, Key=object_key)["Body"]
            reader = gzip.GzipFile(fileobj=body) if object_key.endswith(".gz") else body
            for line in reader:
                if not line.strip():
                    continue
                envelope = json.loads(line)
                if isinstance(envelope, dict):
                    yield envelope
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchBucket", "NotFound"}:
                return
            raise
        except BotoCoreError:
            raise
        finally:
            if reader is not None and reader is not body:
                reader.close()
            if body is not None:
                body.close()

    @staticmethod
    def _match_event(
        envelope: dict[str, Any],
        identifier_index: dict[str, set[_RawMatcher]],
        raw_profile_index: dict[str, set[_RawMatcher]],
    ) -> set[_RawMatcher]:
        payload = envelope.get("payload") or envelope.get("event")
        payload = payload if isinstance(payload, dict) else {}
        identity = envelope.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        values = {
            str(value).strip().casefold()
            for field in _IDENTITY_FIELDS
            for value in (identity.get(field), payload.get(field), envelope.get(field))
            if value
        }
        direct_raw_profile_id = payload.get("raw_profile_id") or envelope.get("raw_profile_id")
        if direct_raw_profile_id:
            direct = raw_profile_index.get(str(direct_raw_profile_id), set())
            if direct:
                return direct
        matched: set[_RawMatcher] = set()
        for value in values:
            matched.update(identifier_index.get(value, set()))
        return matched

    @staticmethod
    def _event_sort_key(event: dict[str, Any]) -> datetime:
        value = event.get("event_time")
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                pass
        return datetime.min.replace(tzinfo=timezone.utc)
