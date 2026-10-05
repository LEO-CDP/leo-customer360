"""Dagster jobs for data synchronization and TSV catalog imports.

``product_content_import_job`` and ``content_item_import_job`` consume
tenant-scoped S3 staging objects submitted by customer360-api. Product imports
generate display text through customer360-agent before storing product metadata
and linked content items. Content imports validate and transactionally persist
content rows. ``data_synch_job`` remains a compatibility placeholder.

Run from `customer360-backend/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`segmentation`/`analytics`) in
the Dagster UI.
"""

import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import Config, Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("DATA_SYNCH_PLACEHOLDER_SLEEP_SECONDS", "2"))

from product_import import import_product_file  # noqa: E402
from content_import import import_content_file  # noqa: E402


class ProductContentImportConfig(Config):
    """Tenant-scoped staged S3 object submitted by customer360-api."""

    tenant_id: str
    bucket: str
    object_key: str


class ContentImportConfig(Config):
    """Tenant-scoped staged content object submitted by customer360-api."""

    tenant_id: str
    bucket: str
    object_key: str


@op
def data_synch_placeholder_op(context: OpExecutionContext) -> None:
    """Stand-in for the real data-sync op: logs started -> sleep -> done so
    the job is runnable/observable end-to-end before real logic exists."""
    context.log.info("data_synch job: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("data_synch job: done")


@op
def import_product_content_op(
    context: OpExecutionContext,
    config: ProductContentImportConfig,
) -> dict[str, Any]:
    """Enrich staged product fields with the agent service and upsert PostgreSQL rows."""
    context.log.info(
        "Product import started (run_id=%s, tenant_id=%s)",
        context.run_id,
        config.tenant_id,
    )
    result = import_product_file(
        tenant_id=config.tenant_id,
        bucket=config.bucket,
        object_key=config.object_key,
    )
    context.log.info(
        "Product import completed (tenant_id=%s, products_imported=%d, content_generation_skipped=%s)",
        result["tenant_id"],
        result["products_imported"],
        result["content_generation_skipped"],
    )
    return result


@op
def import_content_items_op(
    context: OpExecutionContext,
    config: ContentImportConfig,
) -> dict[str, Any]:
    """Validate and transactionally import staged content rows."""
    context.log.info(
        "Content import started (run_id=%s, tenant_id=%s)",
        context.run_id,
        config.tenant_id,
    )
    result = import_content_file(
        tenant_id=config.tenant_id,
        bucket=config.bucket,
        object_key=config.object_key,
    )
    context.log.info(
        "Content import completed (tenant_id=%s, content_items_imported=%d)",
        result["tenant_id"],
        result["content_items_imported"],
    )
    return result


@job(name="data_synch_job")
def data_synch_job() -> None:
    data_synch_placeholder_op()


@job(name="product_content_import_job", tags={"backend_job": "data_synch"})
def product_content_import_job() -> None:
    import_product_content_op()


@job(name="content_item_import_job", tags={"backend_job": "data_synch"})
def content_item_import_job() -> None:
    import_content_items_op()


defs = Definitions(
    jobs=[data_synch_job, product_content_import_job, content_item_import_job]
)
