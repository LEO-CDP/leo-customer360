"""Zalo OA access-token refresh from the generic connector registry.

The rotating token is stored in ``crm_connector_config.credentials`` and the
tenant-specific OAuth endpoint/app credentials are stored alongside it.

⚠️ Zalo OAuth v4 refresh endpoint/params/header -- confirm against current docs.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from notification_engine.db import DB_SCHEMA, connect
from notification_engine.rls import set_tenant_context


def _refresh(refresh_token: str, app_id: str, app_secret: str, token_url: str) -> dict:
    body = urllib.parse.urlencode(
        {"refresh_token": refresh_token, "app_id": app_id, "grant_type": "refresh_token"}
    ).encode("utf-8")
    req = urllib.request.Request(
        token_url,
        data=body,
        headers={"secret_key": app_secret, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def refresh_due_tokens(conn, skew_seconds: int = 300, log=print) -> dict:
    """Refresh every active Zalo connector whose token expires within ``skew_seconds``.

    One bad OA (network/refused) is logged and skipped, never blocking the rest.
    Returns ``{'checked', 'refreshed', 'errors'}``.
    """
    with conn.cursor() as cur:
        # No tenant context here: this cross-tenant query relies on the backend DB
        # role holding BYPASSRLS. Without it, RLS fails closed -> 0 rows -> tokens
        # silently never refresh (checked=0 in the summary is the tell).
                cur.execute(
                        f"""SELECT tenant_id, connector_id, credentials, config
                                    FROM {DB_SCHEMA}.crm_connector_config
                                 WHERE connector_type = 'CHAT' AND provider = 'ZALO'
                                     AND direction IN ('OUTBOUND', 'BIDIRECTIONAL')
                                     AND status = 'ACTIVE' AND is_active = TRUE
                                     AND COALESCE(credentials->>'refresh_token', '') <> ''
                   AND (
                                                credentials->>'token_expires_at' IS NULL
                                                OR (credentials->>'token_expires_at')::timestamptz
                           < now() + make_interval(secs => %s)
                   )""",
            (skew_seconds,),
        )
        rows = cur.fetchall()

    checked, refreshed, errors = len(rows), 0, 0
    for tenant_id, connector_id, credentials, config in rows:
        credentials = credentials or {}
        config = config or {}
        try:
            data = _refresh(
                credentials["refresh_token"],
                credentials.get("app_id", ""),
                credentials.get("app_secret", ""),
                config.get("oa_token_url", "https://oauth.zaloapp.com/v4/oa/access_token"),
            )
            if not data.get("access_token"):
                errors += 1
                log(f"zalo token refresh: no access_token for connector={connector_id}: {data}")
                continue
            new = dict(credentials)
            new["access_token"] = data["access_token"]
            if data.get("refresh_token"):  # Zalo rotates the refresh token
                new["refresh_token"] = data["refresh_token"]
            try:
                expires_in = int(data.get("expires_in") or 3600)
            except (TypeError, ValueError):
                expires_in = 3600
            new["token_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            with conn.cursor() as cur:
                set_tenant_context(cur, str(tenant_id))
                cur.execute(
                    f"UPDATE {DB_SCHEMA}.crm_connector_config "
                    f"SET credentials = %s::jsonb, updated_at = now() WHERE connector_id = %s",
                    (json.dumps(new), connector_id),
                )
            conn.commit()
            refreshed += 1
        except Exception as exc:  # noqa: BLE001 - resilience: one OA must not block others
            conn.rollback()
            errors += 1
            log(f"zalo token refresh failed for connector={connector_id}: {exc}")

    return {"checked": checked, "refreshed": refreshed, "errors": errors}


if __name__ == "__main__":  # manual run: python -m notification_engine.token_refresh
    _conn = connect()
    try:
        print(refresh_due_tokens(_conn))
    finally:
        _conn.close()
