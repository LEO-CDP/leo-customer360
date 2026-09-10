"""Hand-off to the email dispatch flow.

campaign_activation and email_engine are separate Dagster code locations, so
activation cannot compose email_engine's ops into its own job graph. It instead
submits a fresh ``email_engine_job`` run to the Dagster webserver's GraphQL API
(the same mechanism customer360-api's ``core/utils/dagster_client.py`` uses),
passing the campaign as run_config. The email send then runs out-of-process in
its own retry-tracked Dagster run.

Config via env: ``DAGSTER_GRAPHQL_HOST`` (default ``localhost``),
``DAGSTER_GRAPHQL_PORT`` (default ``3000``).
"""

import os
from typing import Callable

from dagster_graphql import DagsterGraphQLClient

DAGSTER_GRAPHQL_HOST = os.environ.get("DAGSTER_GRAPHQL_HOST", "localhost")
DAGSTER_GRAPHQL_PORT = int(os.environ.get("DAGSTER_GRAPHQL_PORT", "3000"))
EMAIL_ENGINE_JOB_NAME = os.environ.get("DAGSTER_EMAIL_ENGINE_JOB_NAME", "email_engine_job")
EMAIL_ENGINE_LOCATION_NAME = os.environ.get("DAGSTER_EMAIL_ENGINE_LOCATION_NAME", "email_engine")


class EmailEngineTriggerError(Exception):
    """Raised when the email_engine_job run could not be submitted."""


def trigger_email_engine_job(campaign_id: str, tenant_id: str, log: Callable[[str], None] = print) -> str:
    """Submit an ``email_engine_job`` run for one campaign; return its run_id."""
    run_config = {
        "ops": {"send_campaign_op": {"config": {"campaign_id": campaign_id, "tenant_id": tenant_id}}}
    }
    try:
        client = DagsterGraphQLClient(DAGSTER_GRAPHQL_HOST, port_number=DAGSTER_GRAPHQL_PORT)
        run_id = client.submit_job_execution(
            EMAIL_ENGINE_JOB_NAME,
            repository_location_name=EMAIL_ENGINE_LOCATION_NAME,
            run_config=run_config,
        )
    except Exception as exc:  # noqa: BLE001 - webserver unreachable / job not registered / submit rejected.
        raise EmailEngineTriggerError(
            f"could not submit {EMAIL_ENGINE_JOB_NAME} at "
            f"{DAGSTER_GRAPHQL_HOST}:{DAGSTER_GRAPHQL_PORT}: {exc}"
        ) from exc
    log(f"campaign_activation: submitted {EMAIL_ENGINE_JOB_NAME} run {run_id} for campaign {campaign_id}")
    return run_id
