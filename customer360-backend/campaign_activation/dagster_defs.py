"""Dagster job definitions for the Campaign Activation service
(customer360-backend/campaign_activation).

Real campaign orchestration (replaces the former sleep placeholder):
``campaign_activation_job`` runs ``activate_campaign`` (see
``campaign_activation/activation.py``) -- validate approval and the selected
workflow, mark the campaign Running, then submit the persisted
per-profile ``campaign_workflow_job``.

``campaign_workflow_job`` runs the exact saved graph for each matched profile,
including branch conditions, waits, email/SMS/push content, and profile tags.

Triggered with run_config carrying the campaign, e.g. by customer360-api's
``POST /api/v1/admin/campaigns/{id}/activate`` via its
``CampaignActivationDagsterService`` (core/utils/dagster_client.py):

    ops:
      activate_campaign_op:
        config:
          campaign_id: "<uuid>"
          tenant_id: "<uuid>"

Run from `customer360-backend/`: `dagster dev -w workspace.yaml`.
"""

import os
import sys
import logging

# `python_file` workspace loader doesn't add this file's dir to sys.path, so
# `import campaign_activation` (the nested package) fails without this. Same
# gotcha as ../segmentation/dagster_defs.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    Config,
    Definitions,
    MetadataValue,
    OpExecutionContext,
    Output,
    RetryPolicy,
    job,
    op,
)

from campaign_activation.activation import activate_campaign  # noqa: E402
from campaign_activation import version_log_message  # noqa: E402
from campaign_activation.workflow_runner import run_campaign_workflow  # noqa: E402

logger = logging.getLogger(__name__)
logger.info("%s code_location_loaded=true", version_log_message())


class ActivateCampaignConfig(Config):
    """Tenant-scoped approved campaign activation, with opt-in completed replay."""

    campaign_id: str
    tenant_id: str
    restart: bool = False
    expected_execution_id: str | None = None


class CampaignWorkflowConfig(Config):
    """Tenant-scoped persisted workflow to execute per profile."""

    tenant_id: str
    campaign_id: str
    execution_id: str | None = None


@op(retry_policy=RetryPolicy(max_retries=2, delay=10))
def activate_campaign_op(context: OpExecutionContext, config: ActivateCampaignConfig) -> Output[dict]:
    """Validate an approved workflow campaign, mark it Running, and submit
    the persisted per-profile workflow."""
    context.log.info(
        "%s job=campaign_activation_job started campaign_id=%s tenant_id=%s restart=%s",
        version_log_message(),
        config.campaign_id,
        config.tenant_id,
        config.restart,
    )
    summary = activate_campaign(
        config.campaign_id,
        config.tenant_id,
        restart=config.restart,
        expected_execution_id=config.expected_execution_id,
        log=context.log.info,
    )
    context.log.info("campaign_activation job: done (%s)", summary)
    return Output(
        summary,
        metadata={
            "campaign_id": MetadataValue.text(str(summary.get("campaign_id") or config.campaign_id)),
            "tenant_id": MetadataValue.text(str(summary.get("tenant_id") or config.tenant_id)),
            "status": MetadataValue.text(str(summary.get("status") or "Running")),
            "execution_id": MetadataValue.text(str(summary.get("execution_id") or "")),
            "workflow_run_id": MetadataValue.text(str(summary.get("workflow_run_id") or "")),
            "restart": MetadataValue.bool(bool(config.restart)),
        },
    )


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def run_campaign_workflow_op(
    context: OpExecutionContext,
    config: CampaignWorkflowConfig,
) -> Output[dict]:
    """Execute email/SMS/push/tag actions from the persisted workflow graph."""
    context.log.info(
        "%s job=campaign_workflow_job started run_id=%s tenant_id=%s campaign_id=%s execution_id=%s",
        version_log_message(),
        context.run_id,
        config.tenant_id,
        config.campaign_id,
        config.execution_id,
    )
    summary = run_campaign_workflow(
        tenant_id=config.tenant_id,
        campaign_id=config.campaign_id,
        run_id=context.run_id,
        execution_id=config.execution_id,
    )
    context.log.info("campaign workflow completed (%s)", summary)
    return Output(
        summary,
        metadata={
            "campaign_id": MetadataValue.text(str(summary.get("campaign_id") or config.campaign_id)),
            "tenant_id": MetadataValue.text(str(summary.get("tenant_id") or config.tenant_id)),
            "status": MetadataValue.text(str(summary.get("status") or "Completed")),
            "execution_id": MetadataValue.text(str(summary.get("execution_id") or config.execution_id or "")),
            "profiles": MetadataValue.int(int(summary.get("profiles") or 0)),
            "sent": MetadataValue.int(int(summary.get("sent") or 0)),
            "skipped": MetadataValue.int(int(summary.get("skipped") or 0)),
            "applied": MetadataValue.int(int(summary.get("applied") or 0)),
            "errors": MetadataValue.int(int(summary.get("errors") or 0)),
            "duration_seconds": MetadataValue.float(float(summary.get("duration_seconds") or 0.0)),
            "end_counts": MetadataValue.json(summary.get("end_counts") or {}),
            "node_counts": MetadataValue.json(summary.get("node_counts") or {}),
            "data_sync_run_ids": MetadataValue.json(summary.get("data_sync_run_ids") or []),
        },
    )


@job(name="campaign_activation_job")
def campaign_activation_job() -> None:
    activate_campaign_op()


@job(name="campaign_workflow_job", tags={"backend_job": "campaign_workflow"})
def campaign_workflow_job() -> None:
    run_campaign_workflow_op()


defs = Definitions(jobs=[campaign_activation_job, campaign_workflow_job])
