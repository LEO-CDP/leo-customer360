"""Resolve outbound CRM connectors for the email activation pipeline.

The active connector row is read once per campaign run from the generic
``crm_connector_config`` table. The returned flat keys intentionally match the
existing dispatch adapter contract, so adding SMS, push, chat, or ads
connectors does not couple those adapters to the database schema.
"""

import os
from typing import Optional

from psycopg2.extras import RealDictCursor

from .db import DB_SCHEMA

_CONFIG_COLUMNS = (
    "provider", "auth_type", "credentials_ref", "credentials", "config", "name",
)


def _env_config() -> dict:
    """Static fallback from SMTP_* env vars -- mock unless an SMTP host is set."""
    provider = os.environ.get("EMAIL_DISPATCH_ADAPTER", "mock").strip().lower()
    if provider not in {"mock", "smtp"}:
        provider = "mock"
    return {
        "provider": provider,
        "smtp_host": os.environ.get("SMTP_HOST"),
        "smtp_port": int(os.environ["SMTP_PORT"]) if os.environ.get("SMTP_PORT") else None,
        "smtp_username": os.environ.get("SMTP_USERNAME") or None,
        "smtp_password": os.environ.get("SMTP_PASSWORD") or None,
        "smtp_use_tls": os.environ.get("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"},
        "from_address": os.environ.get("EMAIL_FROM_ADDRESS"),
        "from_name": os.environ.get("EMAIL_FROM_NAME"),
        "name": "env",
        "source": "env",
    }


def _db_config(conn, tenant_id: str) -> Optional[dict]:
    """Resolve the tenant's active outbound email connector, or None."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET app.tenant_id = %s", (tenant_id,))
        cur.execute(
            f"SELECT {', '.join(_CONFIG_COLUMNS)} FROM {DB_SCHEMA}.crm_connector_config "
            "WHERE tenant_id = %(tenant_id)s "
            "AND connector_type = 'EMAIL' "
            "AND direction IN ('OUTBOUND', 'BIDIRECTIONAL') "
            "AND status = 'ACTIVE' AND is_active = TRUE "
            "ORDER BY is_default DESC, updated_at DESC LIMIT 1",
            {"tenant_id": tenant_id},
        )
        row = cur.fetchone()
    if not row:
        return None

    row = dict(row)
    connector_config = dict(row.pop("config") or {})
    credentials = dict(row.pop("credentials") or {})
    resolved = {**connector_config, **row}
    resolved["provider"] = str(resolved.get("provider") or "mock").lower()
    resolved["smtp_username"] = resolved.get("smtp_username") or credentials.get("username")
    resolved["smtp_password"] = credentials.get("password")
    resolved["source"] = "db"
    return resolved


def load_email_config(tenant_id: str, conn) -> dict:
    """Resolve the tenant's active email connector, then env/mock fallback."""
    return _db_config(conn, tenant_id) or _env_config()
