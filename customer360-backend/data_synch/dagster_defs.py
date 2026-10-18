"""Dagster jobs for campaign audience synchronization and TSV catalog imports.

``data_synch_job`` exports a campaign's eligible segment members to one
configured Meta, Google Ads, or TikTok custom audience. The export is
tenant-scoped, consent-aware, hashed before transport, and idempotently
audited in ``crm_ad_sync_runs``. The catalog imports remain separate jobs.

Run from `customer360-backend/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`segmentation`/`analytics`) in
the Dagster UI.
"""

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import Config, Definitions, OpExecutionContext, RetryPolicy, job, op

from audience_sync import run_data_synch_job  # noqa: E402
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


class DataSynchConfig(Config):
    """Approved campaign audience export submitted by customer360-api."""

    tenant_id: str
    campaign_id: str
    platform: str
    segment_id: str | None = None
    audience_name: str | None = None
    dry_run: bool = False
    batch_size: int = 1000


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def run_data_synch_op(
    context: OpExecutionContext,
    config: DataSynchConfig,
) -> dict[str, Any]:
    """Export the campaign's eligible matched profiles to a custom audience."""
    context.log.info(
        "data_synch job started (run_id=%s, tenant_id=%s, campaign_id=%s, platform=%s, dry_run=%s)",
        context.run_id,
        config.tenant_id,
        config.campaign_id,
        config.platform,
        config.dry_run,
    )
    result = run_data_synch_job(
        tenant_id=config.tenant_id,
        campaign_id=config.campaign_id,
        platform=config.platform,
        segment_id=config.segment_id,
        audience_name=config.audience_name,
        dry_run=config.dry_run,
        batch_size=config.batch_size,
    )
    context.log.info(
        "data_synch job completed (sync_run_id=%s, eligible=%d, uploaded=%d)",
        result["sync_run_id"],
        result["eligible_count"],
        result["uploaded_count"],
    )
    return result


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


@job(name="data_synch_job", tags={"backend_job": "data_synch"})
def data_synch_job() -> None:
    run_data_synch_op()


@job(name="product_content_import_job", tags={"backend_job": "data_synch"})
def product_content_import_job() -> None:
    import_product_content_op()


@job(name="content_item_import_job", tags={"backend_job": "data_synch"})
def content_item_import_job() -> None:
    import_content_items_op()


defs = Definitions(
    jobs=[data_synch_job, product_content_import_job, content_item_import_job]
)
