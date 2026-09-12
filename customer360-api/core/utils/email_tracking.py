"""Email tracking token verification + shared constants.

Decodes the signed tokens minted by backend-system's email_engine
(``email_engine/tracking.py``). The two deployables implement the SAME format
independently -- keep them in sync::

    raw    = "<tenant_id>|<campaign_id>|<master_profile_id>"
    sig    = hmac_sha256(secret, raw).hexdigest()[:20]
    token  = base64url("<raw>|<sig>")   # padding stripped

``decode_tracking_token`` returns the three ids only when the signature checks
out (constant-time compare), so a forged/tampered token is rejected.
"""

import base64
import hashlib
import hmac
import logging
from typing import Optional

from core.config import settings

_SIG_LEN = 20

if settings.email_tracking_secret == "leocdp-dev-tracking-secret":  # pragma: no cover
    logging.getLogger(__name__).warning(
        "EMAIL_TRACKING_SECRET is the insecure dev default; set a strong value in prod."
    )

# 1x1 fully-transparent GIF returned by the open-pixel endpoint.
TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

# Provider/normalized event -> governed cdp_event_catalog event_name.
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

# Events that add the recipient to the compliance suppression list, mapped to
# the suppression ``reason``. Soft bounces are handled by the caller (they do
# NOT suppress).
SUPPRESSION_EVENTS = {
    "email-complained": "complaint",
    "email-unsubscribed": "unsubscribe",
    "email-bounced": "hard_bounce",
}


def _sign(raw: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:_SIG_LEN]


def verify_click_url(url: str, sig: Optional[str], secret: Optional[str] = None) -> bool:
    """True if ``sig`` is the valid HMAC of ``url`` -- proves the click
    destination was minted by us and hasn't been swapped (anti open-redirect).
    The main token signs tenant/campaign/profile only; this signs the URL."""
    if not url or not sig:
        return False
    secret = secret if secret is not None else settings.email_tracking_secret
    return hmac.compare_digest(sig, _sign(url, secret))


def verify_webhook_signature(raw_body: bytes, signature: Optional[str], secret: str) -> bool:
    """Verify a provider webhook HMAC-SHA256 over the raw request body.
    Accepts a bare hex digest or a ``sha256=<hex>`` prefixed one. Fails closed
    when the secret or signature is missing."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(provided.strip(), expected)


def decode_tracking_token(token: str, secret: Optional[str] = None) -> Optional[dict]:
    """Verify a tracking token and return ``{tenant_id, campaign_id,
    master_profile_id}``, or ``None`` if it is malformed or the signature is
    invalid."""
    if not token:
        return None
    secret = secret if secret is not None else settings.email_tracking_secret
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 - any decode error -> invalid token.
        return None

    parts = decoded.split("|")
    if len(parts) != 4:
        return None
    tenant_id, campaign_id, master_profile_id, sig = parts
    expected = _sign(f"{tenant_id}|{campaign_id}|{master_profile_id}", secret)
    if not hmac.compare_digest(sig, expected):
        return None
    return {
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
        "master_profile_id": master_profile_id,
    }
