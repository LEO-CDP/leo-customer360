"""Dagster job definitions for the Campaign Activation service
(backend-system/campaign_activation).

Real campaign orchestration (replaces the former sleep placeholder):
``campaign_activation_job`` runs ``activate_campaign`` (see
``campaign_activation/activation.py``) -- validate approval, snapshot the
segment, mark the campaign Running, then submit ``email_engine_job`` to do the
actual send.

Triggered with run_config carrying the campaign, e.g. by customer360-api's
``POST /api/v1/admin/campaigns/{id}/activate`` via its
``CampaignActivationDagsterService`` (core/utils/dagster_client.py):

    ops:
      activate_campaign_op:
        config:
          campaign_id: "<uuid>"
          tenant_id: "<uuid>"

Run from `backend-system/`: `dagster dev -w workspace.yaml`.
"""

import os
import sys

# `python_file` workspace loader doesn't add this file's dir to sys.path, so
# `import campaign_activation` (the nested package) fails without this. Same
# gotcha as ../segmentation/dagster_defs.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    Config,
    Definitions,
    OpExecutionContext,
    RetryPolicy,
    job,
    op,
)

from campaign_activation.activation import activate_campaign  # noqa: E402


class ActivateCampaignConfig(Config):
    """Op config identifying the campaign to activate. Both required -- an
    activation is always scoped to one Approved campaign within one tenant."""

    campaign_id: str
    tenant_id: str


@op(retry_policy=RetryPolicy(max_retries=2, delay=10))
def activate_campaign_op(context: OpExecutionContext, config: ActivateCampaignConfig) -> dict:
    """Validate + snapshot an Approved campaign, mark it Running, then submit the
    email_engine run that dispatches it."""
    context.log.info(
        "campaign_activation job: started (campaign_id=%s, tenant_id=%s)",
        config.campaign_id, config.tenant_id,
    )
    summary = activate_campaign(config.campaign_id, config.tenant_id, log=context.log.info)
    context.log.info("campaign_activation job: done (%s)", summary)
    return summary


@job(name="campaign_activation_job")
def campaign_activation_job() -> None:
    activate_campaign_op()


defs = Definitions(jobs=[campaign_activation_job])
