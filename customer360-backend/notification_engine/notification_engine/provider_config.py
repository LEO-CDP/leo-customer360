"""Resolve tenant-scoped Zalo settings from ``crm_connector_config``."""

from .db import DB_SCHEMA
from .rls import set_tenant_context

DEFAULT_ZALO_CONFIG = {
    "token_refresh_cron": "*/30 * * * *",
    "dispatch_adapter": "mock",
    "zns_api_base_url": "https://business.openapi.zalo.me",
    "batch_size": 500,
    "optout_projection_cron": "*/15 * * * *",
    "optout_lookback_hours": 6,
}


def load_zalo_config(conn, tenant_id: str) -> dict | None:
    """Return the active tenant Zalo connector with credentials merged in."""
    with conn.cursor() as cur:
        set_tenant_context(cur, tenant_id)
        cur.execute(
            f"""SELECT credentials, config
                   FROM {DB_SCHEMA}.crm_connector_config
                  WHERE tenant_id = %s
                    AND connector_type = 'CHAT'
                    AND provider = 'ZALO'
                    AND direction IN ('OUTBOUND', 'BIDIRECTIONAL')
                    AND status = 'ACTIVE' AND is_active = TRUE
                  ORDER BY is_default DESC, updated_at DESC
                  LIMIT 1""",
            (tenant_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    credentials, config = row
    values = dict(DEFAULT_ZALO_CONFIG)
    values.update(config or {})
    values.update(credentials or {})
    return values


def load_oa_token(conn, tenant_id: str) -> dict:
    """Return the tenant's current Zalo access token and OA id."""
    values = load_zalo_config(conn, tenant_id) or {}
    return {"access_token": values.get("access_token"), "oa_id": values.get("oa_id")}
