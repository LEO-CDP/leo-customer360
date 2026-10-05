"""Parse and stage product TSV uploads for asynchronous catalog import."""

from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any

from pydantic import ValidationError

from leo_customer360_dao.config import settings
from leo_customer360_dao.schemas.product_import import ProductImportRecord
from core.utils.s3_buckets import build_s3_client

logger = logging.getLogger(__name__)

MAX_PRODUCT_TSV_BYTES = 10 * 1024 * 1024
MAX_PRODUCT_TSV_ROWS = 5_000
MAX_SOURCE_FIELDS_CHARACTERS = 32_000
MAX_SOURCE_FIELD_NAME_CHARACTERS = 200
MAX_PRODUCT_TSV_COLUMNS = 256
S3_UPLOAD_PREFIX = "product-imports"

_REQUIRED_COLUMNS = {
    "Product_Type",
    "Store_ID",
    "Product_ID_Type",
    "Product_ID",
    "Name",
    "Full_URL",
}
class ProductTsvError(ValueError):
    """Raised for malformed or invalid product TSV input."""


class ProductImportStorageError(RuntimeError):
    """Raised when a validated import cannot be staged for Dagster."""


@dataclass(frozen=True)
class StagedProductImport:
    bucket: str
    object_key: str
    tenant_id: str
    row_count: int


