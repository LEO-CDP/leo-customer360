"""Ask your data: a question in, a checked SELECT, its rows and a plain-English summary out.

The agent service only proposes SQL and writes the summary; it never touches the
database. This repository owns everything with consequences:

- the agent is shown only tables and columns the restricted role can read, so it never
  learns that a personal column exists;
- the SQL it returns is checked again here with the shared guard (a model-written
  statement is never trusted because the agent said it was fine);
- the SQL runs as the `customer360_ask_data` role (migration 012) in a read-only,
  always-rolled-back transaction with a timeout and the tenant setting applied. That
  role is the real boundary: Postgres refuses personal columns, unlisted tables and
  writes, and row-level security still filters every table by tenant;
- results are streamed through a server-side cursor and cut at MAX_ROWS, so the
  statement runs exactly as the guard approved it (no wrapping or rewriting).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from leo_customer360_agent import agents as default_agent_client
from leo_customer360_agent.client import AIProviderError
from leo_customer360_agent.schema_docs import build_schema_docs
from leo_customer360_agent.sql_guard import UnsafeSQLError, ensure_select_only

logger = logging.getLogger(__name__)

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 10_000
SCHEMA = "customer360"
DOCS_TTL_SECONDS = 300
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AskDataUnavailable(RuntimeError):
    """The feature cannot run here: no usable restricted role, or nothing readable."""


class AskDataAgentError(RuntimeError):
    """The agent service failed or returned something unusable."""


class AskDataQueryError(RuntimeError):
    """The database refused or failed the approved SELECT (for example, permission denied)."""


@dataclass(frozen=True)
class AskDataResult:
    status: str  # "ok" | "not_found" | "refused" | "error"
    sql: Optional[str] = None
    rows: Optional[list[dict[str, Any]]] = None
    truncated: bool = False
    narrative: Optional[str] = None
    ungrounded: list[str] = field(default_factory=list)
    chart: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    narrative_error: Optional[str] = None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _db_message(exc: DBAPIError) -> str:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    primary = getattr(diag, "message_primary", None)
    return primary or (str(orig or exc).splitlines() or ["database error"])[0]


def _check_role_name(role: str) -> str:
    if not role or not _IDENTIFIER.match(role):
        raise AskDataUnavailable("the ask-data database role is not configured")
    return role


_DOCS_QUERY = """SELECT c.relname AS table_name, a.attname AS column_name,
       obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = :schema AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND has_column_privilege(:role, c.oid, a.attnum, 'SELECT')
ORDER BY c.relname, a.attnum"""

_DOCS_CACHE: dict[tuple[int, str], tuple[float, dict[str, str]]] = {}


def clear_docs_cache() -> None:
    _DOCS_CACHE.clear()


def load_schema_docs(engine, role: str) -> dict[str, str]:
    """Docs for exactly the tables and columns `role` may read, cached briefly (the schema is the same for every tenant)."""
    _check_role_name(role)
    key = (id(engine), role)
    cached = _DOCS_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < DOCS_TTL_SECONDS:
        return cached[1]

    columns: dict[str, list[str]] = {}
    comments: dict[str, Optional[str]] = {}
    try:
        with engine.connect() as conn:
            for row in conn.execute(text(_DOCS_QUERY), {"schema": SCHEMA, "role": role}):
                columns.setdefault(row.table_name, []).append(row.column_name)
                comments.setdefault(row.table_name, row.comment)
    except SQLAlchemyError as exc:
        raise AskDataUnavailable(f"could not read the schema for ask-data: {exc.__class__.__name__}") from exc

    docs = build_schema_docs(columns, comments)
    _DOCS_CACHE[key] = (time.monotonic(), docs)
    return docs


def run_as_role(
    engine,
    role: str,
    tenant_id: Any,
    sql: str,
    max_rows: int = MAX_ROWS,
    timeout_ms: int = STATEMENT_TIMEOUT_MS,
) -> tuple[list[dict[str, Any]], bool]:
    """Run one approved SELECT as `role` for `tenant_id`; return (rows, truncated).

    The transaction is read-only and always rolled back. A role that is missing, not granted
    to the connection's login, or able to bypass row-level security makes the feature
    unavailable rather than running with more access than intended."""
    _check_role_name(role)
    tenant = str(uuid.UUID(str(tenant_id)))
    try:
        conn = engine.connect()
    except SQLAlchemyError as exc:
        raise AskDataUnavailable(f"database connection failed: {exc.__class__.__name__}") from exc

    with conn:
        trans = conn.begin()
        try:
            try:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                bypasses = conn.execute(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
                ).scalar()
                if bypasses:
                    raise AskDataUnavailable(f"role {role!r} can bypass row-level security; refusing to run")
                conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
            except AskDataUnavailable:
                raise
            except DBAPIError as exc:
                raise AskDataUnavailable(f"ask-data role {role!r} is not usable: {_db_message(exc)}") from exc

            try:
                result = conn.execution_options(stream_results=True).execute(text(sql))
                fetched = result.fetchmany(max_rows + 1)
                names = list(result.keys())
                result.close()
            except DBAPIError as exc:
                raise AskDataQueryError(_db_message(exc)) from exc
            truncated = len(fetched) > max_rows
            return [dict(zip(names, row)) for row in fetched[:max_rows]], truncated
        finally:
            trans.rollback()


class AskDataRepository:
    def __init__(
        self,
        db,
        role: str,
        agent=None,
        docs_loader: Optional[Callable[[], dict[str, str]]] = None,
        runner: Optional[Callable[[Any, str], tuple[list[dict[str, Any]], bool]]] = None,
    ) -> None:
        engine = db.get_bind() if db is not None else None
        self._agent = agent or default_agent_client
        self._docs_loader = docs_loader or (lambda: load_schema_docs(engine, role))
        self._runner = runner or (lambda tenant, sql: run_as_role(engine, role, tenant, sql))

    def ask(
        self,
        tenant_id: Any,
        question: str,
        history: Optional[list[dict[str, str]]] = None,
    ) -> AskDataResult:
        docs = self._docs_loader()
        if not docs:
            raise AskDataUnavailable("no tables are readable by the ask-data role")

        try:
            proposal = self._agent.propose_sql(question, docs, history=history)
        except AIProviderError as exc:
            raise AskDataAgentError(str(exc)) from exc

        if proposal.status == "not_found":
            return AskDataResult(status="not_found")
        if proposal.status != "ok" or not proposal.sql:
            logger.warning("ask-data: agent refused (%s)", proposal.error)
            return AskDataResult(status="refused", error=proposal.error or "The agent did not produce a usable SELECT.")

        try:
            sql = ensure_select_only(proposal.sql)
        except UnsafeSQLError as exc:
            logger.warning("ask-data: SQL from the agent failed the API's own check: %s", exc)
            return AskDataResult(status="refused", error=f"Refused, only a single read-only SELECT is allowed: {exc}")

        try:
            rows, truncated = self._runner(tenant_id, sql)
        except AskDataQueryError as exc:
            return AskDataResult(status="error", sql=sql, error=str(exc))

        safe_rows = [_json_safe(row) for row in rows]
        try:
            story = self._agent.narrate_rows(question, safe_rows)
        except AIProviderError as exc:
            return AskDataResult(
                status="ok", sql=sql, rows=safe_rows, truncated=truncated, narrative=None, narrative_error=str(exc)
            )
        return AskDataResult(
            status="ok",
            sql=sql,
            rows=safe_rows,
            truncated=truncated,
            narrative=story.narrative,
            ungrounded=list(story.ungrounded),
            chart=story.chart,
        )
