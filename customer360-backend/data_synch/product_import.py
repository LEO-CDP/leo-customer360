"""Async TSV product import orchestration, LLM enrichment, and tenant-safe persistence."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Mapping

import boto3
import psycopg2
from botocore.client import Config
from psycopg2.extras import Json, execute_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from leo_customer360_dao.config import settings
from leo_customer360_dao.schemas.product_import import ProductImportRecord

logger = logging.getLogger(__name__)

DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
MAX_IMPORT_OBJECT_BYTES = 15 * 1024 * 1024
MAX_IMPORT_ROWS = 5_000
LLM_BATCH_SIZE = 20
LLM_REQUEST_TIMEOUT_SECONDS = 180
CONTENT_IMPORT_PREFIX = "product-imports"

PRODUCT_UPSERT_SQL = f"""
    INSERT INTO {DB_SCHEMA}.cdp_product_items AS existing_product (
        product_item_id, tenant_id, content_item_id, domain, product_type,
        source_id, source_type, product_id_type, product_id, keywords, ext_attributes,
        original_price, sale_price, currency, source_fields
    ) VALUES %s
    ON CONFLICT (tenant_id, source_type, source_id, product_id_type, product_id)
    DO UPDATE SET
        content_item_id = COALESCE(
            existing_product.content_item_id,
            EXCLUDED.content_item_id
        ),
        domain = EXCLUDED.domain,
        product_type = EXCLUDED.product_type,
        keywords = EXCLUDED.keywords,
        ext_attributes = EXCLUDED.ext_attributes,
        original_price = EXCLUDED.original_price,
        sale_price = EXCLUDED.sale_price,
        currency = EXCLUDED.currency,
        source_fields = EXCLUDED.source_fields,
        updated_at = now()
    RETURNING source_type, source_id, product_id_type, product_id, content_item_id
