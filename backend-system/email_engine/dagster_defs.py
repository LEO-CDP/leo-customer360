"""Dagster job definitions for the Email Engine service
(backend-system/email_engine).

Real outbound-email send pipeline (replaces the former sleep placeholder):
``email_engine_job`` runs ``send_campaign`` (see ``email_engine/send.py``) for
one Approved campaign, rendering + dispatching to the campaign segment's members
and writing an idempotent ``cdp_campaign_dispatch_logs`` ledger.

Triggered with run_config carrying the target campaign, e.g. by
``campaign_activation_job`` after it validates approval, or directly by
customer360-api's ``EmailEngineDagsterService`` (core/utils/dagster_client.py):

    ops:
      send_campaign_op:
        config:
          campaign_id: "<uuid>"
          tenant_id: "<uuid>"

Run from `backend-system/`: `dagster dev -w workspace.yaml`.
"""

import os
import sys

# Dagster's `python_file` workspace loader does NOT add this file's own
# directory to sys.path, so `import email_engine` (the nested package next to
# this file) fails under `dagster dev -w workspace.yaml` without this. Same
# gotcha documented in ../segmentation/dagster_defs.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    Config,
    Definitions,
    OpExecutionContext,
    RetryPolicy,
    job,
    op,
)

from email_engine.send import send_campaign  # noqa: E402


class SendCampaignConfig(Config):
    """Op config identifying the campaign to send. Both are required -- there is
    no ALL-campaigns default (a send is always scoped to one Approved campaign
    within one tenant)."""

    campaign_id: str
    tenant_id: str


@op(retry_policy=RetryPolicy(max_retries=2, delay=15))
def send_campaign_op(context: OpExecutionContext, config: SendCampaignConfig) -> dict:
    """Render + dispatch one Approved campaign to its segment members. Idempotent
    (dispatch ledger keyed on (campaign_id, master_profile_id)), so Dagster
    retries never double-send an already-'Sent' recipient."""
    context.log.info(
        "email_engine job: started (campaign_id=%s, tenant_id=%s)",
        config.campaign_id, config.tenant_id,
    )
    summary = send_campaign(
        config.campaign_id,
        config.tenant_id,
        run_id=context.run_id,
        log=context.log.info,
    )
    context.log.info("email_engine job: done (%s)", summary)
    return summary


@job(name="email_engine_job")
def email_engine_job() -> None:
    send_campaign_op()


defs = Definitions(jobs=[email_engine_job])
