"""Parse and stage content TSV uploads for asynchronous import."""

from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import dataclass
from io import StringIO
from typing import Any

from pydantic import ValidationError

from core.utils.s3_buckets import build_s3_client
from leo_customer360_dao.config import settings
from leo_customer360_dao.schemas.content_import import ContentImportRecord

logger = logging.getLogger(__name__)

MAX_CONTENT_TSV_BYTES = 10 * 1024 * 1024
MAX_CONTENT_TSV_ROWS = 5_000
MAX_CONTENT_TSV_COLUMNS = 64
MAX_CONTENT_FIELD_CHARACTERS = 32_000
S3_UPLOAD_PREFIX = "content-imports"

_REQUIRED_COLUMNS = {"Domain", "Item_Type", "Title"}
_OPTIONAL_COLUMNS = {
    "Summary",
    "Image_URL",
    "CTA_Label",
    "CTA_URL",
    "Segment_Tags",
    "Published_At",
    "Status_Code",
}


class ContentTsvError(ValueError):
    """Raised for malformed or invalid content TSV input."""


class ContentImportStorageError(RuntimeError):
    """Raised when validated content cannot be staged for Dagster."""


@dataclass(frozen=True)
class StagedContentImport:
    bucket: str
    object_key: str
    tenant_id: str
    row_count: int


def parse_content_tsv(content: bytes) -> list[ContentImportRecord]:
    """Parse UTF-8 TSV fields, validating column names and row types before staging."""
    if not content:
        raise ContentTsvError("The uploaded TSV is empty")
    if len(content) > MAX_CONTENT_TSV_BYTES:
        raise ContentTsvError(f"The uploaded TSV exceeds the {MAX_CONTENT_TSV_BYTES}-byte limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContentTsvError("The uploaded TSV must be UTF-8 encoded") from exc

    try:
        reader = csv.DictReader(StringIO(text, newline=""), delimiter="\t", strict=True)
        headers = reader.fieldnames
        if not headers:
            raise ContentTsvError("The uploaded TSV must include a header row")
        if any(not header or not header.strip() for header in headers):
            raise ContentTsvError("TSV column names must not be blank")
        if len(headers) > MAX_CONTENT_TSV_COLUMNS:
            raise ContentTsvError(f"TSV uploads are limited to {MAX_CONTENT_TSV_COLUMNS} columns")
        if len(headers) != len(set(headers)):
            raise ContentTsvError("The TSV contains duplicate column names")
        missing = sorted(_REQUIRED_COLUMNS - set(headers))
        if missing:
            raise ContentTsvError("Missing required TSV column(s): " + ", ".join(missing))
        unknown = sorted(set(headers) - _REQUIRED_COLUMNS - _OPTIONAL_COLUMNS)
        if unknown:
            raise ContentTsvError("Unsupported TSV column(s): " + ", ".join(unknown))

        records: list[ContentImportRecord] = []
        for row_number, raw_row in enumerate(reader, start=2):
            if row_number > MAX_CONTENT_TSV_ROWS + 1:
                raise ContentTsvError(f"The TSV exceeds the {MAX_CONTENT_TSV_ROWS}-row limit")
            if None in raw_row:
                raise ContentTsvError(f"Row {row_number}: row has more fields than the header")
            if not any((value or "").strip() for value in raw_row.values()):
                continue

            row = {header: (raw_row.get(header) or "").strip() for header in headers}
            if sum(len(key) + len(value) for key, value in row.items()) > MAX_CONTENT_FIELD_CHARACTERS:
                raise ContentTsvError(
                    f"Row {row_number}: combined content fields exceed "
                    f"{MAX_CONTENT_FIELD_CHARACTERS} characters"
                )
            tags = [
                tag.strip()
                for tag in row.get("Segment_Tags", "").split(",")
                if tag.strip()
            ]
            published_at = row.get("Published_At") or None
            try:
                record = ContentImportRecord.model_validate(
                    {
                        "domain": row["Domain"],
                        "item_type": row["Item_Type"].lower(),
                        "title": row["Title"],
                        "summary": row.get("Summary") or None,
                        "image_url": row.get("Image_URL") or None,
                        "cta_label": row.get("CTA_Label") or None,
                        "cta_url": row.get("CTA_URL") or None,
                        "segment_tags": tags,
                        "published_at": published_at,
                        "status_code": int(row.get("Status_Code") or "1"),
                    }
                )
            except (ValidationError, ValueError, TypeError) as exc:
                detail = str(exc)
                if isinstance(exc, ValidationError):
                    detail = "; ".join(
                        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                        for error in exc.errors()
                    )
                raise ContentTsvError(f"Row {row_number}: {detail}") from exc
            records.append(record)

        if not records:
            raise ContentTsvError("The TSV does not contain any content rows")
        return records
    except csv.Error as exc:
        raise ContentTsvError(f"Malformed TSV near line {reader.line_num}: {exc}") from exc


def stage_content_import(
    tenant_id: uuid.UUID,
    records: list[ContentImportRecord],
    *,
    s3_client: Any | None = None,
) -> StagedContentImport:
    """Stage validated content rows in the configured S3 bucket for Dagster."""
    bucket = settings.content_import_s3_bucket
    object_key = f"{S3_UPLOAD_PREFIX}/{tenant_id}/{uuid.uuid4()}.json"
    payload = {
        "tenant_id": str(tenant_id),
        "records": [record.model_dump(mode="json") for record in records],
    }
    client = s3_client or build_s3_client()
    owns_client = s3_client is None
    try:
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:
        logger.exception("Could not stage content import for tenant_id=%s", tenant_id)
        raise ContentImportStorageError("Could not stage the content import file") from exc
    finally:
        if owns_client:
            client.close()
    return StagedContentImport(bucket, object_key, str(tenant_id), len(records))


def delete_staged_content_import(
    bucket: str,
    object_key: str,
    *,
    s3_client: Any | None = None,
) -> None:
    """Delete an object when Dagster rejected the staged content import."""
    client = s3_client or build_s3_client()
    owns_client = s3_client is None
    try:
        client.delete_object(Bucket=bucket, Key=object_key)
    finally:
        if owns_client:
            client.close()
