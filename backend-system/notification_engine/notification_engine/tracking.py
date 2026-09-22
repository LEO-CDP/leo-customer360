"""Signed tracking-token encoding for Zalo ZNS sends.

Identical format + secret to email_engine/tracking.py so the SAME
customer360-event-api decode side works for both channels. A token carries
(tenant_id, campaign_id, master_profile_id); the ZNS send embeds it as the
``tracking_id`` the webhook echoes back. HMAC-signed with EMAIL_TRACKING_SECRET.

    raw   = "<tenant_id>|<campaign_id>|<master_profile_id>"
    sig   = hmac_sha256(secret, raw).hexdigest()[:20]
    token = base64url("<raw>|<sig>")  # padding stripped
"""

import base64
import hashlib
import hmac

from .config import TRACKING_SECRET

_SIG_LEN = 20


def _sign(raw: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:_SIG_LEN]


def encode_tracking_token(tenant_id: str, campaign_id: str, master_profile_id: str,
                          *, secret: str = TRACKING_SECRET) -> str:
    """Build a signed, URL-safe tracking token for one (campaign, recipient)."""
    raw = f"{tenant_id}|{campaign_id}|{master_profile_id}"
    payload = f"{raw}|{_sign(raw, secret)}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
