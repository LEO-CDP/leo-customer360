"""PUBLIC email tracking + webhook routes.

Unauthenticated by design -- these are hit by recipient mail clients (open
pixel, click redirect, unsubscribe) and by email providers (delivery/bounce/
complaint webhooks), none of which can present a bearer token or tenant header.
Correlation + tenant come from the signed tracking token (``u`` / payload
``token``), verified via ``core.utils.email_tracking.decode_tracking_token``;
an invalid token is silently ignored (never 500s a mail client / provider).

Every path here MUST be listed in ``PUBLIC_PATHS`` (core/apps/http_api_app.py)
so the auth middleware lets it through.

Events normalize into ``cdp_raw_events``; hard bounce / complaint / unsubscribe
also add the recipient to ``cdp_email_suppression`` (see core/crud/email_tracking).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from core.config import settings
from core.crud.email_tracking import (
    add_suppression,
    record_engagement_event,
    resolve_recipient_email,
)
from core.schemas.crm import EmailWebhookEvent
from core.utils.email_tracking import (
    SUPPRESSION_EVENTS,
    TRANSPARENT_GIF,
    WEBHOOK_EVENT_TO_NAME,
    decode_tracking_token,
    verify_click_url,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

email_tracking_router = APIRouter(prefix="/track/email", tags=["Email - Tracking"])

_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate, private"}


def _safe_record(decoded: dict, event_name: str, *, dedup_key: str, payload: dict | None = None) -> None:
    """Record an engagement event, swallowing any error so a tracking hit never
    fails the pixel/redirect the recipient is waiting on."""
    try:
        record_engagement_event(
            decoded["tenant_id"], decoded["campaign_id"], decoded["master_profile_id"],
            event_name, dedup_key=dedup_key, event_payload=payload,
        )
    except Exception:  # noqa: BLE001
        logger.warning("failed to record %s event", event_name, exc_info=True)


@email_tracking_router.get("/open")
def track_open(u: Optional[str] = Query(None, description="Tracking token.")):
    """Open-tracking pixel: records email-opened, always returns a 1x1 GIF
    (even for a missing/invalid token -- never errors the mail client)."""
    decoded = decode_tracking_token(u) if u else None
    if decoded:
        _safe_record(decoded, "email-opened",
                     dedup_key=f"{decoded['campaign_id']}:{decoded['master_profile_id']}:email-opened")
    return Response(content=TRANSPARENT_GIF, media_type="image/gif", headers=_NO_STORE)


@email_tracking_router.get("/click")
def track_click(
    u: Optional[str] = Query(None),
    url: Optional[str] = Query(None, description="Original destination URL."),
    k: Optional[str] = Query(None, description="HMAC of url, minted at send time (anti open-redirect)."),
):
    """Click-tracking redirect: records email-clicked, 302s to the original URL
    ONLY when its signature ``k`` verifies (so the destination can't be swapped
    into an open redirect) and it is http/https; otherwise redirects to '/'."""
    decoded = decode_tracking_token(u) if u else None
    trusted_url = bool(url) and url.lower().startswith(("http://", "https://")) and verify_click_url(url, k)
    if decoded:
        _safe_record(decoded, "email-clicked",
                     dedup_key=f"{decoded['campaign_id']}:{decoded['master_profile_id']}:email-clicked",
                     payload={"url": url if trusted_url else None})
    return RedirectResponse(url if trusted_url else "/", status_code=302, headers=_NO_STORE)


@email_tracking_router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(u: str = Query(...)):
    """One-click unsubscribe: suppresses the recipient + records
    email-unsubscribed, returns a simple confirmation page."""
    decoded = decode_tracking_token(u)
    if not decoded:
        return HTMLResponse("<html><body><p>This unsubscribe link is invalid or expired.</p></body></html>",
                            status_code=400)
    _safe_record(decoded, "email-unsubscribed",
                 dedup_key=f"{decoded['campaign_id']}:{decoded['master_profile_id']}:email-unsubscribed")
    email = resolve_recipient_email(decoded["tenant_id"], decoded["campaign_id"], decoded["master_profile_id"])
    if email:
        try:
            add_suppression(decoded["tenant_id"], email, "unsubscribe",
                            campaign_id=decoded["campaign_id"], source="unsubscribe-link")
        except Exception:  # noqa: BLE001
            logger.warning("failed to suppress %s on unsubscribe", email, exc_info=True)
    return HTMLResponse("<html><body><p>You have been unsubscribed. We're sorry to see you go.</p></body></html>")


@email_tracking_router.post("/webhook")
async def email_webhook(
    request: Request,
    provider: str = Query("generic"),
    x_webhook_signature: Optional[str] = Header(None),
):
    """Email provider callback (delivery/bounce/complaint/open/click). Correlates
    via the echoed token, records the event (deduped), and updates suppression on
    hard bounce / complaint. Always 200 on accepted input so the provider does not
    retry-storm.

    AUTHENTICITY: the request body must carry a valid HMAC-SHA256 signature
    (``X-Webhook-Signature``) over the raw body, keyed by
    CRM_EMAIL_WEBHOOK_SIGNING_SECRET -- otherwise a holder of a (public) tracking
    token could forge bounce/complaint callbacks and poison the suppression list.
    The endpoint is disabled (503) when the secret is unset (fail closed)."""
    secret = settings.email_webhook_signing_secret
    if not secret:
        return JSONResponse({"status": "disabled", "reason": "webhook signing secret not configured"}, status_code=503)
    raw = await request.body()
    if not verify_webhook_signature(raw, x_webhook_signature, secret):
        return JSONResponse({"status": "rejected", "reason": "invalid signature"}, status_code=401)
    try:
        payload = EmailWebhookEvent.model_validate_json(raw)
    except Exception:  # noqa: BLE001 - malformed body after a valid signature.
        return JSONResponse({"status": "rejected", "reason": "invalid payload"}, status_code=400)

    decoded = decode_tracking_token(payload.token)
    if not decoded:
        return {"status": "ignored", "reason": "missing or invalid token"}

    event_name = WEBHOOK_EVENT_TO_NAME.get(payload.event.strip().lower())
    if not event_name:
        return {"status": "ignored", "reason": f"unknown event '{payload.event}'"}

    dedup_id = payload.message_id or f"{decoded['campaign_id']}:{decoded['master_profile_id']}:{event_name}"
    _safe_record(decoded, event_name, dedup_key=f"{provider}:{dedup_id}",
                 payload={"provider": provider, "message_id": payload.message_id, "event": payload.event})

    reason = SUPPRESSION_EVENTS.get(event_name)
    # Soft bounces do NOT suppress -- only hard bounces / complaints / unsubscribes do.
    if reason == "hard_bounce" and (payload.bounce_type or "").strip().lower() == "soft":
        reason = None
    if reason:
        # Resolve the address from OUR dispatch ledger via the signed token --
        # never from the (untrusted) payload -- so a caller can't suppress an
        # arbitrary address it doesn't own.
        email = resolve_recipient_email(
            decoded["tenant_id"], decoded["campaign_id"], decoded["master_profile_id"]
        )
        if email:
            try:
                add_suppression(decoded["tenant_id"], email, reason,
                                campaign_id=decoded["campaign_id"], source=provider)
            except Exception:  # noqa: BLE001
                logger.warning("failed to suppress %s from webhook", email, exc_info=True)

    return {"status": "ok", "event": event_name, "suppressed": bool(reason)}


all_email_tracking_routers = [email_tracking_router]
