"""Segment-ID driven CRM sync engine.

Resolves the members of one ``cdp_segments`` row and routes each matched
``cdp_master_profiles`` record into the correct ``crm_*`` tables by
``lifecycle_stage``:

* **Route A -- customer** (``lifecycle_stage = 'customer'``):
  ``crm_customer_contacts`` (from the profile's own engagement signals) and
  ``crm_transactions`` (from available transaction facts -- never fabricated).
* **Route B -- lead** (``lifecycle_stage = 'lead'``): ``crm_lead_source``
  (from ``acquisition_source``, or a fallback source) and ``crm_lead`` with
  the ``lead_source_id`` relation populated.
* **Route C -- contact** (every other lifecycle stage): ``crm_contact``.

Idempotency is structural, not transactional: every target row's primary key
is a deterministic ``uuid5`` derived from ``(tenant_id, target, natural key)``
(``crm_transactions`` additionally rides its ``ux_crm_transactions_tenant_source``
partial unique index), so re-running the same segment ``ON CONFLICT DO UPDATE``s
the same rows instead of duplicating them.

Every execution is audited in ``crm_segment_sync_runs`` with per-route counts.
The membership recompute reuses ``core.crud.segmentation.recompute_segment_membership``
so this engine never re-implements segment matching.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from core.crud.segmentation import DOMAIN_ATTRIBUTES_JOIN_SQL, recompute_segment_membership
from core.models.crm import SegmentSyncRun
from core.utils.sql_safety import validate_sql_where_fragment

logger = logging.getLogger(__name__)

# Stable namespace for all deterministic sync primary keys. Derived once from a
# fixed string so it never changes across runs/processes (a literal would do the
# same; uuid5-of-a-name keeps the intent self-documenting).
CRM_SYNC_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "leocdp.io/crm/segment-sync")

# Routing rule sets -- kept as explicit constants so the "match requested
# behavior exactly" acceptance criterion is auditable in one place. Everything
# not a customer and not a lead is a contact (Route C).
CUSTOMER_LIFECYCLE_STAGES = frozenset({"customer"})
LEAD_LIFECYCLE_STAGES = frozenset({"lead"})

ROUTE_CUSTOMER = "customer"
ROUTE_LEAD = "lead"
ROUTE_CONTACT = "contact"

# Fallback ``crm_lead_source.name`` when a lead profile has no acquisition_source.
DEFAULT_LEAD_SOURCE_NAME = "segment_sync"

# Contact-log marker written by this engine into crm_customer_contacts.contact_type.
CUSTOMER_CONTACT_TYPE = "segment_sync"


def classify_route(lifecycle_stage: Optional[str]) -> str:
    """Map a profile's ``lifecycle_stage`` to its sync route (Route A/B/C)."""
    stage = (lifecycle_stage or "").strip().lower()
    if stage in CUSTOMER_LIFECYCLE_STAGES:
        return ROUTE_CUSTOMER
    if stage in LEAD_LIFECYCLE_STAGES:
        return ROUTE_LEAD
    return ROUTE_CONTACT


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    text_value = str(value).strip()
    return uuid.UUID(text_value) if text_value else None


def _deterministic_id(*parts: Any) -> uuid.UUID:
    """Stable uuid5 primary key from the given natural-key parts."""
    return uuid.uuid5(CRM_SYNC_NAMESPACE, "|".join(str(p) for p in parts))


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _has_identity(profile: dict) -> bool:
    """A lead/contact row is only meaningful with at least one identity field."""
    return any(
        _clean(profile.get(field))
        for field in ("email", "phone_number", "first_name", "last_name", "full_name")
    )


def _split_name(profile: dict) -> tuple[Optional[str], Optional[str]]:
    """Map profile name fields to (first_name, last_name), falling back to
    splitting full_name when the discrete fields are absent."""
    first = _clean(profile.get("first_name"))
    last = _clean(profile.get("last_name"))
    if first or last:
        return first, last
    full = _clean(profile.get("full_name"))
    if not full:
        return None, None
    head, _, tail = full.partition(" ")
    return head or None, tail.strip() or None


def _customer_contact_eligible(profile: dict) -> bool:
    """Only build a crm_customer_contacts row when the profile actually carries
    an interaction signal -- never invent an empty contact log."""
    return (
        _clean(profile.get("preferred_channel")) is not None
        or profile.get("last_activity_at") is not None
        or profile.get("engagement_score") is not None
    )


