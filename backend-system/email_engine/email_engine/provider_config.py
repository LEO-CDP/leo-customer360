"""Dynamic per-tenant email dispatch config resolution.

Resolved at send time from the DB (the active ``crm_email_provider_config`` row --
source of truth), else static ``SMTP_*`` env vars, else a mock default. It is read
ONCE per campaign run, so there is no cache layer -- and the SMTP password never
leaves Postgres.
"""

import os
from typing import Optional

from psycopg2.extras import RealDictCursor

from .db import DB_SCHEMA

_CONFIG_COLUMNS = (
    "provider", "smtp_host", "smtp_port", "smtp_username", "smtp_password",
    "smtp_use_tls", "from_address", "from_name", "name",
)


def _env_config() -> dict:
    """Static fallback from SMTP_* env vars -- mock unless an SMTP host is set."""
    provider = (os.environ.get("EMAIL_DISPATCH_ADAPTER", "mock")).strip().lower()
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
    """The tenant's active crm_email_provider_config row, or None."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET app.tenant_id = %s", (tenant_id,))
        cur.execute(
            f"SELECT {', '.join(_CONFIG_COLUMNS)} FROM {DB_SCHEMA}.crm_email_provider_config "
            f"WHERE tenant_id = %(tenant_id)s AND is_active = TRUE LIMIT 1",
            {"tenant_id": tenant_id},
        )
        row = cur.fetchone()
    if not row:
        return None
    config = dict(row)
    config["source"] = "db"
    return config


def load_email_config(tenant_id: str, conn) -> dict:
    """Resolve the tenant's email dispatch config: active DB row, else env/mock."""
    return _db_config(conn, tenant_id) or _env_config()
