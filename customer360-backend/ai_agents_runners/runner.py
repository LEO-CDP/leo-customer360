"""Tenant-scoped workflow selection for AI-agent runner jobs.

This module owns orchestration only. It resolves active workflow rows in
execution order and produces an explicit execution plan for agent handlers.
Handlers can be added without changing API-triggered or scheduled selection.
No profile output is fabricated when an agent handler is not implemented.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg2
from croniter import croniter
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()

logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")


@dataclass(frozen=True)
class WorkflowRunSummary:
    """Bounded result of one master workflow selection."""

    trigger: str
    tenant_id: str | None
    segment_id: str | None
    selected_steps: int
    skipped_steps: int
    plan: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "tenant_id": self.tenant_id,
            "segment_id": self.segment_id,
            "selected_steps": self.selected_steps,
            "skipped_steps": self.skipped_steps,
            "plan": list(self.plan),
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


class AgentWorkflowMasterTask:
    """Resolve active workflow steps for API events and cron ticks."""

    def __init__(self, connection_factory=_connect) -> None:
        self._connection_factory = connection_factory

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
                    "candidate_content_item_ids": [
                        str(value) for value in (row["candidate_content_item_ids"] or [])
                    ],
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
                w.candidate_content_item_ids,
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