def _transaction_facts(profile: dict) -> list[dict]:
    """Extract available transaction facts from ``attributes.transactions``.

    Returns the list of well-formed fact dicts (never fabricates one). When the
    profile carries no transaction source data, returns ``[]`` so the customer
    route writes zero transactions rather than inventing amounts.
    """
    attributes = profile.get("attributes")
    if not isinstance(attributes, dict):
        return []
    facts = attributes.get("transactions")
    if not isinstance(facts, list):
        return []
    return [fact for fact in facts if isinstance(fact, dict)]


# ---------------------------------------------------------------------------
# Member resolution
# ---------------------------------------------------------------------------

_MEMBER_COLUMNS = """
    cdp_master_profiles.master_profile_id,
    cdp_master_profiles.email,
    cdp_master_profiles.phone_number,
    cdp_master_profiles.first_name,
    cdp_master_profiles.last_name,
    cdp_master_profiles.full_name,
    cdp_master_profiles.lifecycle_stage,
    cdp_master_profiles.acquisition_source,
    cdp_master_profiles.preferred_channel,
    cdp_master_profiles.last_activity_at,
    cdp_master_profiles.engagement_score,
    cdp_master_profiles.persona_summary,
    cdp_master_profiles.attributes
"""


def _iter_segment_members(
    db: Session, tenant_id: str, where_fragment: str, batch_size: int
) -> Iterator[dict]:
    """Yield matching active profiles for the segment in keyset-paginated
    batches (ordered by master_profile_id) so a large segment never loads its
    whole membership at once. Mirrors the matched-profiles read path
    (same tenant scope, status_code=1 filter and cdp_domain_profiles join)."""
    schema = settings.db_schema
    join_sql = DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=schema)
    last_id = "00000000-0000-0000-0000-000000000000"
    stmt = text(
        f"""
        SELECT {_MEMBER_COLUMNS}
        FROM {schema}.cdp_master_profiles
        {join_sql}
        WHERE tenant_id = :tenant_id
          AND status_code = 1
          AND cdp_master_profiles.master_profile_id > CAST(:last_id AS uuid)
          AND ({where_fragment})
        ORDER BY cdp_master_profiles.master_profile_id
        LIMIT :limit
        """
    )
    while True:
        rows = db.execute(
            stmt, {"tenant_id": tenant_id, "last_id": last_id, "limit": batch_size}
        ).mappings().all()
        if not rows:
            return
        for row in rows:
            yield dict(row)
        if len(rows) < batch_size:
            return
        last_id = str(rows[-1]["master_profile_id"])


# ---------------------------------------------------------------------------
# Idempotent upserts (deterministic-PK ON CONFLICT DO UPDATE)
# ---------------------------------------------------------------------------


def _upsert_lead_source(db: Session, schema: str, tenant_id: str, source_name: str) -> uuid.UUID:
    lead_source_id = _deterministic_id(tenant_id, "lead_source", source_name.strip().lower())
    db.execute(
        text(
            f"""
            INSERT INTO {schema}.crm_lead_source (lead_source_id, tenant_id, name, description)
            VALUES (:id, :tenant_id, :name, :description)
            ON CONFLICT (lead_source_id) DO UPDATE SET name = EXCLUDED.name
            """
        ),
        {
            "id": str(lead_source_id),
            "tenant_id": tenant_id,
            "name": source_name,
            "description": "Auto-created from cdp_master_profiles.acquisition_source by segment CRM sync.",
        },
    )
    return lead_source_id


def _upsert_lead(
    db: Session, schema: str, tenant_id: str, profile: dict, lead_source_id: uuid.UUID, segment_id: str
) -> None:
    master_profile_id = str(profile["master_profile_id"])
    lead_id = _deterministic_id(tenant_id, "lead", master_profile_id)
    first_name, last_name = _split_name(profile)
    db.execute(
        text(
            f"""
            INSERT INTO {schema}.crm_lead
                (lead_id, tenant_id, lead_source_id, first_name, last_name, email, phone, metadata)
            VALUES
                (:id, :tenant_id, :lead_source_id, :first_name, :last_name, :email, :phone,
                 CAST(:metadata AS jsonb))
            ON CONFLICT (lead_id) DO UPDATE SET
                lead_source_id = EXCLUDED.lead_source_id,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                metadata = EXCLUDED.metadata
            """
        ),
        {
            "id": str(lead_id),
            "tenant_id": tenant_id,
            "lead_source_id": str(lead_source_id),
            "first_name": first_name,
            "last_name": last_name,
            "email": _clean(profile.get("email")),
            "phone": _clean(profile.get("phone_number")),
            "metadata": _link_metadata(master_profile_id, segment_id),
        },
    )


