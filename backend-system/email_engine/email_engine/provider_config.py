"""Dynamic per-tenant email dispatch config resolution.

At send time the pipeline resolves the tenant's email provider config in this
order:

    Redis cache  ->  crm_email_provider_config (active row, DB source of truth)
                 ->  static SMTP_* env vars     ->  mock default

The resolved config is cached in Redis for ``EMAIL_PROVIDER_CONFIG_TTL_SECONDS``
(default 300s) so a full campaign send does not re-query the DB per run. Redis is
best-effort: any Redis failure just skips the cache (fail open), exactly like
customer360-api's response cache. Writes to the config (via customer360-api's
admin endpoint) delete the cache key so the next send picks up the change.
"""

import json
import logging
import os
from typing import Optional

from psycopg2.extras import RealDictCursor

from .db import DB_SCHEMA

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = int(os.environ.get("EMAIL_PROVIDER_CONFIG_TTL_SECONDS", "300"))
CACHE_KEY_PREFIX = "email_provider_config:"

_CONFIG_COLUMNS = (
    "provider", "smtp_host", "smtp_port", "smtp_username", "smtp_password",
    "smtp_use_tls", "from_address", "from_name", "name",
)


def cache_key(tenant_id: str) -> str:
    return f"{CACHE_KEY_PREFIX}{tenant_id}"


def _redis_client():
    """Best-effort Redis client from env; None if redis-py is absent or the
    server is unreachable (caching is then simply skipped)."""
    try:
        import redis  # noqa: PLC0415 - optional dependency, imported lazily.

        client = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            db=int(os.environ.get("REDIS_DB", "0")),
            password=os.environ.get("REDIS_PASSWORD") or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001 - Redis is optional; degrade to no-cache.
        logger.debug("email provider-config cache disabled (no Redis): %s", exc)
        return None


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
    """Resolve the tenant's email dispatch config (Redis -> DB -> env/mock)."""
    client = _redis_client()
    key = cache_key(tenant_id)
    if client is not None:
        try:
            cached = client.get(key)
            if cached:
                return json.loads(cached)
        except Exception as exc:  # noqa: BLE001 - ignore cache read failures.
            logger.debug("provider-config cache read failed: %s", exc)

    config = _db_config(conn, tenant_id) or _env_config()

    if client is not None:
        try:
            client.setex(key, CACHE_TTL_SECONDS, json.dumps(config, default=str))
        except Exception as exc:  # noqa: BLE001 - ignore cache write failures.
            logger.debug("provider-config cache write failed: %s", exc)
    return config
