"""Dagster definitions for the Notification Engine service
(backend-system/notification_engine).

Hosts the Zalo OA token-refresh job + the repo's FIRST ``ScheduleDefinition``,
which keeps each tenant's ``zalo-oa`` OAuth token fresh in
``sys_data_source.access_tokens``. The Zalo ZNS campaign-send job
(``send_zalo_campaign``) is added to this code location in Phase 2.

Run from `backend-system/`: `dagster dev -w workspace.yaml`.
"""

import os
import sys

# Dagster's `python_file` workspace loader does NOT add this file's own dir to
# sys.path, so `import notification_engine` (the nested package) fails without
# this. Same gotcha as email_engine/segmentation dagster_defs.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    Config,
    Definitions,
    OpExecutionContext,
    RetryPolicy,
    ScheduleDefinition,
    job,
    op,
)

from notification_engine.config import OPTOUT_PROJECTION_CRON, TOKEN_REFRESH_CRON  # noqa: E402
from notification_engine.db import connect  # noqa: E402
from notification_engine.optout_projection import project_optout_events, read_optout_events  # noqa: E402
from notification_engine.send import send_zalo_campaign  # noqa: E402
from notification_engine.token_refresh import refresh_due_tokens  # noqa: E402


@op(retry_policy=RetryPolicy(max_retries=2, delay=15))
def refresh_zalo_tokens_op(context: OpExecutionContext) -> dict:
    """Refresh near-expiry Zalo OA tokens across all tenants."""
    conn = connect()
    try:
        summary = refresh_due_tokens(conn, log=context.log.info)
    finally:
        conn.close()
    context.log.info("zalo token refresh: %s", summary)
    return summary


@job(name="zalo_token_refresh_job")
def zalo_token_refresh_job() -> None:
    refresh_zalo_tokens_op()


zalo_token_refresh_schedule = ScheduleDefinition(
    name="zalo_token_refresh_schedule",
    job=zalo_token_refresh_job,
    cron_schedule=TOKEN_REFRESH_CRON,
)


class SendZaloCampaignConfig(Config):
    """Op config identifying the zalo_zns campaign to send (one Approved campaign
    within one tenant)."""

    campaign_id: str
    tenant_id: str


@op(retry_policy=RetryPolicy(max_retries=2, delay=15))
def send_zalo_campaign_op(context: OpExecutionContext, config: SendZaloCampaignConfig) -> dict:
    """Render + dispatch one Approved zalo_zns campaign to its segment members.
    Idempotent (dispatch ledger keyed on (campaign_id, master_profile_id))."""
    context.log.info(
        "notification_engine job: started (campaign_id=%s, tenant_id=%s)",
        config.campaign_id, config.tenant_id,
    )
    summary = send_zalo_campaign(
        config.campaign_id, config.tenant_id, run_id=context.run_id, log=context.log.info
    )
    context.log.info("notification_engine job: done (%s)", summary)
    return summary


# Name matches customer360-api's dagster_notification_engine_job_name default
# ("notification_engine_job") so NotificationEngineDagsterService.dispatch() finds it.
@job(name="notification_engine_job")
def notification_engine_job() -> None:
    send_zalo_campaign_op()


@op(retry_policy=RetryPolicy(max_retries=2, delay=15))
def project_zalo_optouts_op(context: OpExecutionContext) -> dict:
    """S3-first opt-out projection: apply new zalo-opt-out events from S3 onto
    cdp_master_profiles.communication_preferences (the only place consent is written)."""
    conn = connect()
    try:
        events = read_optout_events(conn)
        summary = project_optout_events(conn, events)
    finally:
        conn.close()
    context.log.info("zalo opt-out projection: %s", summary)
    return summary


@job(name="zalo_optout_projection_job")
def zalo_optout_projection_job() -> None:
    project_zalo_optouts_op()


zalo_optout_projection_schedule = ScheduleDefinition(
    name="zalo_optout_projection_schedule",
    job=zalo_optout_projection_job,
    cron_schedule=OPTOUT_PROJECTION_CRON,
)


defs = Definitions(
    jobs=[zalo_token_refresh_job, notification_engine_job, zalo_optout_projection_job],
    schedules=[zalo_token_refresh_schedule, zalo_optout_projection_schedule],
)
