"""Signed tracking-token encoding for email open/click/unsubscribe URLs.

A token carries (tenant_id, campaign_id, master_profile_id) so the public
tracking endpoints (open pixel, click redirect, unsubscribe) can correlate a
callback back to the exact campaign + profile *without* a login/tenant header.
It is HMAC-signed with ``EMAIL_TRACKING_SECRET`` so those endpoints can reject
forged/tampered tokens.

The decode side lives in customer360-api (a separate deployable); both sides
implement the SAME format independently -- keep them in sync. Format::

    raw    = "<tenant_id>|<campaign_id>|<master_profile_id>"
    sig    = hmac_sha256(secret, raw).hexdigest()[:20]
    token  = base64url("<raw>|<sig>")   # padding stripped
"""

import base64
import hashlib
import hmac
import os

TRACKING_SECRET = os.environ.get("EMAIL_TRACKING_SECRET", "leocdp-dev-tracking-secret")
_SIG_LEN = 20


def _sign(raw: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:_SIG_LEN]


def encode_tracking_token(
    tenant_id: str,
    campaign_id: str,
    master_profile_id: str,
    *,
    secret: str = TRACKING_SECRET,
) -> str:
    """Build a signed, URL-safe tracking token for one (campaign, recipient)."""
    raw = f"{tenant_id}|{campaign_id}|{master_profile_id}"
    payload = f"{raw}|{_sign(raw, secret)}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
