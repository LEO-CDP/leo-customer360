"""Project verified marketing-consent revocations from S3 onto profile state.

The event API writes verified email/Zalo callback evidence to S3 and never
connects to Postgres. This backend projection applies only explicit opt-outs,
then removes the derived eligibility snapshot so the next evaluation recomputes it.
"""

import logging
from typing import Iterable
from uuid import UUID

from .db import DB_SCHEMA
from .rls import set_tenant_context

logger = logging.getLogger(__name__)

VERIFIED_CONSENT_SOURCE = "verified_consent_callback"
REVOCATION_CONTRACTS = {
    "email-unsubscribed": {
        "channel": "email",
        "field": "email_opt_in",
        "reason": "unsubscribe",
        "evidence_source": "verified_email_callback",
        "reason_key": "email_opt_out_reason",
        "source_key": "email_consent_source",
    },
    "zalo-opt-out": {
        "channel": "zalo",
        "field": "zalo_opt_in",
        "reason": "opt_out",
        "evidence_source": "verified_zalo_webhook",
        "reason_key": "zalo_opt_out_reason",
        "source_key": "zalo_consent_source",
    },
}


def apply_optout(
    conn,
    tenant_id: str,
    master_profile_id: str,
    *,
    field: str,
    reason_key: str,
    source_key: str,
    reason: str,
    source: str,
) -> bool:
    """Apply one allow-listed channel revocation and clear stale eligibility."""
    if field not in {"email_opt_in", "zalo_opt_in"}:
        raise ValueError("unsupported consent revocation field")
    if reason_key not in {"email_opt_out_reason", "zalo_opt_out_reason"}:
        raise ValueError("unsupported consent revocation reason field")
    if source_key not in {"email_consent_source", "zalo_consent_source"}:
        raise ValueError("unsupported consent source field")
    tenant_uuid = str(UUID(tenant_id))
    profile_uuid = str(UUID(master_profile_id))
    with conn.cursor() as cur:
        set_tenant_context(cur, tenant_uuid)
        cur.execute(
            f"""UPDATE {DB_SCHEMA}.cdp_master_profiles
                   SET communication_preferences =
                         (
                             COALESCE(communication_preferences, '{{}}'::jsonb)
                             - 'eligibility_policy'
                         )
                         || jsonb_build_object(
                             %s, false,
                             %s, %s,
                             %s, %s,
                             'consent_updated_at', now()
                         ),
                       updated_at = now()
                 WHERE tenant_id = %s AND master_profile_id = %s
                   AND (
                       communication_preferences IS NULL
                       OR jsonb_typeof(communication_preferences) = 'object'
                   )
                   AND (
                       communication_preferences -> %s IS DISTINCT FROM 'false'::jsonb
                       OR communication_preferences -> %s
                            IS DISTINCT FROM to_jsonb(%s::text)
                       OR communication_preferences ? 'eligibility_policy'
                   )""",
            (
                field,
                reason_key,
                reason,
                source_key,
                source,
                tenant_uuid,
                profile_uuid,
                field,
                reason_key,
                reason,
            ),
        )
        return cur.rowcount > 0


def project_optout_events(conn, events: Iterable[dict]) -> dict:
    """Apply verified email/Zalo opt-outs; ordinary delivery failures are not consent."""
    applied = skipped = 0
    for event in events:
        if not isinstance(event, dict) or event.get("source_system") != VERIFIED_CONSENT_SOURCE:
            logger.warning("Skipping consent event without verified source marker")
            skipped += 1
            continue
        props = event.get("properties")
        if not isinstance(props, dict):
            logger.warning("Skipping verified consent event with invalid properties")
            skipped += 1
            continue
        contract = REVOCATION_CONTRACTS.get(event.get("event_name"))
        update = props.get("consent_update")
        if not contract or not isinstance(update, dict):
            logger.warning(
                "Skipping unsupported consent revocation event_name=%r",
                event.get("event_name"),
            )
            skipped += 1
            continue
        if (
            props.get("tracking_channel") != contract["channel"]
            or update.get("field") != contract["field"]
            or update.get("granted") is not False
            or update.get("reason") != contract["reason"]
            or update.get("source") != contract["evidence_source"]
        ):
            logger.warning(
                "Skipping consent event with mismatched evidence event_name=%r",
                event.get("event_name"),
            )
            skipped += 1
            continue
        tenant_id = props.get("tenant_id")
        master_profile_id = props.get("master_profile_id")
        if not (tenant_id and master_profile_id):
            logger.warning(
                "Skipping consent event missing tenant/profile event_name=%r",
                event.get("event_name"),
            )
            skipped += 1
            continue
        try:
            changed = apply_optout(
                conn,
                tenant_id,
                master_profile_id,
                field=contract["field"],
                reason_key=contract["reason_key"],
                source_key=contract["source_key"],
                reason=contract["reason"],
                source=contract["evidence_source"],
            )
            conn.commit()
            if changed:
                applied += 1
            else:
                skipped += 1
        except Exception:  # one bad profile must not block independent revocations
            conn.rollback()
            logger.exception(
                "Failed to project verified consent revocation event_name=%s",
                event.get("event_name"),
            )
            skipped += 1
    return {"applied": applied, "skipped": skipped}


def read_optout_events(conn) -> list[dict]:
    """Read verified revocations for tenants with active email or Zalo connectors."""
    from .s3_reader import read_optout_events_for_tenants  # lazy: boto3 only at run time

    with conn.cursor() as cur:
        # Cross-tenant driver query (no tenant context) -- relies on the backend DB
        # role holding BYPASSRLS; without it RLS fails closed to 0 rows and the
        # consent projection silently no-ops.
        cur.execute(
            f"""SELECT tenant_id,
                       MAX(COALESCE((config->>'optout_lookback_hours')::integer, 6))
                   FROM {DB_SCHEMA}.crm_connector_config
                  WHERE status = 'ACTIVE' AND is_active = TRUE
                    AND (
                        connector_type = 'EMAIL'
                        OR (connector_type = 'CHAT' AND provider = 'ZALO')
                    )
                  GROUP BY tenant_id"""
        )
        rows = cur.fetchall()
        tenant_ids = [str(row[0]) for row in rows]
        lookback_hours = max((int(row[1]) for row in rows), default=6)
    if not tenant_ids:
        return []
    return read_optout_events_for_tenants(tenant_ids, lookback_hours=lookback_hours)


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
        {
            "event_name": "email-unsubscribed",
            "source_system": VERIFIED_CONSENT_SOURCE,
            "properties": {
                "tracking_channel": "email",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "master_profile_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "consent_update": {
                    "field": "email_opt_in",
                    "granted": False,
                    "reason": "unsubscribe",
                    "source": "verified_email_callback",
                },
            },
        },
        {
            "event_name": "zalo-failed",
            "source_system": VERIFIED_CONSENT_SOURCE,
            "properties": {
                "tracking_channel": "zalo",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "master_profile_id": "aaaaaaaa-0000-0000-0000-000000000002",
                "suppression_reason": "permanent_failure",
            },
        },
    ]
    summary = project_optout_events(_Conn(), events)
    assert summary == {"applied": 1, "skipped": 1}, summary
    print("optout_projection self-check OK:", summary)


if __name__ == "__main__":
    _demo()
