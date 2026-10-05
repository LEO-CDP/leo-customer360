"""Tenant-scoped segment workflow planning and recommendation execution."""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg2
from croniter import croniter
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor, execute_values

load_dotenv()

logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
SEGMENT_PROFILE_SQL = f"""
    SELECT
        profile.master_profile_id,
        profile.domain,
        COALESCE(profile.segmentation_tags, ARRAY[]::text[]) AS segmentation_tags
    FROM {DB_SCHEMA}.cdp_master_profiles AS profile
    JOIN {DB_SCHEMA}.cdp_segments AS segment
      ON segment.tenant_id = profile.tenant_id
     AND segment.segment_id = %s
     AND segment.is_active = TRUE
    WHERE profile.tenant_id = %s
      AND profile.status_code = 1
      AND segment.segment_tag = ANY(
          COALESCE(profile.segmentation_tags, ARRAY[]::text[])
      )
    ORDER BY profile.master_profile_id
"""
START_RECOMMENDATION_RUN_SQL = f"""
    INSERT INTO {DB_SCHEMA}.cdp_profile_recommendation_runs (
        tenant_id, segment_id, run_id, status, profile_count, step_count
    ) VALUES (%s, %s, %s, 'RUNNING', %s, %s)
    ON CONFLICT (tenant_id, segment_id, run_id)
    DO UPDATE SET
        status = 'RUNNING',
        profile_count = EXCLUDED.profile_count,
        step_count = EXCLUDED.step_count,
        profile_runs_processed = 0,
        recommendations_written = 0,
        error_message = NULL,
        started_at = now(),
        completed_at = NULL
"""
DELETE_RECOMMENDATION_RUN_ROWS_SQL = f"""
    DELETE FROM {DB_SCHEMA}.cdp_profile_recommendations
    WHERE tenant_id = %s
      AND segment_id = %s
      AND run_id = %s
"""
FINISH_RECOMMENDATION_RUN_SQL = f"""
    UPDATE {DB_SCHEMA}.cdp_profile_recommendation_runs
    SET status = %s,
        profile_runs_processed = %s,
        recommendations_written = %s,
        error_message = %s,
        completed_at = now()
    WHERE tenant_id = %s AND segment_id = %s AND run_id = %s
"""
DELETE_PREVIOUS_SUCCESSFUL_RUNS_SQL = f"""
    DELETE FROM {DB_SCHEMA}.cdp_profile_recommendation_runs
    WHERE tenant_id = %s
      AND segment_id = %s
      AND status = 'SUCCEEDED'
      AND run_id <> %s
"""
DELETE_FAILED_RUN_ROWS_SQL = f"""
    DELETE FROM {DB_SCHEMA}.cdp_profile_recommendations
    WHERE tenant_id = %s AND segment_id = %s AND run_id = %s
"""
UPSERT_PROFILE_RECOMMENDATIONS_SQL = f"""
    INSERT INTO {DB_SCHEMA}.cdp_profile_recommendations (
        tenant_id, segment_id, master_profile_id, agent_code, content_item_id,
        run_id, rank, score, matched_tags, reason
    ) VALUES %s
    ON CONFLICT (
        tenant_id, segment_id, master_profile_id, agent_code, run_id, content_item_id
    ) DO UPDATE SET
        rank = EXCLUDED.rank,
        score = EXCLUDED.score,
        matched_tags = EXCLUDED.matched_tags,
        reason = EXCLUDED.reason,
        generated_at = now()
"""


@dataclass(frozen=True)
class WorkflowRunSummary:
    """Selection and execution metrics for one master workflow run."""

    trigger: str
    tenant_id: str | None
    segment_id: str | None
    selected_steps: int
    skipped_steps: int
    plan: tuple[dict[str, Any], ...]
    executed_steps: int = 0
    profile_runs_processed: int = 0
    recommendations_written: int = 0
    unsupported_steps: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "tenant_id": self.tenant_id,
            "segment_id": self.segment_id,
            "selected_steps": self.selected_steps,
            "skipped_steps": self.skipped_steps,
            "plan": list(self.plan),
            "executed_steps": self.executed_steps,
            "profile_runs_processed": self.profile_runs_processed,
            "recommendations_written": self.recommendations_written,
            "unsupported_steps": self.unsupported_steps,
        }


def _connect():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )


def _schedule_matches(expression: str | None, when: datetime) -> bool:
    if not expression:
        return False
    try:
        return bool(croniter.match(expression, when))
    except (ValueError, TypeError):
        logger.warning("Skipping invalid workflow schedule %r", expression, exc_info=True)
        return False


def _execute_agent_pipeline(payload: dict[str, Any], run_id: str) -> Any:
    from ai_agents_runners.agent_pipeline import execute_agent_pipeline

    return execute_agent_pipeline(payload, run_id=run_id)


