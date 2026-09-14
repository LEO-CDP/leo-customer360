"""PostgreSQL Row-Level Security context helpers for the campaign activation service.

Standalone copy (same helper segmentation/identity_resolution each ship) --
this is an independently deployed code location with its own requirements.txt,
so it does not import customer360-api / sibling-service code. See
backend-system/README.md "Independent code locations".
"""

from typing import Optional


def set_tenant_context(cursor, tenant_id: Optional[str]) -> None:
    """Set the transaction's RLS tenant context before tenant-owned SQL."""
    value = str(tenant_id).strip() if tenant_id is not None else ""
    # ponytail: session-level SET (not SET LOCAL) -- each connect() is a dedicated
    # connection closed after the run, so tenant context can't leak across callers.
    # Switch to SET LOCAL if this is ever pooled behind a transaction-mode pgbouncer.
    cursor.execute("SET app.tenant_id = %s", (value,))
