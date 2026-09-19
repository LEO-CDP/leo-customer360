"""Resolve a tenant's Zalo OA token for dispatch.

OA config/tokens live in ``sys_data_source`` (slug='zalo-oa'); the send pipeline
reads the current access token from there at run time (DB is source of truth;
the token-refresh schedule keeps it fresh). No SMTP-style provider table.
"""

from .db import DB_SCHEMA
from .rls import set_tenant_context


def load_oa_token(conn, tenant_id: str) -> dict:
    """Return ``{'access_token', 'oa_id'}`` for the tenant's connected OA (values
    may be None if the OA is not connected)."""
    with conn.cursor() as cur:
        set_tenant_context(cur, tenant_id)
        cur.execute(
            f"SELECT access_tokens FROM {DB_SCHEMA}.sys_data_source "
            f"WHERE tenant_id = %s AND slug = 'zalo-oa' AND status = 1",
            (tenant_id,),
        )
        row = cur.fetchone()
    tokens = (row[0] if row else None) or {}
    return {"access_token": tokens.get("access_token"), "oa_id": tokens.get("oa_id")}
