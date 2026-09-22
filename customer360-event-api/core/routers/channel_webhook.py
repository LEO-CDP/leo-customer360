"""Channel-agnostic engagement-webhook factory (email, Zalo, ...).

Every provider webhook is the same body: verify an HMAC over the raw payload,
decode the signed tracking token -> (tenant, campaign, profile), map the provider
event to a canonical name, and record it to the S3 event lake via the SAME
``TrackingLogService``. Only the event map, signing secret, signature header, and
channel label differ -- so they share this factory.

S3-first: opt-out is NOT written to Postgres here; it rides the S3 event as a
``suppression_reason`` that a downstream projection applies to profile consent.
Reuses ``decode_tracking_token`` (verifies the shared EMAIL_TRACKING_SECRET token).
"""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from core.config import settings
from core.webhook_security import verify_hmac_signature
from core.routers.email_tracking import decode_tracking_token
from core.routers.tracking import build_tracking_request, get_tracking_service, ingest_tracking_request
from core.service import TrackingLogService

logger = logging.getLogger(__name__)


def _source_id(tenant_id: str) -> uuid.UUID:
    """Use tenant UUIDs as S3 source partitions, with a stable test fallback."""
    try:
        return uuid.UUID(tenant_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"channel-tracking:{tenant_id}")


def record_channel_event(decoded, event_name, service, *, channel, dedup_key, payload=None) -> bool:
    """Record one engagement event to the S3 event lake via TrackingLogService.
    Returns True on success, False if persistence failed (caller decides retry)."""
    try:
        properties = {
            "tracking_channel": channel,
            "tenant_id": decoded["tenant_id"],
            "campaign_id": decoded["campaign_id"],
            "master_profile_id": decoded["master_profile_id"],
            "event_dedup_key": dedup_key,
            **(payload or {}),
        }
        request = build_tracking_request(
            data_source_id=_source_id(decoded["tenant_id"]),
            user_id=decoded["master_profile_id"],
            metadata={"source": channel, "campaign_id": decoded["campaign_id"]},
            events=[{"event_name": event_name, "properties": properties}],
        )
        ingest_tracking_request(request, service)
        return True
    except Exception:  # webhook responses must stay provider-safe
        logger.warning("failed to persist %s tracking event %s", channel, event_name, exc_info=True)
        return False


def make_webhook_router(*, prefix, secret_attr, event_map, suppress_reasons, channel,
                        sig_header, id_field="msg_id"):
    """Build a POST {prefix}/webhook router for one channel. See module docstring."""
    router = APIRouter(prefix=prefix, tags=[f"{channel.capitalize()} - Tracking"])

    @router.post("/webhook")
    async def webhook(
        request: Request,
        service: TrackingLogService = Depends(get_tracking_service),
        signature: Optional[str] = Header(None, alias=sig_header),
    ):
        secret = getattr(settings, secret_attr, "") or ""
        if not secret:
            return JSONResponse(
                {"status": "disabled", "reason": "webhook signing secret not configured"}, status_code=503
            )
        raw = await request.body()
        if not verify_hmac_signature(raw, signature, secret):
            return JSONResponse({"status": "rejected", "reason": "invalid signature"}, status_code=401)
        try:
            evt = json.loads(raw)
        except Exception:
            return JSONResponse({"status": "rejected", "reason": "invalid payload"}, status_code=400)

        provider_event = str(evt.get("event_name") or evt.get("event") or "").strip().lower()
        name = event_map.get(provider_event)
        decoded = decode_tracking_token(evt.get("tracking_id") or evt.get("token"))
        if not name or not decoded:
            return {"status": "ignored"}

        msg_id = evt.get(id_field) or evt.get("message_id")
        reason = suppress_reasons.get(name)
        # S3-first: opt-out carries suppression_reason on the S3 event; a
        # downstream projection op applies it to profile consent (no DB write here).
        ok = record_channel_event(
            decoded, name, service, channel=channel,
            dedup_key=f"{channel}:{msg_id}:{name}",
            payload={"provider_message_id": msg_id, "suppression_reason": reason},
        )
        # A dropped suppression event = consent lost (user messaged after opt-out),
        # so ask the provider to retry with a 503; non-suppression events stay best-effort.
        if reason and not ok:
            return JSONResponse(
                {"status": "retry", "reason": "failed to persist suppression event"},
                status_code=503,
            )
        return {"status": "ok", "event": name, "suppression_reason": reason}

    return router
