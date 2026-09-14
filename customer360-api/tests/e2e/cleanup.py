"""Sweep residual E2E artifacts for the configured tenant.

A normal run cleans up after itself (the ``track`` fixture + segment-delete
cascade). This is the safety net for a run that was killed mid-teardown: it
deletes every ``e2e-`` tagged segment (cascading its crm_segment_sync_runs rows)
and every ``E2E `` prefixed campaign for the tenant.

Reads the same E2E_* env as the suite (exported by test.sh). Invoke via
``CLEANUP=1 ./test.sh``, ``./test.sh --cleanup``, or ``python cleanup.py``.
"""

import os
import sys

import httpx

BASE = os.environ.get("E2E_BASE_URL", "").rstrip("/")
PREFIX = os.environ.get("E2E_API_PREFIX", "/api/v1")
VERIFY = os.environ.get("E2E_VERIFY_TLS", "true").strip().lower() not in ("false", "0", "no")
TIMEOUT = float(os.environ.get("E2E_TIMEOUT", "60"))


def _headers() -> dict:
    h = {}
    if os.environ.get("E2E_BEARER_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["E2E_BEARER_TOKEN"]
    if os.environ.get("E2E_TENANT_ID"):
        h["X-Tenant-Id"] = os.environ["E2E_TENANT_ID"]
    if os.environ.get("E2E_USER_ID"):
        h["X-User-Id"] = os.environ["E2E_USER_ID"]
    return h


def _sweep(client, path, id_field, should_delete) -> int:
    r = client.get(f"{PREFIX}{path}/", params={"page_size": 1000, "limit": 1000})
    if r.status_code != 200:
        print(f"  {path}: list HTTP {r.status_code} {r.text[:100]}")
        return 0
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", [])
    deleted = 0
    for item in items:
        if should_delete(item):
            d = client.delete(f"{PREFIX}{path}/{item[id_field]}")
            if d.status_code in (200, 204):
                deleted += 1
    return deleted


def main() -> int:
    if not BASE:
        print("E2E_BASE_URL not set")
        return 1
    with httpx.Client(base_url=BASE, headers=_headers(), timeout=TIMEOUT,
                      follow_redirects=True, verify=VERIFY) as client:
        segs = _sweep(client, "/segments", "segment_id",
                      lambda it: str(it.get("segment_tag", "")).startswith("e2e-"))
        camps = _sweep(client, "/campaigns", "campaign_id",
                       lambda it: str(it.get("name", "")).startswith("E2E "))
    print(f"cleanup: deleted {segs} e2e segment(s) (+cascaded sync-runs), {camps} E2E campaign(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
