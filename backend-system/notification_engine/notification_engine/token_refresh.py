"""Zalo OA access-token refresh.

OA config/tokens live per-tenant in ``sys_data_source`` (slug='zalo-oa'); the
rotating token is inside the ``access_tokens`` JSONB. This refreshes tokens that
are at/near expiry and writes the rotated token back (Zalo rotates the refresh
token too). Processes all tenants on one connection (like identity_resolution) --
the backend-system DB role bypasses RLS; per-row tenant context is still set.

⚠️ Zalo OAuth v4 refresh endpoint/params/header -- confirm against current docs.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from notification_engine.config import ZALO_APP_ID, ZALO_APP_SECRET, ZALO_TOKEN_URL
from notification_engine.db import DB_SCHEMA, connect
from notification_engine.rls import set_tenant_context


def _refresh(refresh_token: str) -> dict:
    body = urllib.parse.urlencode(
        {"refresh_token": refresh_token, "app_id": ZALO_APP_ID, "grant_type": "refresh_token"}
    ).encode("utf-8")
    req = urllib.request.Request(
        ZALO_TOKEN_URL,
        data=body,
        headers={"secret_key": ZALO_APP_SECRET, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def refresh_due_tokens(conn, skew_seconds: int = 300, log=print) -> dict:
    """Refresh every ``zalo-oa`` row whose token expires within ``skew_seconds``.

    One bad OA (network/refused) is logged and skipped, never blocking the rest.
    Returns ``{'checked', 'refreshed', 'errors'}``.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT tenant_id, data_source_id, access_tokens
                  FROM {DB_SCHEMA}.sys_data_source
                 WHERE slug = 'zalo-oa' AND status = 1
                   AND COALESCE(access_tokens->>'refresh_token', '') <> ''
                   AND (
                        access_tokens->>'token_expires_at' IS NULL
                        OR (access_tokens->>'token_expires_at')::timestamptz
                           < now() + make_interval(secs => %s)
                   )""",
            (skew_seconds,),
        )
        rows = cur.fetchall()

    checked, refreshed, errors = len(rows), 0, 0
    for tenant_id, data_source_id, tokens in rows:
        tokens = tokens or {}
        try:
            data = _refresh(tokens["refresh_token"])
            if not data.get("access_token"):
                errors += 1
                log(f"zalo token refresh: no access_token for data_source={data_source_id}: {data}")
                continue
            new = dict(tokens)
            new["access_token"] = data["access_token"]
            if data.get("refresh_token"):  # Zalo rotates the refresh token
                new["refresh_token"] = data["refresh_token"]
            expires_in = int(data.get("expires_in", 3600))
            new["token_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            with conn.cursor() as cur:
                set_tenant_context(cur, str(tenant_id))
                cur.execute(
                    f"UPDATE {DB_SCHEMA}.sys_data_source "
                    f"SET access_tokens = %s::jsonb, updated_at = now() WHERE data_source_id = %s",
                    (json.dumps(new), data_source_id),
                )
            conn.commit()
            refreshed += 1
        except Exception as exc:  # noqa: BLE001 - resilience: one OA must not block others
            conn.rollback()
            errors += 1
            log(f"zalo token refresh failed for data_source={data_source_id}: {exc}")

    return {"checked": checked, "refreshed": refreshed, "errors": errors}


if __name__ == "__main__":  # manual run: python -m notification_engine.token_refresh
    _conn = connect()
    try:
        print(refresh_due_tokens(_conn))
    finally:
        _conn.close()
