"""Public email tracking routes owned by the data-tracking service.

Email clients and provider webhooks cannot authenticate with the platform API.
The signed tracking token supplies tenant, campaign, and profile correlation.
Every accepted event is sent through the same TrackingLogService used by the
web SDK, so the durable source of truth is the S3 tracking stream consumed by
Dagster rather than the customer360 API database.
"""

import base64
import hashlib
import hmac
import logging
import uuid
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from core.config import settings
from core.webhook_security import verify_hmac_signature
from core.routers.tracking import (
    build_tracking_request,
    get_tracking_service,
    ingest_tracking_request,
)
from core.service import TrackingLogService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/track/email", tags=["Email - Tracking"])

_SIG_LEN = 20
_TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate, private"}

WEBHOOK_EVENT_TO_NAME = {
    "delivered": "email-delivered",
    "delivery": "email-delivered",
    "bounce": "email-bounced",
    "bounced": "email-bounced",
    "complaint": "email-complained",
    "complained": "email-complained",
    "spam": "email-complained",
    "open": "email-opened",
    "opened": "email-opened",
    "click": "email-clicked",
    "clicked": "email-clicked",
    "unsubscribe": "email-unsubscribed",
    "unsubscribed": "email-unsubscribed",
}
SUPPRESSION_EVENTS = {
    "email-complained": "complaint",
    "email-unsubscribed": "unsubscribe",
    "email-bounced": "hard_bounce",
}


class EmailWebhookEvent(BaseModel):
    """Normalized provider callback carried into the S3 event envelope."""

    token: str = Field(..., description="Signed tracking token echoed by the provider.")
    event: str = Field(..., description="Provider event such as bounce or complaint.")
    email: Optional[str] = None
    message_id: Optional[str] = None
    bounce_type: Optional[str] = None
    timestamp: Optional[str] = None


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()[:_SIG_LEN]


def decode_tracking_token(token: Optional[str]) -> Optional[dict[str, str]]:
    """Decode and verify the token minted by backend-system email_engine."""
    if not token or not settings.email_tracking_secret:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        tenant_id, campaign_id, master_profile_id, signature = decoded.split("|")
    except (UnicodeDecodeError, ValueError, TypeError):
        return None

    raw = f"{tenant_id}|{campaign_id}|{master_profile_id}"
    if not hmac.compare_digest(signature, _sign(raw, settings.email_tracking_secret)):
        return None
    return {
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
        "master_profile_id": master_profile_id,
    }


def verify_click_url(url: Optional[str], signature: Optional[str]) -> bool:
    if not url or not signature or not settings.email_tracking_secret:
        return False
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    return hmac.compare_digest(signature, _sign(url, settings.email_tracking_secret))


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    return verify_hmac_signature(raw_body, signature, settings.email_webhook_signing_secret)


def _source_id(tenant_id: str) -> uuid.UUID:
    """Use tenant UUIDs as S3 source partitions, with a stable test fallback."""
    try:
        return uuid.UUID(tenant_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"email-tracking:{tenant_id}")


def _record_event(
    decoded: dict[str, str],
    event_name: str,
    service: TrackingLogService,
    *,
    payload: Optional[dict] = None,
    dedup_key: Optional[str] = None,
) -> None:
    try:
        properties = {
            "tracking_channel": "email",
            "tenant_id": decoded["tenant_id"],
            "campaign_id": decoded["campaign_id"],
            "master_profile_id": decoded["master_profile_id"],
            "event_dedup_key": dedup_key
            or f"{decoded['campaign_id']}:{decoded['master_profile_id']}:{event_name}",
            **(payload or {}),
        }
        event = {
            "event_name": event_name,
            "properties": properties,
        }
        if properties.get("url"):
            event["page_url"] = properties["url"]
        request = build_tracking_request(
            data_source_id=_source_id(decoded["tenant_id"]),
            user_id=decoded["master_profile_id"],
            metadata={"source": "email", "campaign_id": decoded["campaign_id"]},
            events=[event],
        )
        ingest_tracking_request(request, service)
    except Exception:  # Tracking responses must remain mail-client safe.
        logger.warning("failed to persist email tracking event %s", event_name, exc_info=True)


@router.get("/open")
def track_open(
    u: Optional[str] = Query(None, description="Signed tracking token."),
    service: TrackingLogService = Depends(get_tracking_service),
):
    decoded = decode_tracking_token(u)
    if decoded:
        _record_event(decoded, "email-opened", service)
    return Response(content=_TRANSPARENT_GIF, media_type="image/gif", headers=_NO_STORE)


@router.get("/click")
def track_click(
    u: Optional[str] = Query(None),
    url: Optional[str] = Query(None),
    k: Optional[str] = Query(None),
    service: TrackingLogService = Depends(get_tracking_service),
):
    decoded = decode_tracking_token(u)
    trusted_url = verify_click_url(url, k)
    if decoded:
        _record_event(decoded, "email-clicked", service, payload={"url": url if trusted_url else None})
    return RedirectResponse(url if trusted_url else "/", status_code=302, headers=_NO_STORE)


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(
    u: str = Query(...),
    service: TrackingLogService = Depends(get_tracking_service),
):
    decoded = decode_tracking_token(u)
    if not decoded:
        return HTMLResponse(
            "<html><body><p>This unsubscribe link is invalid or expired.</p></body></html>",
            status_code=400,
        )
    _record_event(decoded, "email-unsubscribed", service, payload={"suppression_reason": "unsubscribe"})
    return HTMLResponse("<html><body><p>You have been unsubscribed. We're sorry to see you go.</p></body></html>")


@router.post("/webhook")
async def email_webhook(
    request: Request,
    provider: str = Query("generic"),
    x_webhook_signature: Optional[str] = Header(None),
    service: TrackingLogService = Depends(get_tracking_service),
):
    if not settings.email_webhook_signing_secret:
        return JSONResponse(
            {"status": "disabled", "reason": "webhook signing secret not configured"},
            status_code=503,
        )
    raw = await request.body()
    if not verify_webhook_signature(raw, x_webhook_signature):
        return JSONResponse({"status": "rejected", "reason": "invalid signature"}, status_code=401)
    try:
        payload = EmailWebhookEvent.model_validate_json(raw)
    except Exception:
        return JSONResponse({"status": "rejected", "reason": "invalid payload"}, status_code=400)

    decoded = decode_tracking_token(payload.token)
    if not decoded:
        return {"status": "ignored", "reason": "missing or invalid token"}
    event_name = WEBHOOK_EVENT_TO_NAME.get(payload.event.strip().lower())
    if not event_name:
        return {"status": "ignored", "reason": f"unknown event '{payload.event}'"}

    reason = SUPPRESSION_EVENTS.get(event_name)
    if reason == "hard_bounce" and (payload.bounce_type or "").strip().lower() == "soft":
        reason = None
    dedup_id = payload.message_id or f"{decoded['campaign_id']}:{decoded['master_profile_id']}:{event_name}"
    _record_event(
        decoded,
        event_name,
        service,
        dedup_key=f"{provider}:{dedup_id}",
        payload={
            "provider": provider,
            "message_id": payload.message_id,
            "provider_email": payload.email,
            "bounce_type": payload.bounce_type,
            "timestamp": payload.timestamp,
            "suppression_reason": reason,
        },
    )
    return {"status": "ok", "event": event_name, "suppressed": bool(reason)}


all_email_tracking_routers = [router]
