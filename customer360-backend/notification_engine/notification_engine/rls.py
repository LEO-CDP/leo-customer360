"""PostgreSQL Row-Level Security context helper for the notification engine.

Standalone copy (same helper email_engine/segmentation each ship) -- this is an
independently deployed code location with its own requirements.txt, so it does
not import customer360-api / sibling-service code.
"""

from typing import Optional


def set_tenant_context(cursor, tenant_id: Optional[str]) -> None:
    """Set the transaction's RLS tenant context before tenant-owned SQL."""
    value = str(tenant_id).strip() if tenant_id is not None else ""
    cursor.execute("SET app.tenant_id = %s", (value,))
