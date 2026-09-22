"""Pluggable Zalo ZNS dispatch adapters (mirrors email_engine/adapters.py).

The adapter and API base are selected from the tenant's connector row:

  * ``mock`` -- MockZNSAdapter: logs + returns a synthetic message id, so the
    pipeline runs end-to-end with no Zalo credentials (E2E / tests).
  * ``zns``  -- ZNSDispatchAdapter: real send via the Zalo OA ZNS API.

⚠️ Zalo ZNS send endpoint/payload -- confirm against current Zalo OA docs.
"""

import json
import logging
import urllib.request
import uuid
from typing import Optional

from .models import DispatchResult

logger = logging.getLogger(__name__)


class DispatchAdapter:
    provider_name = "base"

    def send(self, *, phone: str, template_id: str, template_data: dict, tracking_id: str) -> DispatchResult:
        raise NotImplementedError


class MockZNSAdapter(DispatchAdapter):
    """No-network adapter: logs the send, always succeeds with a synthetic id."""

    provider_name = "zalo_zns_mock"

    def send(self, *, phone: str, template_id: str, template_data: dict, tracking_id: str) -> DispatchResult:
        message_id = f"mock-zns-{uuid.uuid4()}"
        # Log the non-PII tracking handle, never the recipient phone number.
        logger.info("MockZNSAdapter: template=%s tracking=%s msg_id=%s", template_id, tracking_id, message_id)
        return DispatchResult(ok=True, provider_message_id=message_id)


class ZNSDispatchAdapter(DispatchAdapter):
    """Real ZNS send via the Zalo OA business Open API."""

    provider_name = "zalo_zns"

    def __init__(self, access_token: str, api_base: str = "https://business.openapi.zalo.me") -> None:
        self.token = access_token
        self.api_base = api_base.rstrip("/")

    def send(self, *, phone: str, template_id: str, template_data: dict, tracking_id: str) -> DispatchResult:
        # ⚠️ POST {api_base}/message/template  header: access_token
        payload = json.dumps({
            "phone": phone,
            "template_id": str(template_id),
            "template_data": template_data,
            "tracking_id": tracking_id,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_base}/message/template",
            data=payload,
            headers={"access_token": self.token, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
        except Exception as exc:  # noqa: BLE001 - surface any send failure as a Failed dispatch
            # Log the non-PII tracking handle, never the recipient phone number.
            logger.warning("ZNSDispatchAdapter: send failed (tracking=%s): %s", tracking_id, exc)
            return DispatchResult(ok=False, error=str(exc))
        if data.get("error") == 0:
            return DispatchResult(ok=True, provider_message_id=str((data.get("data") or {}).get("msg_id", "")))
        return DispatchResult(ok=False, error=f"{data.get('error')}:{data.get('message')}")


def build_zns_adapter(
    access_token: Optional[str] = None,
    provider: Optional[str] = None,
    api_base: Optional[str] = None,
) -> DispatchAdapter:
    """Return the configured adapter, defaulting to mock without credentials."""
    provider = (provider or "mock").strip().lower()
    if provider == "zns" and access_token:
        return ZNSDispatchAdapter(access_token, api_base or "https://business.openapi.zalo.me")
    if provider not in ("zns", "mock"):
        logger.warning("Unknown Zalo dispatch adapter %r; falling back to mock", provider)
    return MockZNSAdapter()
