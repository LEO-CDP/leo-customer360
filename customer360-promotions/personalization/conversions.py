"""Signed server-to-server conversion postbacks (``POST /conversions``).

An advertiser or affiliate system reports that a click led to a conversion. The
call is authenticated, not trusted:

* the body is signed with HMAC-SHA-256 using the tenant's conversion secret
  (same approach as customer360-event-api's ``verify_hmac_signature``, kept as a
  local copy because each service deploys on its own);
* a timestamp header must be within ``conversion_replay_window_seconds``;
* an ``event_id`` can be used once (409 on repeats);
* the conversion is attributed to the originating click (``click_id``) or serve
  request (``request_id``). One that cannot be attributed is rejected (422), never
  guessed.

The stored ``conversion`` event reuses the visitor key of the click it came from.
A key is never derived here: the postback carries no visitor id, and a click from
a visitor who could not be profiled has a null key, so its conversion does too.

A failure of this module never touches ad serving.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional
from uuid import UUID

from pydantic import ValidationError

from core.schemas import ConversionIn
from personalization.buffered_writer import BufferOverflow
from personalization.hot_state import HotStateUnavailable
from personalization.identity import tenant_secret

logger = logging.getLogger(__name__)

EVENT_ID_TTL_SECONDS = 7 * 86_400
MAX_FUTURE = timedelta(minutes=10)
MAX_AGE = timedelta(days=30)
CLICK_ID_LENGTH = 32


class ConversionError(Exception):
    """A request-level failure with the HTTP status the caller should see."""

    def __init__(self, status: int, detail: str, headers: Optional[dict[str, str]] = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.headers = headers or {}


def issue_click_id(
    master_secret: Optional[str], tenant_id: int, request_id: str, ad_id: int
) -> Optional[str]:
    """The click id handed out with an ad: an HMAC of (request, ad) under a per-tenant secret.

    Deterministic, so serving needs no storage and the same request and ad always
    get the same id; unguessable, so a third party cannot forge a click id for a
    click that never happened and have a conversion attributed to it. 128 bits of
    the HMAC is plenty. None when no secret is configured (conversions can then
    still be attributed by ``request_id``).
    """
    if not master_secret:
        return None
    key = tenant_secret(master_secret, tenant_id)
    digest = hmac.new(key, f"click:{request_id}:{int(ad_id)}".encode(), hashlib.sha256).hexdigest()
    return digest[:CLICK_ID_LENGTH]


def verify_signature(raw_body: bytes, signature: Optional[str], secret: str) -> bool:
    """Constant-time check of a hex HMAC-SHA-256 over the raw body (``sha256=`` prefix allowed)."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(provided.strip(), expected)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _uuid(value: Optional[str]) -> Optional[str]:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


class ConversionService:
    """Authenticates, attributes and queues one conversion postback."""

    def __init__(
        self,
        *,
        tenant_repository: Any,
        event_repository: Any,
        hot_state: Any,
        writer: Any,
        config: Any,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._tenants = tenant_repository
        self._events = event_repository
        self._hot_state = hot_state
        self._writer = writer
        self._config = config
        self._now = now

    def process(
        self,
        *,
        tenant_key: Optional[str],
        timestamp: Optional[str],
        signature: Optional[str],
        raw_body: bytes,
    ) -> dict[str, Any]:
        """Handle one postback. Raises ConversionError (status 401/409/422/503); returns the 202 body."""
        config = self._config
        if not config.conversion_secrets:
            raise ConversionError(503, "Conversion postbacks are not enabled")

        # Every authentication failure is the same 401, so a caller cannot tell an
        # unknown tenant from a tenant without a secret from a bad signature.
        denied = ConversionError(401, "Invalid signature")
        secret = config.conversion_secret_for(tenant_key or "")
        if not secret or not verify_signature(raw_body, signature, secret):
            raise denied

        now = self._now()
        sent_at = _parse_time(timestamp)
        window = timedelta(seconds=config.conversion_replay_window_seconds)
        if sent_at is None or abs(now - sent_at) > window:
            raise denied

        tenant_id = self._tenants.get_active_tenant_id(tenant_key)
        if tenant_id is None:
            raise denied

        try:
            body = ConversionIn.model_validate(json.loads(raw_body))
        except (ValueError, ValidationError):
            raise ConversionError(422, "Malformed conversion")

        event_id = _uuid(body.event_id)
        occurred = _parse_time(body.conversion_time)
        if event_id is None or occurred is None or not (now - MAX_AGE <= occurred <= now + MAX_FUTURE):
            raise ConversionError(422, "Invalid event_id or conversion_time")
        request_id = _uuid(body.request_id) if body.request_id else None
        if body.request_id and request_id is None:
            raise ConversionError(422, "Invalid request_id")
        if (body.revenue_amount is None) != (body.currency is None):
            raise ConversionError(422, "revenue_amount and currency go together")
        try:
            revenue = Decimal(body.revenue_amount) if body.revenue_amount is not None else None
        except InvalidOperation:
            raise ConversionError(422, "Invalid revenue_amount")

        origin = None
        if body.click_id:
            origin = self._events.find_click(tenant_id, body.click_id)
        if origin is None and request_id:
            origin = self._events.find_by_request(tenant_id, request_id)
        if origin is None:
            raise ConversionError(422, "Conversion could not be attributed to a click or request")

        if self._is_duplicate(tenant_id, event_id, occurred):
            raise ConversionError(409, "Duplicate event_id")

        row = {
            "event_id": event_id,
            "event_time": occurred,
            "tenant_id": tenant_id,
            "event_type": "conversion",
            "ad_id": origin["ad_id"],
            "campaign_id": origin.get("campaign_id"),
            "creative_id": origin.get("creative_id"),
            "placement_id": origin.get("placement_id"),
            "user_key_hash": origin.get("user_key_hash"),
            "request_id": str(origin["request_id"]) if origin.get("request_id") else request_id,
            "revenue_amount": revenue,
            "currency": body.currency.upper() if body.currency else None,
            "payload": {
                k: v
                for k, v in {
                    "click_id": body.click_id,
                    "source": "postback",
                    "attributed_by": "click_id" if body.click_id and origin else "request_id",
                }.items()
                if v is not None
            },
            "flags": [],
        }
        try:
            self._writer.put(row)
        except BufferOverflow:
            self._forget(tenant_id, event_id)
            raise ConversionError(503, "Event intake is busy; retry shortly", {"Retry-After": "5"})
        return {"status": "accepted"}

    # ------------------------------------------------------------------

    def _is_duplicate(self, tenant_id: int, event_id: str, occurred: datetime) -> bool:
        """Redis remembers ids for a week; the database check covers Redis being down or flushed."""
        try:
            if self._hot_state.seen_event(tenant_id, f"conversion:{event_id}", EVENT_ID_TTL_SECONDS):
                return True
        except HotStateUnavailable:
            pass
        return bool(self._events.event_exists(tenant_id, event_id, occurred))

    def _forget(self, tenant_id: int, event_id: str) -> None:
        """Undo the dedupe marker when the write was refused, so the retry is not a 'duplicate'."""
        try:
            self._hot_state.delete(tenant_id, "event", f"conversion:{event_id}")
        except HotStateUnavailable:
            pass
