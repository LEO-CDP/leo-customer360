"""Dagster submission for the persisted campaign workflow."""

import os
from typing import Callable

from dagster_graphql import DagsterGraphQLClient

DAGSTER_GRAPHQL_HOST = os.environ.get("DAGSTER_GRAPHQL_HOST", "localhost")
DAGSTER_GRAPHQL_PORT = int(os.environ.get("DAGSTER_GRAPHQL_PORT", "3000"))
CAMPAIGN_WORKFLOW_JOB_NAME = os.environ.get(
    "DAGSTER_CAMPAIGN_WORKFLOW_JOB_NAME", "campaign_workflow_job"
)
CAMPAIGN_WORKFLOW_LOCATION_NAME = os.environ.get(
    "DAGSTER_CAMPAIGN_WORKFLOW_LOCATION_NAME", "campaign_activation"
)


class CampaignWorkflowTriggerError(Exception):
    """Raised when the persisted campaign workflow cannot be submitted."""


def trigger_campaign_workflow_job(
    campaign_id: str,
    tenant_id: str,
    log: Callable[[str], None] = print,
) -> str:
    """Submit the per-profile workflow execution job."""
    try:
        client = DagsterGraphQLClient(DAGSTER_GRAPHQL_HOST, port_number=DAGSTER_GRAPHQL_PORT)
        run_id = client.submit_job_execution(
            CAMPAIGN_WORKFLOW_JOB_NAME,
            repository_location_name=CAMPAIGN_WORKFLOW_LOCATION_NAME,
            run_config={
                "ops": {
                    "run_campaign_workflow_op": {
                        "config": {
                            "tenant_id": tenant_id,
                            "campaign_id": campaign_id,
                        }
                    }
                }
            },
            tags={"tenant_id": tenant_id, "campaign_id": campaign_id, "workflow_run": "true"},
        )
    except Exception as exc:  # noqa: BLE001 - submission failures become activation failures.
        raise CampaignWorkflowTriggerError(
            f"could not submit {CAMPAIGN_WORKFLOW_JOB_NAME} at "
            f"{DAGSTER_GRAPHQL_HOST}:{DAGSTER_GRAPHQL_PORT}: {exc}"
        ) from exc
    log(f"campaign_activation: submitted {CAMPAIGN_WORKFLOW_JOB_NAME} run {run_id}")
    return run_id
