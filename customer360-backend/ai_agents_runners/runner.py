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
LOAD_SELECTED_CONTENT_SQL = f"""
    SELECT
        content.content_item_id,
        content.domain,
        content.item_type,
        content.title,
        content.summary,
        content.segment_tags,
        content.embedding_model,
        vector_dims(content.embedding) AS embedding_dimensions
    FROM {DB_SCHEMA}.cdp_content_items AS content
    WHERE content.tenant_id = %s
      AND content.status_code = 1
      AND content.content_item_id = ANY(%s::uuid[])
"""
UPDATE_CONTENT_EMBEDDINGS_SQL = f"""
    UPDATE {DB_SCHEMA}.cdp_content_items AS content
    SET embedding = embedding_values.embedding::vector,
        embedding_model = embedding_values.embedding_model,
        updated_at = now()
    FROM (VALUES %s) AS embedding_values(tenant_id, content_item_id, embedding, embedding_model)
    WHERE content.tenant_id = embedding_values.tenant_id::uuid
      AND content.content_item_id = embedding_values.content_item_id::uuid
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
DELETE_FAILED_RUN_ROWS_SQL = f"""
    DELETE FROM {DB_SCHEMA}.cdp_profile_recommendations
    WHERE tenant_id = %s AND segment_id = %s AND run_id = %s
"""
UPSERT_PROFILE_RECOMMENDATIONS_SQL = f"""
    INSERT INTO {DB_SCHEMA}.cdp_profile_recommendations (
        tenant_id, segment_id, master_profile_id, agent_code, content_item_id,
        run_id, rank, score, semantic_score, tag_score, strategy, matched_tags, reason
    ) VALUES %s
    ON CONFLICT (
        tenant_id, segment_id, master_profile_id, agent_code, run_id, content_item_id
    ) DO UPDATE SET
        rank = EXCLUDED.rank,
        score = EXCLUDED.score,
        semantic_score = EXCLUDED.semantic_score,
        tag_score = EXCLUDED.tag_score,
        strategy = EXCLUDED.strategy,
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
        embedding_function: Callable[..., list[list[float]]] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._pipeline_executor = pipeline_executor or _execute_agent_pipeline
        self._embedding_function = embedding_function

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
                    strategy = step["configuration"].get("strategy", "tags")
                    uses_vectors = strategy in {"semantic", "hybrid"}
                    model_key = self._embedding_model_key() if uses_vectors else ""
                    profile_embeddings: list[list[float] | None]
                    if uses_vectors:
                        self._ensure_content_embeddings(
                            tenant_id=tenant_id,
                            candidate_ids=step["candidate_content_item_ids"],
                            model_key=model_key,
                        )
                        semantic_query = step["configuration"].get("semantic_query", "")
                        if semantic_query is None:
                            semantic_query = ""
                        if (
                            not isinstance(semantic_query, str)
                            or len(semantic_query) > 2000
                        ):
                            raise ValueError(
                                "configuration.semantic_query must be text of at most 2000 characters"
                            )
                        profile_query_texts = [
                            self._profile_query_text(profile, semantic_query)
                            for profile in profiles
                        ]
                        profile_embeddings = self._embed(
                            profile_query_texts,
                            task="query",
                        )
                        if len(profile_embeddings) != len(profiles):
                            raise ValueError(
                                "Embedding provider did not return a query vector per profile"
                            )
                    else:
                        profile_embeddings = [None] * len(profiles)

                    for profile, profile_embedding in zip(
                        profiles, profile_embeddings, strict=True
                    ):
                        payload = {
                            "tenant_id": tenant_id,
                            "segment_id": segment_id,
                            "agent_code": step["agent_code"],
                            "model_type": step["model_type"],
                            "input_data": {
                                "domain": profile["domain"],
                                "segmentation_tags": profile["segmentation_tags"],
                                **(
                                    {
                                        "profile_embedding": profile_embedding,
                                        "embedding_model": model_key,
                                    }
                                    if profile_embedding is not None
                                    else {}
                                ),
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

    @staticmethod
    def _profile_query_text(profile: dict[str, Any], semantic_query: str) -> str:
        domain = str(profile["domain"]).strip()
        tags = [
            str(tag).strip()
            for tag in (profile.get("segmentation_tags") or [])
            if str(tag).strip()
        ]
        parts = [f"Customer domain: {domain}."]
        if tags:
            parts.append("Customer interests and segment labels: " + ", ".join(tags) + ".")
        if semantic_query.strip():
            parts.append("Recommendation context: " + semantic_query.strip())
        return " ".join(parts)

    def _ensure_content_embeddings(
        self,
        *,
        tenant_id: str,
        candidate_ids: list[str],
        model_key: str,
    ) -> None:
        with self._connection_factory() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
                cursor.execute(
                    LOAD_SELECTED_CONTENT_SQL,
                    (tenant_id, candidate_ids),
                )
                rows = list(cursor.fetchall())

        if len(rows) != len(candidate_ids):
            raise ValueError(
                "One or more selected content items are missing, inactive, "
                "or outside this tenant"
            )
        expected_dimensions = int(model_key.rsplit(":", 1)[-1])
        missing_rows = [
            row
            for row in rows
            if row["embedding_model"] != model_key
            or row["embedding_dimensions"] != expected_dimensions
        ]
        if not missing_rows:
            return

        texts = [
            self._content_embedding_text(row)
            for row in missing_rows
        ]
        vectors = self._embed(texts, task="document")
        if len(vectors) != len(missing_rows):
            raise ValueError("Embedding provider did not return a vector per content item")
        values = [
            (
                tenant_id,
                str(row["content_item_id"]),
                self._vector_literal(vector),
                model_key,
            )
            for row, vector in zip(missing_rows, vectors, strict=True)
        ]
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
                execute_values(
                    cursor,
                    UPDATE_CONTENT_EMBEDDINGS_SQL,
                    values,
                    template="(%s, %s, %s, %s)",
                    page_size=100,
                )

    @staticmethod
    def _content_embedding_text(row: dict[str, Any]) -> str:
        tags = ", ".join(row.get("segment_tags") or [])
        return " ".join(
            value
            for value in (
                f"Type: {row['item_type']}.",
                f"Domain: {row['domain']}.",
                f"Title: {row['title']}.",
                f"Summary: {row.get('summary') or ''}",
                f"Keywords: {tags}." if tags else "",
            )
            if value
        )

    @staticmethod
    def _vector_literal(vector: list[float]) -> str:
        return "[" + ",".join(str(float(value)) for value in vector) + "]"

    def _embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        if self._embedding_function is not None:
            return self._embedding_function(texts, task=task)
        from .agent_pipeline.embeddings import embed_texts

        return embed_texts(texts, task=task)

    @staticmethod
    def _embedding_model_key() -> str:
        from .agent_pipeline.embeddings import embedding_model_key

        return embedding_model_key()

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
                if status == "FAILED":
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
                item.get("semantic_score"),
                item.get("tag_score", 0.0),
                item.get("strategy", "tags"),
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
                        template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
