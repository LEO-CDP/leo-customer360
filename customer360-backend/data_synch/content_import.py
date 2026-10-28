"""Tenant-scoped, transactional import of staged content TSV rows."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Callable

import boto3
import psycopg2
from botocore.client import Config
from psycopg2.extras import execute_values
from pydantic import ValidationError

from leo_customer360_dao.config import settings
from leo_customer360_dao.schemas.content_import import ContentImportRecord

logger = logging.getLogger(__name__)

DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
MAX_CONTENT_IMPORT_OBJECT_BYTES = 15 * 1024 * 1024
MAX_CONTENT_IMPORT_ROWS = 5_000
CONTENT_IMPORT_PREFIX = "content-imports"

CONTENT_UPSERT_SQL = f"""
    INSERT INTO {DB_SCHEMA}.cdp_content_items (
        content_item_id, tenant_id, domain, item_type, title, summary,
        image_url, cta_label, cta_url, segment_tags, published_at, status_code
    ) VALUES %s
    ON CONFLICT (content_item_id)
    DO UPDATE SET
        domain = EXCLUDED.domain,
        item_type = EXCLUDED.item_type,
        title = EXCLUDED.title,
        summary = EXCLUDED.summary,
        image_url = EXCLUDED.image_url,
        cta_label = EXCLUDED.cta_label,
        cta_url = EXCLUDED.cta_url,
        segment_tags = EXCLUDED.segment_tags,
        published_at = EXCLUDED.published_at,
        status_code = EXCLUDED.status_code,
        updated_at = now()
"""


class ContentImportError(RuntimeError):
    """Raised when a staged content import cannot be validated or persisted."""


def _build_s3_client():
    kwargs: dict[str, Any] = {
        "region_name": settings.event_s3_region,
        "verify": settings.event_s3_verify_ssl,
        "config": Config(
            s3={
                "addressing_style": (
                    "path" if settings.event_s3_force_path_style else "auto"
                )
            }
        ),
    }
    if settings.event_s3_endpoint_url:
        kwargs["endpoint_url"] = settings.event_s3_endpoint_url
    if settings.event_s3_access_key_id:
        kwargs["aws_access_key_id"] = settings.event_s3_access_key_id
    if settings.event_s3_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.event_s3_secret_access_key
    if settings.event_s3_session_token:
        kwargs["aws_session_token"] = settings.event_s3_session_token
    return boto3.client("s3", **kwargs)


def _connect():
    return psycopg2.connect(
        host=settings.db_host,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        port=settings.db_port,
    )


def _load_content_records(
    *,
    tenant_id: uuid.UUID,
    bucket: str,
    object_key: str,
    s3_client: Any,
) -> list[ContentImportRecord]:
    if bucket != settings.content_import_s3_bucket:
        raise ContentImportError("Import bucket does not match configured content-import storage")
    if not object_key.startswith(f"{CONTENT_IMPORT_PREFIX}/{tenant_id}/"):
        raise ContentImportError("Import object key is outside the requested tenant prefix")
    try:
        response = s3_client.get_object(Bucket=bucket, Key=object_key)
        body = response["Body"]
        try:
            raw_data = body.read(MAX_CONTENT_IMPORT_OBJECT_BYTES + 1)
        finally:
            body.close()
    except Exception as exc:
        logger.exception("Could not read staged content import for tenant_id=%s", tenant_id)
        raise ContentImportError("Could not read staged content import") from exc
    if len(raw_data) > MAX_CONTENT_IMPORT_OBJECT_BYTES:
        raise ContentImportError("Staged content import exceeds the configured size limit")
    try:
        payload = json.loads(raw_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentImportError("Staged content import is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("tenant_id") != str(tenant_id):
        raise ContentImportError("Staged content import tenant does not match the Dagster run")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not 1 <= len(raw_records) <= MAX_CONTENT_IMPORT_ROWS:
        raise ContentImportError("Staged content import contains an invalid row count")
    try:
        records = [ContentImportRecord.model_validate(row) for row in raw_records]
    except ValidationError as exc:
        raise ContentImportError(f"Staged content import contains invalid rows: {exc}") from exc
    return records


def persist_content_records(
    *,
    tenant_id: uuid.UUID,
    object_key: str,
    records: list[ContentImportRecord],
    connection_factory: Callable[[], Any] = _connect,
) -> int:
    """Upsert all staged content rows in a single tenant-scoped transaction."""
    if not records:
        raise ContentImportError("There are no content rows to import")
    with connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            domains = sorted({record.domain for record in records})
            cursor.execute(
                f"SELECT domain_code FROM {DB_SCHEMA}.sys_domain "
                "WHERE is_active = TRUE AND domain_code = ANY(%s)",
                (domains,),
            )
            active_domains = {row[0] for row in cursor.fetchall()}
            invalid_domains = sorted(set(domains) - active_domains)
            if invalid_domains:
                raise ContentImportError(
                    "Inactive or unknown content domain(s): " + ", ".join(invalid_domains)
                )

            content_rows = [
                (
                    str(uuid.uuid5(tenant_id, f"{object_key}:{index}")),
                    str(tenant_id),
                    record.domain,
                    record.item_type,
                    record.title,
                    record.summary,
                    record.image_url,
                    record.cta_label,
                    record.cta_url,
                    record.segment_tags,
                    record.published_at,
                    record.status_code,
                )
                for index, record in enumerate(records)
            ]
            execute_values(
                cursor,
                CONTENT_UPSERT_SQL,
                content_rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                page_size=500,
            )
    return len(records)


def import_content_file(
    *,
    tenant_id: str,
    bucket: str,
    object_key: str,
    s3_client: Any | None = None,
    connection_factory: Callable[[], Any] = _connect,
) -> dict[str, Any]:
    """Load and import one staged content TSV object."""
    parsed_tenant_id = uuid.UUID(tenant_id)
    staged_s3_client = s3_client or _build_s3_client()
    owns_s3_client = s3_client is None
    try:
        records = _load_content_records(
            tenant_id=parsed_tenant_id,
            bucket=bucket,
            object_key=object_key,
            s3_client=staged_s3_client,
        )
        imported = persist_content_records(
            tenant_id=parsed_tenant_id,
            object_key=object_key,
            records=records,
            connection_factory=connection_factory,
        )
        try:
            staged_s3_client.delete_object(Bucket=bucket, Key=object_key)
        except Exception:
            logger.exception(
                "Content import completed but staged object cleanup failed (tenant_id=%s)",
                parsed_tenant_id,
            )
        logger.info("Imported %d content rows for tenant_id=%s", imported, parsed_tenant_id)
        return {"tenant_id": str(parsed_tenant_id), "content_items_imported": imported}
    finally:
        if owns_s3_client:
            staged_s3_client.close()