def _upsert_contact(db: Session, schema: str, tenant_id: str, profile: dict, segment_id: str) -> None:
    master_profile_id = str(profile["master_profile_id"])
    contact_id = _deterministic_id(tenant_id, "contact", master_profile_id)
    first_name, last_name = _split_name(profile)
    db.execute(
        text(
            f"""
            INSERT INTO {schema}.crm_contact
                (contact_id, tenant_id, first_name, last_name, email, phone, metadata)
            VALUES
                (:id, :tenant_id, :first_name, :last_name, :email, :phone, CAST(:metadata AS jsonb))
            ON CONFLICT (contact_id) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                metadata = EXCLUDED.metadata
            """
        ),
        {
            "id": str(contact_id),
            "tenant_id": tenant_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": _clean(profile.get("email")),
            "phone": _clean(profile.get("phone_number")),
            "metadata": _link_metadata(master_profile_id, segment_id),
        },
    )


def _upsert_customer_contact(db: Session, schema: str, tenant_id: str, profile: dict, segment_id: str) -> int:
    """Build/update one interaction-log row for a customer profile from its own
    engagement signals. Returns 1 if written, 0 if the profile had no eligible
    signal. Idempotent per (segment, profile)."""
    if not _customer_contact_eligible(profile):
        return 0
    master_profile_id = str(profile["master_profile_id"])
    contact_id = _deterministic_id(tenant_id, "customer_contact", segment_id, master_profile_id)
    channel = _clean(profile.get("preferred_channel"))
    summary = _clean(profile.get("persona_summary"))
    engagement = profile.get("engagement_score")
    if summary is None:
        summary = (
            f"Engagement score {engagement}" if engagement is not None else "Synced from segment membership."
        )
    contact_date = profile.get("last_activity_at") or datetime.now(timezone.utc)
    db.execute(
        text(
            f"""
            INSERT INTO {schema}.crm_customer_contacts
                (contact_id, tenant_id, master_profile_id, contact_type, contact_channel,
                 contact_content, contact_date)
            VALUES
                (:id, :tenant_id, :master_profile_id, :contact_type, :channel, :content, :contact_date)
            ON CONFLICT (contact_id) DO UPDATE SET
                contact_channel = EXCLUDED.contact_channel,
                contact_content = EXCLUDED.contact_content,
                contact_date = EXCLUDED.contact_date
            """
        ),
        {
            "id": str(contact_id),
            "tenant_id": tenant_id,
            "master_profile_id": master_profile_id,
            "contact_type": CUSTOMER_CONTACT_TYPE,
            "channel": channel,
            "content": summary,
            "contact_date": contact_date,
        },
    )
    return 1


