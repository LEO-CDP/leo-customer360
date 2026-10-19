"""PostgreSQL Row-Level Security context helpers for the campaign activation service.

Standalone copy (same helper segmentation/identity_resolution each ship) --
this is an independently deployed code location with its own requirements.txt,
so it does not import customer360-api / sibling-service code. See
customer360-backend/README.md "Independent code locations".
"""

from typing import Optional


def set_tenant_context(cursor, tenant_id: Optional[str]) -> None:
    """Set the transaction's RLS tenant context before tenant-owned SQL."""
    value = str(tenant_id).strip() if tenant_id is not None else ""
    # ponytail: session-level SET (not SET LOCAL) -- each connect() is a dedicated
    # connection closed after the run, so tenant context can't leak across callers.
    # Switch to SET LOCAL if this is ever pooled behind a transaction-mode pgbouncer.
    cursor.execute("SET app.tenant_id = %s", (value,))


def lock_campaign_execution(connection, tenant_id: str, campaign_id: str, *, wait: bool = False) -> bool:
    """Serialize activation and execution until the dedicated connection closes."""
    function = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {function}(hashtextextended(%s, 0))",
            (f"campaign-workflow:{tenant_id}:{campaign_id}",),
        )
        row = cursor.fetchone()
    return True if wait else bool(row[0])
