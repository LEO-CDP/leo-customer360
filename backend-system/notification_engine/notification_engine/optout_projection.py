"""Opt-out consent projection (S3-first).

The Zalo webhook records opt-out events to the S3 event lake with a
``suppression_reason`` -- it does NOT write DB state. This module is the ONLY
place opt-out is materialized onto profile consent: it applies each opt-out
event to ``cdp_master_profiles.communication_preferences`` (``zalo_opt_in`` =
false), the field the send eligibility query reads. Rebuildable by replaying the
S3 opt-out stream.

``project_optout_events`` is the pure, testable core. Wiring the S3 read (list
the tenant buckets' new ``zalo-opt-out`` NDJSON records) is the integration glue
-- see ``read_optout_events`` (the analytics code location already ships the
S3 reader; the same event stream feeds this projection).
"""

from typing import Iterable

from .db import DB_SCHEMA
from .rls import set_tenant_context


def apply_optout(conn, tenant_id, master_profile_id, reason) -> bool:
    """Set ``zalo_opt_in=false`` (+ reason) on one profile. Returns True if a row
    was updated. Caller owns the commit."""
    with conn.cursor() as cur:
        set_tenant_context(cur, str(tenant_id))
        cur.execute(
            f"""UPDATE {DB_SCHEMA}.cdp_master_profiles
                   SET communication_preferences =
                         COALESCE(communication_preferences, '{{}}'::jsonb)
                         || jsonb_build_object('zalo_opt_in', false, 'zalo_opt_out_reason', %s),
                       updated_at = now()
                 WHERE tenant_id = %s AND master_profile_id = %s""",
            (reason, str(tenant_id), str(master_profile_id)),
        )
        return cur.rowcount > 0


def project_optout_events(conn, events: Iterable[dict]) -> dict:
    """Apply a stream of decoded opt-out events onto profile consent.

    Each event carries ``tenant_id`` / ``master_profile_id`` / ``suppression_reason``
    (in ``properties`` or at top level). Commits per event so one bad row never
    rolls back the rest. Returns ``{'applied', 'skipped'}``.
    """
    applied = skipped = 0
    for event in events:
        props = event.get("properties") or event
        reason = props.get("suppression_reason")
        tenant_id = props.get("tenant_id")
        master_profile_id = props.get("master_profile_id")
        if not (reason and tenant_id and master_profile_id):
            skipped += 1
            continue
        try:
            changed = apply_optout(conn, tenant_id, master_profile_id, reason)
            conn.commit()
            if changed:
                applied += 1
            else:
                skipped += 1
        except Exception:  # noqa: BLE001 - one bad profile must not block the rest
            conn.rollback()
            skipped += 1
    return {"applied": applied, "skipped": skipped}


def read_optout_events(conn) -> list[dict]:
    """Read new ``zalo-opt-out`` / ``zalo-failed`` events from the S3 event lake
    for every tenant with a connected OA, ready to feed ``project_optout_events``."""
    from .s3_reader import read_optout_events_for_tenants  # lazy: boto3 only at run time

    with conn.cursor() as cur:
        # Cross-tenant driver query (no tenant context) -- relies on the backend DB
        # role holding BYPASSRLS; without it RLS fails closed to 0 rows and the
        # opt-out projection silently no-ops.
        cur.execute(f"SELECT DISTINCT tenant_id FROM {DB_SCHEMA}.sys_data_source WHERE slug = 'zalo-oa'")
        tenant_ids = [str(row[0]) for row in cur.fetchall()]
    if not tenant_ids:
        return []
    return read_optout_events_for_tenants(tenant_ids)


def _demo() -> None:
    """assert-based self-check of the pure projection logic (no DB/network)."""
    class _Cur:
        rowcount = 1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass

    class _Conn:
        commits = 0
        def cursor(self): return _Cur()
        def commit(self): self.__class__.commits += 1
        def rollback(self): pass

    events = [
        {"properties": {"tenant_id": "t1", "master_profile_id": "p1", "suppression_reason": "opt_out"}},
        {"properties": {"tenant_id": "t1", "suppression_reason": "opt_out"}},  # missing profile -> skipped
        {"event_name": "zalo-delivered", "properties": {"tenant_id": "t1", "master_profile_id": "p2"}},  # no reason
    ]
    summary = project_optout_events(_Conn(), events)
    assert summary == {"applied": 1, "skipped": 2}, summary
    print("optout_projection self-check OK:", summary)


if __name__ == "__main__":
    _demo()