def _upsert_transactions(db: Session, schema: str, tenant_id: str, profile: dict) -> int:
    """Materialize available transaction facts into crm_transactions, deduped by
    the ``ux_crm_transactions_tenant_source`` partial unique index. Amounts are
    taken verbatim from the source fact (``None`` stays ``None``) -- never
    fabricated. Returns the number of facts upserted."""
    master_profile_id = str(profile["master_profile_id"])
    written = 0
    for index, fact in enumerate(_transaction_facts(profile)):
        source_system = _clean(fact.get("source_system")) or "segment_sync"
        # ponytail: positional fallback for facts with no source_transaction_id.
        # Re-sync stays idempotent only while the source facts keep their order;
        # reordering id-less facts would create new rows. Facts carrying a real
        # source_transaction_id are always stable.
        source_transaction_id = _clean(fact.get("source_transaction_id")) or f"{master_profile_id}:{index}"
        transaction_id = _deterministic_id(
            tenant_id, "transaction", source_system, source_transaction_id
        )
        db.execute(
            text(
                f"""
                INSERT INTO {schema}.crm_transactions
                    (transaction_id, tenant_id, master_profile_id, source_system, source_transaction_id,
                     transaction_type, transaction_status, entity_type, entity_name, amount, currency,
                     channel, transaction_time)
                VALUES
                    (:id, :tenant_id, :master_profile_id, :source_system, :source_transaction_id,
                     :transaction_type, :transaction_status, :entity_type, :entity_name, :amount, :currency,
                     :channel, :transaction_time)
                ON CONFLICT (tenant_id, source_system, source_transaction_id)
                    WHERE source_transaction_id IS NOT NULL
                DO UPDATE SET
                    master_profile_id = EXCLUDED.master_profile_id,
                    transaction_type = EXCLUDED.transaction_type,
                    transaction_status = EXCLUDED.transaction_status,
                    entity_type = EXCLUDED.entity_type,
                    entity_name = EXCLUDED.entity_name,
                    amount = EXCLUDED.amount,
                    currency = EXCLUDED.currency,
                    channel = EXCLUDED.channel,
                    transaction_time = EXCLUDED.transaction_time
                """
            ),
            {
                "id": str(transaction_id),
                "tenant_id": tenant_id,
                "master_profile_id": master_profile_id,
                "source_system": source_system,
                "source_transaction_id": source_transaction_id,
                "transaction_type": _clean(fact.get("transaction_type")),
                "transaction_status": _clean(fact.get("transaction_status")),
                "entity_type": _clean(fact.get("entity_type")),
                "entity_name": _clean(fact.get("entity_name")),
                "amount": fact.get("amount"),
                "currency": _clean(fact.get("currency")),
                "channel": _clean(fact.get("channel")),
                "transaction_time": fact.get("transaction_time"),
            },
        )
        written += 1
    return written


