"""Shared webhook security helpers used by every channel's tracking webhook.

Keeping the HMAC verification in one place stops the per-channel webhooks (email,
Zalo, ...) from drifting on auth-relevant code.
"""

import hashlib
import hmac
from typing import Optional


def verify_hmac_signature(raw_body: bytes, signature: Optional[str], secret: str) -> bool:
    """Constant-time verify a hex HMAC-SHA256 over ``raw_body``. Accepts an optional
    ``sha256=`` prefix on the provided signature; False if secret or signature is missing."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(provided.strip(), expected)
