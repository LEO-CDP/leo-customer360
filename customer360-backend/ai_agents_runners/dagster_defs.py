"""Dagster master task for API-triggered and cron-driven AI-agent workflows."""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dagster import (
    Config,
    Definitions,
    Failure,
    OpExecutionContext,
    RunRequest,
    ScheduleDefinition,
    job,
    op,
    sensor,
)
from pydantic import Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agents_runners.agent_pipeline import execute_agent_pipeline  # noqa: E402
from ai_agents_runners.clv.service import ClvService  # noqa: E402
from ai_agents_runners.runner import AgentWorkflowMasterTask  # noqa: E402

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
    """Select and execute supported ranking steps for the configured segment."""
    scheduled_at = (
        datetime.fromisoformat(config.scheduled_at.replace("Z", "+00:00"))
        if config.scheduled_at
        else None
    )
    task = AgentWorkflowMasterTask()
    context.log.info(
        "Starting AI-agent workflow master op "
        "(run_id=%s trigger=%s tenant_id=%s segment_id=%s)",
        context.run_id,
        config.trigger,
        config.tenant_id or "ALL",
        config.segment_id or "ALL",
    )
    try:
        summary = task.run(
            trigger=config.trigger,
            tenant_id=config.tenant_id,
            segment_id=config.segment_id,
            trigger_event=config.trigger_event,
            scheduled_at=scheduled_at,
        )
        summary = task.execute(summary, run_id=context.run_id)
    except Exception:
        context.log.exception(
            "AI-agent workflow master op failed "
            "(run_id=%s trigger=%s tenant_id=%s segment_id=%s)",
            context.run_id,
            config.trigger,
            config.tenant_id or "ALL",
            config.segment_id or "ALL",
        )
        raise
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
def ai_agent_workflow_schedule_sensor(_context):
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


class ClvTenantConfig(Config):
    """Optional tenant scope for CLV jobs; empty means every active tenant."""

    tenant_id: Optional[str] = None


class ClvRescoreConfig(Config):
    tenant_id: str
    master_profile_ids: list[str]


class ClvApprovalConfig(Config):
    tenant_id: str
    model_version_id: str
    approved_by: str


@op
def clv_nightly_scoring_op(context: OpExecutionContext, config: ClvTenantConfig) -> dict:
    """Score, rank, tier and project every (tenant, domain) with a champion."""
    summaries = ClvService().run_nightly(config.tenant_id)
    failed = [summary for summary in summaries if summary.status == "failed"]
    context.log.info(
        "CLV nightly scoring: %d pair(s), %d failed", len(summaries), len(failed)
    )
    if failed:
        raise Failure(
            description="CLV scoring failed for "
            + ", ".join(f"{summary.tenant_id}/{summary.domain}" for summary in failed)
        )
    return {"pairs": [summary.__dict__ for summary in summaries]}


@op
def clv_training_op(context: OpExecutionContext, config: ClvTenantConfig) -> dict:
    """Retrain each (tenant, domain) and apply the promotion gate."""
    results = ClvService().run_training(config.tenant_id)
    for result in results:
        context.log.info("CLV training result: %s", result)
    return {"results": results}


@op
def clv_rescore_op(context: OpExecutionContext, config: ClvRescoreConfig) -> dict:
    """Re-score profiles after a purchase against the frozen tier cut-offs."""
    scored = ClvService().score_profiles(config.tenant_id, config.master_profile_ids)
    context.log.info("CLV re-scored %d profile(s)", len(scored))
    return {profile_id: result.as_result() for profile_id, result in scored.items()}


@op
def clv_approve_model_op(context: OpExecutionContext, config: ClvApprovalConfig) -> dict:
    """Human approval: promote a PENDING_APPROVAL model and re-project its tiers."""
    result = ClvService().approve(config.tenant_id, config.model_version_id, config.approved_by)
    context.log.info(
        "CLV model %s approved by %s", config.model_version_id, config.approved_by
    )
    return result


@op
def clv_retention_op(context: OpExecutionContext, config: ClvTenantConfig) -> dict:
    """Export valuation rows past retention to object storage, then delete them."""
    service = ClvService()
    tenants = (
        [config.tenant_id]
        if config.tenant_id
        else [row["tenant_id"] for row in service.repository.list_tenants()]
    )
    results = {tenant: service.apply_retention(tenant) for tenant in tenants}
    context.log.info("CLV retention: %s", results)
    return results


@op
def clv_staleness_op(context: OpExecutionContext) -> list:
    """Fail the run (the alert) when any CLV projection is older than 72 hours."""
    stale = ClvService().stale_projections()
    if stale:
        raise Failure(
            description=f"{len(stale)} CLV projection(s) are older than 72 hours",
            metadata={"stale": str(stale)},
        )
    context.log.info("All CLV projections are fresh")
    return stale


@job(name="clv_nightly_scoring_job", tags={"backend_job": "ai_agents_runners"})
def clv_nightly_scoring_job() -> None:
    clv_nightly_scoring_op()


@job(name="clv_training_job", tags={"backend_job": "ai_agents_runners"})
def clv_training_job() -> None:
    clv_training_op()


@job(name="clv_rescore_job", tags={"backend_job": "ai_agents_runners"})
def clv_rescore_job() -> None:
    clv_rescore_op()


@job(name="clv_approve_model_job", tags={"backend_job": "ai_agents_runners"})
def clv_approve_model_job() -> None:
    clv_approve_model_op()


@job(name="clv_retention_job", tags={"backend_job": "ai_agents_runners"})
def clv_retention_job() -> None:
    clv_retention_op()


@job(name="clv_staleness_job", tags={"backend_job": "ai_agents_runners"})
def clv_staleness_job() -> None:
    clv_staleness_op()


CLV_SCHEDULE_TIMEZONE = os.environ.get("CLV_SCHEDULE_TIMEZONE", "Asia/Ho_Chi_Minh")
clv_schedules = [
    ScheduleDefinition(
        job=job_definition, cron_schedule=cron, execution_timezone=CLV_SCHEDULE_TIMEZONE
    )
    for job_definition, cron in (
        (clv_nightly_scoring_job, "0 2 * * *"),
        (clv_training_job, "0 3 1 * *"),
        (clv_retention_job, "0 4 * * *"),
        (clv_staleness_job, "0 * * * *"),
    )
]

defs = Definitions(
    jobs=[
        ai_agents_master_job,
        agent_type_pipeline_job,
        personalization_job,
        clv_nightly_scoring_job,
        clv_training_job,
        clv_rescore_job,
        clv_approve_model_job,
        clv_retention_job,
        clv_staleness_job,
    ],
    schedules=clv_schedules,
    sensors=[ai_agent_workflow_schedule_sensor],
)
