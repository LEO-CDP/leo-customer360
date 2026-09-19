"""ZNS template sync — reuses ``crm_message_templates`` (metadata.channel='zalo_zns').

ZNS templates are created + approved inside the Zalo OA platform; here we pull
the OA's template list from the Zalo Open API and upsert each as a
``crm_message_templates`` row (channel/id/params in ``metadata``/``variables``), so
the existing ``crm_campaign.template_id`` FK + Draft→Approved lifecycle work
unchanged. No new table.

⚠️ Zalo ``template/all`` endpoint/response shape — confirm against current docs.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.models.crm import MessageTemplate

logger = logging.getLogger(__name__)

ZALO_ZNS_CHANNEL = "zalo_zns"


def _fetch_zns_templates(access_token: str, offset: int = 0, limit: int = 100) -> list[dict]:
    """GET the OA's ZNS template list. ⚠️ endpoint/shape per current Zalo docs."""
    url = (
        f"{settings.crm_zalo_oa_api_base_url.rstrip('/')}/template/all"
        f"?{urllib.parse.urlencode({'offset': offset, 'limit': limit})}"
    )
    req = urllib.request.Request(url, headers={"access_token": access_token}, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.load(resp)
    if payload.get("error") not in (0, None):  # Zalo: {"error":0,"data":[...]}
        raise RuntimeError(f"Zalo template/all error: {payload}")
    return payload.get("data") or []


def _map_status(remote: dict) -> str:
    """Map a Zalo template status onto the crm_message_templates lifecycle."""
    raw = str(remote.get("status", "")).lower()
    return "Approved" if raw in ("enable", "approved", "1", "true") else "Draft"


def sync_templates(db: Session, tenant_id: uuid.UUID, oa_id: Optional[str], access_token: str) -> dict:
    """Pull the OA's ZNS templates and upsert them (matched on the Zalo template id).

    Returns ``{'synced': n}``. Small template counts, so we load the tenant's
    existing ZNS rows once and match in Python (avoids a JSONB index dependency).
    """
    remote = _fetch_zns_templates(access_token)
    existing_rows = db.execute(
        select(MessageTemplate).where(MessageTemplate.tenant_id == tenant_id)
    ).scalars().all()
    by_zid = {
        (t.metadata_ or {}).get("zalo_template_id"): t
        for t in existing_rows
        if (t.metadata_ or {}).get("channel") == ZALO_ZNS_CHANNEL
    }

    synced = 0
    for tpl in remote:
        zid = str(tpl.get("templateId") or tpl.get("template_id") or "").strip()
        if not zid:
            continue
        meta = {
            "channel": ZALO_ZNS_CHANNEL,
            "zalo_template_id": zid,
            "oa_id": oa_id,
            "quality": tpl.get("templateQuality"),
            "category": tpl.get("templateTag"),
            "preview": tpl.get("previewUrl"),
        }
        variables = {"params": tpl.get("listParams") or tpl.get("params") or []}
        name = tpl.get("templateName") or f"ZNS {zid}"
        status = _map_status(tpl)

        row = by_zid.get(zid)
        if row is None:
            db.add(MessageTemplate(
                tenant_id=tenant_id, name=name, status=status, variables=variables, metadata_=meta
            ))
        else:
            row.name = name
            row.status = status
            row.variables = variables
            row.metadata_ = {**(row.metadata_ or {}), **meta}
        synced += 1

    db.commit()
    return {"synced": synced}


def list_templates(db: Session, tenant_id: uuid.UUID) -> list[MessageTemplate]:
    """The tenant's synced ZNS templates (channel='zalo_zns'), newest first."""
    rows = db.execute(
        select(MessageTemplate)
        .where(MessageTemplate.tenant_id == tenant_id)
        .order_by(MessageTemplate.updated_at.desc())
    ).scalars().all()
    return [t for t in rows if (t.metadata_ or {}).get("channel") == ZALO_ZNS_CHANNEL]


def get_template(db: Session, tenant_id: uuid.UUID, template_id: uuid.UUID) -> Optional[MessageTemplate]:
    """One ZNS template by id, scoped to the tenant + channel."""
    row = db.get(MessageTemplate, template_id)
    if row is None or row.tenant_id != tenant_id or (row.metadata_ or {}).get("channel") != ZALO_ZNS_CHANNEL:
        return None
    return row