"""

CONTENT_UPSERT_SQL = f"""
    INSERT INTO {DB_SCHEMA}.cdp_content_items (
        content_item_id, tenant_id, domain, item_type, title, summary,
        image_url, cta_label, cta_url, segment_tags, status_code
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
        updated_at = now()
"""


class ProductContentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_key: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=4000)

    @field_validator("title", "summary")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("generated text must not be blank")
        return cleaned


class ProductContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[ProductContentResult] = Field(min_length=1, max_length=LLM_BATCH_SIZE)


class ProductImportError(RuntimeError):
    """Raised when staged product data cannot be enriched or persisted."""


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


def _load_records(
    *,
    bucket: str,
    object_key: str,
    tenant_id: uuid.UUID,
    s3_client: Any,
) -> list[ProductImportRecord]:
    if bucket != settings.product_import_s3_bucket:
        raise ProductImportError("Import bucket does not match configured product-import storage")
    expected_prefix = f"{CONTENT_IMPORT_PREFIX}/{tenant_id}/"
    if not object_key.startswith(expected_prefix):
        raise ProductImportError("Import object key is outside the requested tenant prefix")

    try:
        response = s3_client.get_object(Bucket=bucket, Key=object_key)
        body = response["Body"]
        try:
            raw_data = body.read(MAX_IMPORT_OBJECT_BYTES + 1)
        finally:
            body.close()
    except Exception as exc:
        logger.exception("Could not read staged product import for tenant_id=%s", tenant_id)
        raise ProductImportError("Could not read staged product import") from exc
    if len(raw_data) > MAX_IMPORT_OBJECT_BYTES:
        raise ProductImportError("Staged product import exceeds the configured size limit")

    try:
        payload = json.loads(raw_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductImportError("Staged product import is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("tenant_id") != str(tenant_id):
        raise ProductImportError("Staged product import tenant does not match the Dagster run")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not 1 <= len(raw_records) <= MAX_IMPORT_ROWS:
        raise ProductImportError("Staged product import contains an invalid row count")
    try:
        records = [ProductImportRecord.model_validate(record) for record in raw_records]
    except ValidationError as exc:
        raise ProductImportError(f"Staged product import contains invalid rows: {exc}") from exc
    product_keys = [record.product_key for record in records]
    if len(product_keys) != len(set(product_keys)):
        raise ProductImportError("Staged product import contains duplicate product identities")
    return records


def _generate_content_batch(
    records: list[ProductImportRecord],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, ProductContentResult]:
    url = settings.agent_service_url.rstrip("/") + "/products/generate-content"
    headers = {"Content-Type": "application/json"}
    if settings.agent_api_token:
        headers["Authorization"] = f"Bearer {settings.agent_api_token}"
    request_payload = {
        "products": [
            {
                "product_key": record.product_key,
                "source_fields": record.source_fields,
            }
            for record in records
        ]
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener(request, timeout=LLM_REQUEST_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
        except (ValueError, TypeError, AttributeError):
            detail = str(exc)
        raise ProductImportError(f"Product content generation failed: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductImportError(f"Product content generation request failed: {exc}") from exc

    try:
        result = ProductContentResponse.model_validate(response_data)
    except ValidationError as exc:
        raise ProductImportError(f"Product content service returned invalid data: {exc}") from exc
    returned_keys = [product.product_key for product in result.products]
    expected_keys = [record.product_key for record in records]
    if len(returned_keys) != len(set(returned_keys)) or set(returned_keys) != set(expected_keys):
        raise ProductImportError(
            "Product content service did not return each requested product exactly once"
        )
    return {product.product_key: product for product in result.products}


def _agent_llm_is_configured(
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> bool:
    """Read non-secret LLM readiness from customer360-agent before enrichment."""
    request = urllib.request.Request(
        settings.agent_service_url.rstrip("/") + "/health",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with opener(request, timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProductImportError(f"Could not check AI-agent readiness: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductImportError(f"Could not check AI-agent readiness: {exc}") from exc
    if not isinstance(health, dict) or not isinstance(health.get("llm_configured"), bool):
        raise ProductImportError("AI-agent health response is missing llm_configured readiness")
    return health["llm_configured"]


def generate_content_for_records(
    records: list[ProductImportRecord],
    *,
    generate_batch: Callable[
        [list[ProductImportRecord]], Mapping[str, ProductContentResult]
    ] = _generate_content_batch,
) -> dict[str, ProductContentResult]:
    """Enrich every product in bounded LLM batches before starting database writes."""
    generated: dict[str, ProductContentResult] = {}
    for start in range(0, len(records), LLM_BATCH_SIZE):
        batch = records[start : start + LLM_BATCH_SIZE]
        batch_results = generate_batch(batch)
        expected_keys = {record.product_key for record in batch}
        if set(batch_results) != expected_keys:
            raise ProductImportError(
                "Product content generator must return one result per input product"
            )
        generated.update(batch_results)
    return generated


def persist_product_records(
    tenant_id: uuid.UUID,
    records: list[ProductImportRecord],
    generated: Mapping[str, ProductContentResult] | None,
    *,
    connection_factory: Callable[[], Any] = _connect,
) -> int:
    """Persist source products and, when available, their generated content atomically.

    The function sets the PostgreSQL tenant context, verifies every product
    domain is active, and upserts ``cdp_product_items`` by
    ``(tenant_id, source_type, source_id, product_id_type, product_id)``.

    When ``generated`` contains a validated result for every source product,
    the same transaction also upserts the linked ``cdp_content_items`` rows.
    When ``generated`` is ``None`` because the agent has no configured LLM
    credential or base URL, it writes only the product records, stores
    ``ext_attributes.next_actions = "generate_content_item"``, leaves new
    ``content_item_id`` values null, and returns before issuing the content
    upsert. Existing content links on re-import are preserved but not changed.

    Args:
        tenant_id: Tenant whose rows are protected by PostgreSQL RLS.
        records: Validated product rows parsed from the staged TSV.
        generated: Complete product-key-to-generated-content mapping, or
            ``None`` to defer content generation without failing the import.
        connection_factory: Injectable PostgreSQL connection factory for tests.

    Returns:
        Number of product source rows persisted.

    Raises:
        ProductImportError: If the generated-result set or product domains are
            invalid, or the database upsert does not return every product key.
    """
    if generated is not None and set(generated) != {record.product_key for record in records}:
        raise ProductImportError("Generated content does not match the supplied product rows")

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
                raise ProductImportError(
                    "Inactive or unknown product domain(s): " + ", ".join(invalid_domains)
                )

            proposed_content_ids = {
                record.product_key: uuid.uuid4().hex if generated is not None else None
                for record in records
            }
            product_rows = [
                (
                    uuid.uuid4().hex,
                    str(tenant_id),
                    proposed_content_ids[record.product_key],
                    record.domain,
                    record.product_type,
                    record.source_id,
                    record.source_type,
                    record.product_id_type,
                    record.product_id,
                    record.keywords,
                    Json(
                        {
                            **record.ext_attributes,
                            **(
                                {"next_actions": "generate_content_item"}
                                if generated is None
                                else {}
                            ),
                        }
                    ),
                    record.original_price,
                    record.sale_price,
                    record.currency,
                    Json(record.source_fields),
                )
                for record in records
            ]
            returned_products = execute_values(
                cursor,
                PRODUCT_UPSERT_SQL,
                product_rows,
                template=(
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                ),
                page_size=500,
                fetch=True,
            )
            if not returned_products:
                raise ProductImportError("Product upsert did not return linked content IDs")
            content_ids_by_key = {
                (row[0], row[1], row[2], row[3]): row[4] for row in returned_products
            }
            expected_identity_keys = {
                (
                    record.source_type,
                    record.source_id,
                    record.product_id_type,
                    record.product_id,
                )
                for record in records
            }
            if set(content_ids_by_key) != expected_identity_keys:
                raise ProductImportError("Product upsert returned an incomplete identity mapping")

            if generated is None:
                return len(records)

            content_rows = []
            for record in records:
                identity = (
                    record.source_type,
                    record.source_id,
                    record.product_id_type,
                    record.product_id,
                )
                content_item = record.to_content_item(
                    tenant_id=tenant_id,
                    content_item_id=content_ids_by_key[identity],
                    title=generated[record.product_key].title,
                    summary=generated[record.product_key].summary,
                )
                content_rows.append(
                    (
                        str(content_item["content_item_id"]),
                        str(content_item["tenant_id"]),
                        content_item["domain"],
                        content_item["item_type"],
                        content_item["title"],
                        content_item["summary"],
                        content_item["image_url"],
                        content_item["cta_label"],
                        content_item["cta_url"],
                        content_item["segment_tags"],
                        1,
                    )
                )
            execute_values(
                cursor,
                CONTENT_UPSERT_SQL,
                content_rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                page_size=500,
            )
    return len(records)


def import_product_file(
    *,
    tenant_id: str,
    bucket: str,
    object_key: str,
    s3_client: Any | None = None,
    connection_factory: Callable[[], Any] = _connect,
    generate_batch: Callable[
        [list[ProductImportRecord]], Mapping[str, ProductContentResult]
    ] = _generate_content_batch,
    llm_configured: Callable[[], bool] = _agent_llm_is_configured,
) -> dict[str, Any]:
    """Load, enrich, and persist a staged tenant product import."""
    parsed_tenant_id = uuid.UUID(tenant_id)
    staged_s3_client = s3_client or _build_s3_client()
    owns_s3_client = s3_client is None
    try:
        records = _load_records(
            bucket=bucket,
            object_key=object_key,
            tenant_id=parsed_tenant_id,
            s3_client=staged_s3_client,
        )
        if llm_configured():
            generated = generate_content_for_records(records, generate_batch=generate_batch)
        else:
            generated = None
            logger.info(
                "Product content generation skipped because no LLM API key or base URL is configured "
                "(tenant_id=%s); products will be queued for content generation",
                parsed_tenant_id,
            )
        imported = persist_product_records(
            parsed_tenant_id,
            records,
            generated,
            connection_factory=connection_factory,
        )
        try:
            staged_s3_client.delete_object(Bucket=bucket, Key=object_key)
        except Exception:
            logger.exception(
                "Product import completed but staged object cleanup failed (tenant_id=%s)",
                parsed_tenant_id,
            )
        logger.info(
            "Imported %d product row(s) for tenant_id=%s",
            imported,
            parsed_tenant_id,
        )
        return {
            "tenant_id": str(parsed_tenant_id),
            "products_imported": imported,
            "content_generation_skipped": generated is None,
        }
    finally:
        if owns_s3_client:
            staged_s3_client.close()
