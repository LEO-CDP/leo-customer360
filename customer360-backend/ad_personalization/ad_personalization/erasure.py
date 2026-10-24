"""Complete data-subject erasure requests (task T081).

The admin API records a request (``erasure_request``: tenant, hashed visitor key,
urgent or not) and purges the visitor's hot state at once. This job does the
durable part. For each open request, urgent first and then oldest, it deletes the
visitor's rows from every table that can hold them and records how many it removed:

    ad_event (all partitions, including the default one), serving_decision,
    visitor_behaviour_profile, consent_record, consent_history, user_serving_key

Every delete is filtered by BOTH ``tenant_id`` and ``user_key_hash``, so one
tenant's request can never touch another tenant's rows, even for the same key.

Outcomes:

* ``completed`` with ``deleted_counts``, even when the visitor had no data (the
  request is the evidence that erasure was carried out).
* ``failed`` with the reason if anything went wrong; a failed request is picked up
  again on the next run. Each request runs inside a savepoint, so one failure
  never rolls back the others.
* A request past its limit (72 hours if urgent, 30 days otherwise) is logged at
  ERROR level, whether or not this run manages to complete it. It never stops the job.

A request is never deleted. The functions here never commit; the caller owns the
transaction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from psycopg2 import sql
from psycopg2.extras import Json

logger = logging.getLogger(__name__)

URGENT_LIMIT = timedelta(hours=72)
STANDARD_LIMIT = timedelta(days=30)

# A failed request is retried, so it counts as open for processing.
OPEN_STATUSES = ("pending", "redis_purged", "failed")

# (result key, table). Every one has tenant_id and user_key_hash columns.
ERASED_TABLES = (
    ("ad_event", "ad_event"),
    ("serving_decision", "serving_decision"),
    ("visitor_behaviour_profile", "visitor_behaviour_profile"),
    ("consent_record", "consent_record"),
    ("consent_history", "consent_history"),
    ("user_serving_key", "user_serving_key"),
)


def deadline_for(requested_at: datetime, urgent: bool) -> datetime:
    return requested_at + (URGENT_LIMIT if urgent else STANDARD_LIMIT)


def open_requests(conn, schema: str) -> list[dict[str, Any]]:
    """Open requests, urgent first, then oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT erasure_id, tenant_id, user_key_hash, urgent, requested_at
                FROM {s}.erasure_request
                WHERE status = ANY(%s)
                ORDER BY urgent DESC, requested_at ASC, erasure_id ASC
                """
            ).format(s=sql.Identifier(schema)),
            (list(OPEN_STATUSES),),
        )
        return [
            {
                "erasure_id": int(r[0]),
                "tenant_id": int(r[1]),
                "user_key_hash": bytes(r[2]),
                "urgent": bool(r[3]),
                "requested_at": r[4],
            }
            for r in cur.fetchall()
        ]


def _erase_visitor(conn, schema: str, tenant_id: int, user_key: bytes) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for result_key, table in ERASED_TABLES:
            cur.execute(
                sql.SQL(
                    "DELETE FROM {s}.{t} WHERE tenant_id = %s AND user_key_hash = %s"
                ).format(s=sql.Identifier(schema), t=sql.Identifier(table)),
                (tenant_id, user_key),
            )
            counts[result_key] = cur.rowcount
    return counts


def _complete(conn, schema: str, erasure_id: int, counts: dict[str, int], now: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                UPDATE {s}.erasure_request
                SET status = 'completed', completed_at = %s, deleted_counts = %s,
                    failure_reason = NULL
                WHERE erasure_id = %s
                """
            ).format(s=sql.Identifier(schema)),
            (now, Json(counts), erasure_id),
        )


def _fail(conn, schema: str, erasure_id: int, reason: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "UPDATE {s}.erasure_request SET status = 'failed', failure_reason = %s "
                "WHERE erasure_id = %s"
            ).format(s=sql.Identifier(schema)),
            (reason[:500], erasure_id),
        )


def process_erasure_requests(
    conn,
    *,
    schema: str,
    now: Optional[datetime] = None,
    log: Callable[[str], None] = logger.info,
) -> dict[str, Any]:
    """Process every open request. Returns ``{processed, completed, failed, overdue}``."""
    now = now or datetime.now(timezone.utc)
    summary: dict[str, Any] = {"processed": 0, "completed": 0, "failed": 0, "overdue": 0}

    for request in open_requests(conn, schema):
        summary["processed"] += 1
        erasure_id = request["erasure_id"]

        if now > deadline_for(request["requested_at"], request["urgent"]):
            summary["overdue"] += 1
            logger.error(
                "erasure request %s (tenant %s, %s) is past its %s limit",
                erasure_id,
                request["tenant_id"],
                "urgent" if request["urgent"] else "standard",
                "72 hour" if request["urgent"] else "30 day",
            )

        with conn.cursor() as cur:
            cur.execute("SAVEPOINT erasure_request")
        try:
            counts = _erase_visitor(conn, schema, request["tenant_id"], request["user_key_hash"])
            _complete(conn, schema, erasure_id, counts, now)
        except Exception as exc:  # noqa: BLE001 - recorded on the request and retried next run
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT erasure_request")
            _fail(conn, schema, erasure_id, f"{type(exc).__name__}: {exc}")
            summary["failed"] += 1
            logger.exception("erasure request %s failed; it will be retried", erasure_id)
        else:
            summary["completed"] += 1
            log(f"erasure: request {erasure_id} completed {counts}")
        finally:
            with conn.cursor() as cur:
                cur.execute("RELEASE SAVEPOINT erasure_request")

    return summary