def _candidate_content_ids_from_db(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Workflow candidate IDs were not returned as a JSON array") from exc
    if not isinstance(value, list):
        raise ValueError("Workflow candidate IDs must be returned as an array")
    try:
        return [str(uuid.UUID(str(candidate_id))) for candidate_id in value]
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Workflow contains an invalid candidate content ID") from exc


class AgentWorkflowMasterTask:
    """Resolve and execute active ranking steps for API events and cron ticks."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] = _connect,
        pipeline_executor: Callable[[dict[str, Any], str], Any] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._pipeline_executor = pipeline_executor or _execute_agent_pipeline

    def run(
        self,
        *,
        trigger: str,
        tenant_id: str | None = None,
        segment_id: str | None = None,
        trigger_event: dict[str, Any] | None = None,
        scheduled_at: datetime | None = None,
    ) -> WorkflowRunSummary:
        if trigger not in {"api", "cron"}:
            raise ValueError("trigger must be either 'api' or 'cron'")
        if trigger == "api" and not tenant_id:
            raise ValueError("tenant_id is required for API-triggered workflow runs")
        if tenant_id:
            uuid.UUID(str(tenant_id))
        if segment_id:
            uuid.UUID(str(segment_id))

        rows = self._load_steps(tenant_id=tenant_id, segment_id=segment_id)
        now = scheduled_at or datetime.now(timezone.utc)
        plan: list[dict[str, Any]] = []
        skipped = 0
        for row in rows:
            if trigger == "cron" and not _schedule_matches(row["effective_schedule"], now):
                skipped += 1
                continue
            plan.append(
                {
                    "tenant_id": str(row["tenant_id"]),
                    "segment_id": str(row["segment_id"]),
                    "agent_code": row["agent_code"],
                    "model_type": row["model_type"],
                    "execution_order": row["execution_order"],
                    "configuration": row["configuration"] or {},
                    "candidate_content_item_ids": _candidate_content_ids_from_db(
                        row["candidate_content_item_ids"]
                    ),
                    "trigger_event": trigger_event or {},
                }
            )

        logger.info(
            "AI-agent workflow master task selected %d step(s), skipped %d (trigger=%s tenant_id=%s segment_id=%s)",
            len(plan),
            skipped,
            trigger,
            tenant_id or "ALL",
            segment_id or "ALL",
        )
        return WorkflowRunSummary(
            trigger=trigger,
            tenant_id=str(tenant_id) if tenant_id else None,
            segment_id=str(segment_id) if segment_id else None,
            selected_steps=len(plan),
            skipped_steps=skipped,
            plan=tuple(plan),
        )

    def execute(self, summary: WorkflowRunSummary, *, run_id: str) -> WorkflowRunSummary:
        """Run each configured ranking step for every active member profile."""
        executed_steps = 0
        profile_runs_processed = 0
        recommendations_written = 0
        unsupported_steps = 0
        ranking_steps_by_segment: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for step in summary.plan:
            if step["model_type"] != "ranking_recommendation":
                unsupported_steps += 1
                logger.warning(
                    "Skipping unsupported workflow agent type=%s agent_code=%s",
                    step["model_type"],
                    step["agent_code"],
                )
                continue
            segment_key = (step["tenant_id"], step["segment_id"])
            ranking_steps_by_segment.setdefault(segment_key, []).append(step)

        for (tenant_id, segment_id), steps in ranking_steps_by_segment.items():
            for step in steps:
                if not step["candidate_content_item_ids"]:
                    raise ValueError(
                        "Ranking Recommendation agent "
                        f"'{step['agent_code']}' has no selected content items"
                    )

            profiles = self._load_segment_profiles(
                tenant_id=tenant_id,
                segment_id=segment_id,
            )
            self._start_recommendation_run(
                tenant_id=tenant_id,
                segment_id=segment_id,
                run_id=run_id,
                profile_count=len(profiles),
                step_count=len(steps),
            )
            step_profile_count = 0
            step_recommendation_count = 0
            try:
                for step in steps:
                    for profile in profiles:
                        payload = {
                            "tenant_id": tenant_id,
                            "segment_id": segment_id,
                            "agent_code": step["agent_code"],
                            "model_type": step["model_type"],
                            "input_data": {
                                "domain": profile["domain"],
                                "segmentation_tags": profile["segmentation_tags"],
                            },
                            "configuration": step["configuration"],
                            "candidate_content_item_ids": step["candidate_content_item_ids"],
                            "trigger_event": step["trigger_event"],
                        }
                        output = self._pipeline_executor(payload, run_id)
                        ranked_items = output.result["ranked_items"]
                        self._insert_profile_recommendations(
                            tenant_id=tenant_id,
                            segment_id=segment_id,
                            master_profile_id=str(profile["master_profile_id"]),
                            agent_code=step["agent_code"],
                            run_id=run_id,
                            ranked_items=ranked_items,
                        )
                        step_profile_count += 1
                        step_recommendation_count += len(ranked_items)
            except Exception as exc:
                try:
                    self._finish_recommendation_run(
                        tenant_id=tenant_id,
                        segment_id=segment_id,
                        run_id=run_id,
                        status="FAILED",
                        profile_runs_processed=step_profile_count,
                        recommendations_written=step_recommendation_count,
                        error_message=str(exc)[:2000],
                    )
                except Exception:
                    logger.exception(
                        "Failed to mark recommendation run failed (tenant_id=%s segment_id=%s run_id=%s)",
                        tenant_id,
                        segment_id,
                        run_id,
                    )
                raise

            self._finish_recommendation_run(
                tenant_id=tenant_id,
                segment_id=segment_id,
                run_id=run_id,
                status="SUCCEEDED",
                profile_runs_processed=step_profile_count,
                recommendations_written=step_recommendation_count,
                error_message=None,
            )
            executed_steps += len(steps)
            profile_runs_processed += step_profile_count
            recommendations_written += step_recommendation_count

        return replace(
            summary,
            executed_steps=executed_steps,
            profile_runs_processed=profile_runs_processed,
            recommendations_written=recommendations_written,
            unsupported_steps=unsupported_steps,
        )

    def _load_segment_profiles(
        self, *, tenant_id: str, segment_id: str
    ) -> list[dict[str, Any]]:
        with self._connection_factory() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
                cursor.execute(SEGMENT_PROFILE_SQL, (segment_id, tenant_id))
                return list(cursor.fetchall())

    def _start_recommendation_run(
        self,
        *,
        tenant_id: str,
        segment_id: str,
        run_id: str,
        profile_count: int,
        step_count: int,
    ) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
                cursor.execute(
                    DELETE_RECOMMENDATION_RUN_ROWS_SQL,
                    (tenant_id, segment_id, run_id),
                )
                cursor.execute(
                    START_RECOMMENDATION_RUN_SQL,
                    (tenant_id, segment_id, run_id, profile_count, step_count),
                )

    def _finish_recommendation_run(
        self,
        *,
        tenant_id: str,
        segment_id: str,
        run_id: str,
        status: str,
        profile_runs_processed: int,
        recommendations_written: int,
        error_message: str | None,
    ) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
                cursor.execute(
                    FINISH_RECOMMENDATION_RUN_SQL,
                    (
                        status,
                        profile_runs_processed,
                        recommendations_written,
                        error_message,
                        tenant_id,
                        segment_id,
                        run_id,
                    ),
                )
                if status == "SUCCEEDED":
                    cursor.execute(
                        DELETE_PREVIOUS_SUCCESSFUL_RUNS_SQL,
                        (tenant_id, segment_id, run_id),
                    )
                elif status == "FAILED":
                    cursor.execute(
                        DELETE_FAILED_RUN_ROWS_SQL,
                        (tenant_id, segment_id, run_id),
                    )

    def _insert_profile_recommendations(
        self,
        *,
        tenant_id: str,
        segment_id: str,
        master_profile_id: str,
        agent_code: str,
        run_id: str,
        ranked_items: list[dict[str, Any]],
    ) -> None:
        rows = [
            (
                tenant_id,
                segment_id,
                master_profile_id,
                agent_code,
                item["item_id"],
                run_id,
                item["rank"],
                item["score"],
                item["matched_tags"],
                item["reason"],
            )
            for item in ranked_items
        ]
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
                if rows:
                    execute_values(
                        cursor,
                        UPSERT_PROFILE_RECOMMENDATIONS_SQL,
                        rows,
                        template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        page_size=500,
                    )

    def _load_steps(
        self,
        *,
        tenant_id: str | None,
        segment_id: str | None,
    ) -> list[dict[str, Any]]:
        predicates = [
            "w.is_active = TRUE",
            "a.status = 'ACTIVE'",
        ]
        params: list[Any] = []
        if tenant_id:
            predicates.append("w.tenant_id = %s")
            params.append(tenant_id)
        if segment_id:
            predicates.append("w.segment_id = %s")
            params.append(segment_id)
        query = f"""
            SELECT
                w.tenant_id,
                w.segment_id,
                w.agent_code,
                w.execution_order,
                w.configuration,
                to_json(w.candidate_content_item_ids) AS candidate_content_item_ids,
                COALESCE(w.schedule_definition, a.schedule_definition) AS effective_schedule,
                a.model_type
            FROM {DB_SCHEMA}.cdp_agent_workflow AS w
            JOIN {DB_SCHEMA}.cdp_ai_agents AS a
              ON a.agent_code = w.agent_code
            WHERE {" AND ".join(predicates)}
            ORDER BY w.tenant_id, w.segment_id, w.execution_order, w.agent_code
        """
        with self._connection_factory() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, params)
                return list(cursor.fetchall())