def _link_metadata(master_profile_id: str, segment_id: str) -> str:
    return json.dumps(
        {
            "linked_master_profile_id": master_profile_id,
            "synced_from_segment_id": segment_id,
            "source": "segment_crm_sync",
        }
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def sync_segment_to_crm(
    db: Session,
    segment: Any,
    *,
    tenant_id: Any,
    triggered_by: Any = None,
    dry_run: bool = False,
    batch_size: Optional[int] = None,
) -> dict:
    """Recompute one segment, then route + upsert its members into crm_* tables.

    Returns a result dict (see ``core.schemas.crm.SegmentCrmSyncResponse``) and
    persists a ``crm_segment_sync_runs`` audit row. Raises ``ValueError`` for a
    segment with no / unsafe ``sql_rules`` (callers should translate to HTTP
    400). Idempotent: re-running the same segment updates the same target rows
    instead of duplicating them.
    """
    if not getattr(segment, "sql_rules", None):
        raise ValueError("Segment has no sql_rules to sync")
    where_fragment = validate_sql_where_fragment(segment.sql_rules)

    schema = settings.db_schema
    batch_size = batch_size or settings.crm_sync_batch_size
    tenant_str = str(tenant_id)
    segment_str = str(segment.segment_id)

    # ponytail: no lock guards two concurrent syncs of the same segment. They
    # converge (deterministic PKs + ON CONFLICT) but can race on recompute /
    # ON CONFLICT; add a pg_advisory_xact_lock on (tenant_id, segment_id) here
    # if one segment ever syncs concurrently.
    recomputed = False

    counts = {"matched": 0, "customer": 0, "lead": 0, "contact": 0, "skipped": 0, "error": 0}
    detail = {
        "customer_contacts_written": 0,
        "transactions_written": 0,
        "leads_written": 0,
        "lead_sources_written": 0,
        "contacts_written": 0,
    }
    lead_source_ids_seen: set[uuid.UUID] = set()
    first_error: Optional[str] = None

    def _build_run(status: str, error_message: Optional[str]) -> SegmentSyncRun:
        now = datetime.now(timezone.utc)
        return SegmentSyncRun(
            sync_run_id=uuid.uuid4(),
            tenant_id=_as_uuid(tenant_id),
            segment_id=_as_uuid(segment.segment_id),
            triggered_by=_as_uuid(triggered_by),
            status=status,
            dry_run=dry_run,
            matched_count=counts["matched"],
            customer_count=counts["customer"],
            lead_count=counts["lead"],
            contact_count=counts["contact"],
            skipped_count=counts["skipped"],
            error_count=counts["error"],
            error_message=error_message,
            started_at=now,
            finished_at=now,
            metadata_={
                **detail,
                "recomputed": recomputed,
                "batch_size": batch_size,
                "customer_stages": sorted(CUSTOMER_LIFECYCLE_STAGES),
                "lead_stages": sorted(LEAD_LIFECYCLE_STAGES),
            },
        )

    try:
        # Recompute membership/segment_tag before resolving members (skipped in
        # dry-run to stay side-effect free). Inside the try so a recompute
        # failure is audited as a Failed run instead of a silent 500.
        if not dry_run:
            recompute_segment_membership(db, segment)
            recomputed = True

        # ponytail: the whole segment syncs in ONE transaction -- batch_size
        # bounds the SELECT fetch (keyset pagination), not the write/lock
        # footprint, which spans every member until the final commit. Fine at
        # current scale; for very large segments commit per batch (idempotent
        # PKs let a resumed re-run converge) or checkpoint the run.
        for profile in _iter_segment_members(db, tenant_str, where_fragment, batch_size):
            counts["matched"] += 1
            route = classify_route(profile.get("lifecycle_stage"))
            try:
                # Per-member write tallies stay local until the savepoint commits
                # cleanly, then fold into `detail` -- so a member whose upsert
                # raises (and rolls back to the savepoint) never inflates the
                # audited write counts.
                cc_written = tx_written = leads_written = contacts_written = 0
                new_lead_source: Optional[uuid.UUID] = None
                with db.begin_nested():
                    if route == ROUTE_CUSTOMER:
                        counts["customer"] += 1
                        if dry_run:
                            cc_written = 1 if _customer_contact_eligible(profile) else 0
                            tx_written = len(_transaction_facts(profile))
                        else:
                            cc_written = _upsert_customer_contact(db, schema, tenant_str, profile, segment_str)
                            tx_written = _upsert_transactions(db, schema, tenant_str, profile)
                    elif route == ROUTE_LEAD:
                        if not _has_identity(profile):
                            counts["skipped"] += 1
                        else:
                            counts["lead"] += 1
                            source_name = _clean(profile.get("acquisition_source")) or DEFAULT_LEAD_SOURCE_NAME
                            if dry_run:
                                leads_written = 1
                            else:
                                new_lead_source = _upsert_lead_source(db, schema, tenant_str, source_name)
                                _upsert_lead(db, schema, tenant_str, profile, new_lead_source, segment_str)
                                leads_written = 1
                    else:  # ROUTE_CONTACT
                        if not _has_identity(profile):
                            counts["skipped"] += 1
                        else:
                            counts["contact"] += 1
                            if dry_run:
                                contacts_written = 1
                            else:
                                _upsert_contact(db, schema, tenant_str, profile, segment_str)
                                contacts_written = 1
                # Savepoint committed: only now count these rows as written.
                detail["customer_contacts_written"] += cc_written
                detail["transactions_written"] += tx_written
                detail["leads_written"] += leads_written
                detail["contacts_written"] += contacts_written
                if new_lead_source is not None and new_lead_source not in lead_source_ids_seen:
                    lead_source_ids_seen.add(new_lead_source)
                    detail["lead_sources_written"] += 1
            except Exception:
                counts["error"] += 1
                logger.warning(
                    "crm sync: failed to route/upsert profile %s (segment %s)",
                    profile.get("master_profile_id"),
                    segment_str,
                    exc_info=True,
                )
                if first_error is None:
                    first_error = f"profile {profile.get('master_profile_id')}: routing/upsert error"

        run = _build_run("Completed", first_error)
        db.add(run)
        db.commit()
    except Exception as exc:
        # Catastrophic (non-per-member) failure: discard partial writes, then
        # audit the failure in its own committed row so the run is never silent.
        db.rollback()
        run = _build_run("Failed", str(exc)[:1000])
        db.add(run)
        db.commit()
        raise

    return {
        "sync_run_id": run.sync_run_id,
        "segment_id": _as_uuid(segment.segment_id),
        "tenant_id": _as_uuid(tenant_id),
        "status": run.status,
        "dry_run": dry_run,
        "route_counts": counts,
        "detail": run.metadata_,
        "message": _result_message(counts, dry_run),
    }


def _result_message(counts: dict, dry_run: bool) -> str:
    prefix = "Dry-run: would sync" if dry_run else "Synced"
    return (
        f"{prefix} {counts['matched']} member(s) -> "
        f"{counts['customer']} customer / {counts['lead']} lead / {counts['contact']} contact"
        f" ({counts['skipped']} skipped, {counts['error']} errored)."
    )
