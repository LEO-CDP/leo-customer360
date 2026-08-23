"""PostgreSQL Row-Level Security context helpers for identity resolution."""

from typing import Optional


def set_tenant_context(cursor, tenant_id: Optional[str]) -> None:
    """Set the transaction's RLS tenant context before tenant-owned SQL."""
    value = str(tenant_id).strip() if tenant_id is not None else ""
    cursor.execute("SET app.tenant_id = %s", (value,))