def _parse_price(value: str, *, row_number: int, field_name: str) -> Decimal | None:
    if not value:
        return None
    try:
        price = Decimal(value)
    except InvalidOperation as exc:
        raise ProductTsvError(
            f"Row {row_number}: {field_name} must be a decimal number"
        ) from exc
    if not price.is_finite() or price < 0:
        raise ProductTsvError(f"Row {row_number}: {field_name} must be finite and non-negative")
    return price


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def parse_product_tsv(content: bytes) -> list[ProductImportRecord]:
    """Parse a UTF-8 TSV with required source columns into validated product rows."""
    if not content:
        raise ProductTsvError("The uploaded TSV is empty")
    if len(content) > MAX_PRODUCT_TSV_BYTES:
        raise ProductTsvError(
            f"The uploaded TSV exceeds the {MAX_PRODUCT_TSV_BYTES}-byte limit"
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProductTsvError("The uploaded TSV must be UTF-8 encoded") from exc

    try:
        reader = csv.DictReader(StringIO(text, newline=""), delimiter="\t", strict=True)
        headers = reader.fieldnames
        if not headers:
            raise ProductTsvError("The uploaded TSV must include a header row")
        if any(not header or not header.strip() for header in headers):
            raise ProductTsvError("TSV column names must not be blank")
        if any(len(header) > MAX_SOURCE_FIELD_NAME_CHARACTERS for header in headers):
            raise ProductTsvError(
                f"TSV column names are limited to {MAX_SOURCE_FIELD_NAME_CHARACTERS} characters"
            )
        if len(headers) != len(set(headers)):
            raise ProductTsvError("The TSV contains duplicate column names")
        if len(headers) > MAX_PRODUCT_TSV_COLUMNS:
            raise ProductTsvError(
                f"TSV uploads are limited to {MAX_PRODUCT_TSV_COLUMNS} columns"
            )

        missing_columns = sorted(_REQUIRED_COLUMNS - set(headers))
        if missing_columns:
            raise ProductTsvError(
                "Missing required TSV column(s): " + ", ".join(missing_columns)
            )

        records: list[ProductImportRecord] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for row_number, raw_row in enumerate(reader, start=2):
            if row_number > MAX_PRODUCT_TSV_ROWS + 1:
                raise ProductTsvError(
                    f"The TSV exceeds the {MAX_PRODUCT_TSV_ROWS}-row limit"
                )
            if None in raw_row:
                raise ProductTsvError(f"Row {row_number}: row has more fields than the header")
            if not any((value or "").strip() for value in raw_row.values()):
                continue

            source_fields = {
                header: (raw_row.get(header) or "").strip()
                for header in headers
            }
            if not source_fields["Name"]:
                raise ProductTsvError(f"Row {row_number}: Name must not be blank")
            if sum(
                len(key) + len(value) for key, value in source_fields.items()
            ) > MAX_SOURCE_FIELDS_CHARACTERS:
                raise ProductTsvError(
                    f"Row {row_number}: combined product fields exceed "
                    f"{MAX_SOURCE_FIELDS_CHARACTERS} characters"
                )
            product_type = source_fields["Product_Type"]
            if product_type == "STOCK":
                domain = "banking"
            elif product_type.endswith("_product") and product_type != "_product":
                domain = product_type[: -len("_product")]
            else:
                raise ProductTsvError(
                    f"Row {row_number}: Product_Type must be STOCK or use the <domain>_product format"
                )
            source_id = source_fields["Store_ID"]
            source_type = source_fields.get("Source_Type", "")
            product_id_type = source_fields["Product_ID_Type"]
            product_id = source_fields["Product_ID"]
            if not source_id and product_type != "STOCK":
                raise ProductTsvError(f"Row {row_number}: Store_ID must not be blank")
            natural_key = (source_type, source_id, product_id_type, product_id)
            if natural_key in seen_keys:
                raise ProductTsvError(
                    f"Row {row_number}: duplicate Source_Type/Store_ID/Product_ID_Type/Product_ID"
                )
            seen_keys.add(natural_key)

            currency = source_fields.get("Currency", "").upper() or None
            if currency and (len(currency) != 3 or not currency.isalpha()):
                raise ProductTsvError(
                    f"Row {row_number}: Currency must be a three-letter code"
                )
            raw_ext_attributes = source_fields.get("ext_attributes", "").strip()
            if raw_ext_attributes:
                try:
                    ext_attributes = json.loads(
                        raw_ext_attributes,
                        parse_constant=_reject_json_constant,
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ProductTsvError(
                        f"Row {row_number}: ext_attributes must be valid JSON"
                    ) from exc
                if not isinstance(ext_attributes, dict):
                    raise ProductTsvError(
                        f"Row {row_number}: ext_attributes must be a JSON object"
                    )
            else:
                ext_attributes = {}
            values: dict[str, Any] = {
                "domain": domain,
                "product_type": product_type,
                "source_id": source_id,
                "source_type": source_type,
                "product_id_type": product_id_type,
                "product_id": product_id,
                "keywords": list(
                    dict.fromkeys(
                        keyword.strip()
                        for keyword in source_fields.get("Keywords", "").split(",")
                        if keyword.strip()
                    )
                ),
                "ext_attributes": ext_attributes,
                "original_price": _parse_price(
                    source_fields.get("Original_Price", ""),
                    row_number=row_number,
                    field_name="Original_Price",
                ),
                "sale_price": _parse_price(
                    source_fields.get("Sale_Price", ""),
                    row_number=row_number,
                    field_name="Sale_Price",
                ),
                "currency": currency,
                "image_url": source_fields.get("Image_URL") or None,
                "product_url": source_fields["Full_URL"],
                "source_fields": source_fields,
            }
            try:
                record = ProductImportRecord.model_validate(values)
            except ValidationError as exc:
                detail = "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
                raise ProductTsvError(f"Row {row_number}: {detail}") from exc

            records.append(record)

        if not records:
            raise ProductTsvError("The TSV does not contain any product rows")
        return records
    except csv.Error as exc:
        raise ProductTsvError(f"Malformed TSV near line {reader.line_num}: {exc}") from exc


def _build_s3_client():
    return build_s3_client()


def stage_product_import(
    tenant_id: uuid.UUID,
    records: list[ProductImportRecord],
    *,
    s3_client: Any | None = None,
) -> StagedProductImport:
    """Persist validated import rows to configured S3 staging for the Dagster worker."""
    bucket = settings.product_import_s3_bucket
    if not bucket:
        raise ProductImportStorageError(
            "PRODUCT_IMPORT_S3_BUCKET is not configured"
        )

    object_key = f"{S3_UPLOAD_PREFIX}/{tenant_id}/{uuid.uuid4()}.json"
    payload = {
        "tenant_id": str(tenant_id),
        "records": [
            record.model_dump(mode="json")
            for record in records
        ],
    }
    client = s3_client or _build_s3_client()
    owns_client = s3_client is None
    try:
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:
        logger.exception("Could not stage product import for tenant_id=%s", tenant_id)
        raise ProductImportStorageError("Could not stage the product import file") from exc
    finally:
        if owns_client:
            client.close()
    return StagedProductImport(
        bucket=bucket,
        object_key=object_key,
        tenant_id=str(tenant_id),
        row_count=len(records),
    )


def delete_staged_import(bucket: str, object_key: str, *, s3_client: Any | None = None) -> None:
    """Delete a staged import that was not submitted to Dagster."""
    client = s3_client or _build_s3_client()
    owns_client = s3_client is None
    try:
        client.delete_object(Bucket=bucket, Key=object_key)
    finally:
        if owns_client:
            client.close()