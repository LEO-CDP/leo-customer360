"""Dagster master task for API-triggered and cron-driven AI-agent workflows."""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dagster import Config, Definitions, OpExecutionContext, RunRequest, job, op, sensor
from pydantic import Field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runner import AgentWorkflowMasterTask  # noqa: E402
from agent_pipeline import execute_agent_pipeline  # noqa: E402

WORKFLOW_POLL_SECONDS = int(os.environ.get("AI_AGENTS_WORKFLOW_POLL_SECONDS", "60"))
PERSONALIZATION_PLACEHOLDER_SLEEP_SECONDS = int(
    os.environ.get("PERSONALIZATION_PLACEHOLDER_SLEEP_SECONDS", "2")
)


class AgentWorkflowRunConfig(Config):
    """Run configuration accepted by the API trigger and cron sensor."""

    trigger: str = "api"
    tenant_id: Optional[str] = None
    segment_id: Optional[str] = None
    trigger_event: dict = Field(default_factory=dict)
    scheduled_at: Optional[str] = None


class AgentPipelineRunConfig(Config):
    """Validated pipeline input supplied as a JSON payload in Dagster run config."""

    payload: dict[str, Any]


@op
def run_agent_workflow_master_op(
    context: OpExecutionContext,
    config: AgentWorkflowRunConfig,
) -> dict:
    """Selects active steps in order without fabricating agent outputs."""
    scheduled_at = (
        datetime.fromisoformat(config.scheduled_at.replace("Z", "+00:00"))
        if config.scheduled_at
        else None
    )
    summary = AgentWorkflowMasterTask().run(
        trigger=config.trigger,
        tenant_id=config.tenant_id,
        segment_id=config.segment_id,
        trigger_event=config.trigger_event,
        scheduled_at=scheduled_at,
    )
    context.log.info("AI-agent workflow master task result: %s", summary.as_dict())
    return summary.as_dict()


@op
def run_agent_type_pipeline_op(
    context: OpExecutionContext,
    config: AgentPipelineRunConfig,
) -> dict[str, Any]:
    """Run one validated agent-type pipeline and return its output envelope."""
    output = execute_agent_pipeline(config.payload, run_id=context.run_id)
    context.log.info(
        "Agent pipeline completed (model_type=%s, agent_code=%s, tenant_id=%s)",
        output.model_type,
        output.agent_code,
        output.tenant_id,
    )
    return output.model_dump(mode="json")


@job(name="ai_agents_master_job", tags={"backend_job": "ai_agents_runners"})
def ai_agents_master_job() -> None:
    run_agent_workflow_master_op()


@job(name="agent_type_pipeline_job", tags={"backend_job": "ai_agents_runners"})
def agent_type_pipeline_job() -> None:
    run_agent_type_pipeline_op()


@op
def personalization_placeholder_op(context: OpExecutionContext) -> None:
    """Compatibility scaffold for future recommendation handlers."""
    context.log.info("personalization runner: started")
    time.sleep(PERSONALIZATION_PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("personalization runner: done")


@job(name="personalization_job", tags={"backend_job": "ai_agents_runners"})
def personalization_job() -> None:
    personalization_placeholder_op()


@sensor(
    job=ai_agents_master_job,
    minimum_interval_seconds=WORKFLOW_POLL_SECONDS,
)
def ai_agent_workflow_schedule_sensor(context):
    """Polls workflow cron definitions and submits one bounded master run."""
    now = datetime.now(timezone.utc)
    return RunRequest(
        run_key=f"ai-agent-workflow-{now.strftime('%Y%m%d%H%M')}",
        run_config={
            "ops": {
                "run_agent_workflow_master_op": {
                    "config": {
                        "trigger": "cron",
                        "scheduled_at": now.isoformat(),
                    }
                }
            }
        },
    )


defs = Definitions(
    jobs=[ai_agents_master_job, agent_type_pipeline_job, personalization_job],
    sensors=[ai_agent_workflow_schedule_sensor],
)
