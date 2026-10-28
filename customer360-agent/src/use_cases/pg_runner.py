"""Executes guarded SQL against Postgres for the SQL-backed agents.

Each call runs in its own read-only transaction with the tenant context set,
so even SQL that slips past `sql_guard` cannot write. Row-level security only
scopes rows to the tenant when the connection cannot bypass it, so the runner
refuses to run on a superuser or BYPASSRLS role instead of leaking rows; pass
`role` to switch to a restricted one."""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable, Optional

STATEMENT_TIMEOUT_MS = 10_000
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def make_pg_runner(
    engine, tenant_id: str, role: Optional[str] = None
) -> Callable[[str], list[dict[str, Any]]]:
    from sqlalchemy import text

    tenant = str(uuid.UUID(tenant_id))  # raises ValueError on anything that is not a UUID
    if role is not None and not _IDENTIFIER.match(role):
        raise ValueError(f"invalid role name: {role!r}")

    def run(sql: str) -> list[dict[str, Any]]:
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
                if role:
                    conn.execute(text(f"SET LOCAL ROLE {role}"))
                bypasses = conn.execute(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
                ).scalar()
                if bypasses:
                    raise RuntimeError(
                        "connection role can bypass row-level security; pass a restricted role"
                    )
                conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
                return [dict(row._mapping) for row in conn.execute(text(sql))]

    return run
