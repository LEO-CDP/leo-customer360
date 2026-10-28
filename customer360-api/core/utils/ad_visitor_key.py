"""Visitor key derivation for the ad personalisation admin plane.

This is the SAME derivation as ``customer360-promotions/personalization/identity.py``
(the ad server), duplicated on purpose because each service deploys on its own:

    tenant_secret = HMAC-SHA-256(key=master_secret, msg=b"c360ads:tenant:" + str(tenant_id))
    visitor_key   = HMAC-SHA-256(key=tenant_secret, msg=leovid)[:16]

The admin API needs it to turn a visitor id into the hashed key an erasure
request is recorded and carried out against. Both implementations are pinned by
``specs/003-ad-personalization-engine/contracts/visitor-key-test-vectors.json``,
which each service's tests load, so they cannot drift apart.

The master secret must be the one the ad server uses. It is read from
``AD_VISITOR_KEY_MASTER_SECRET`` or, so one value serves both services, from the
ad server's own ``c360_PROMOTION_VISITOR_KEY_MASTER_SECRET``. With neither set,
a fixed development-only value is used when the environment is ``development``
(the ad server's default); in any other environment keys cannot be derived and
:class:`VisitorKeyUnavailable` is raised, so no guessable key is ever used.

Never log a raw visitor id or a full key; use :func:`fingerprint`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

KEY_BYTES = 16
MAX_VISITOR_ID_LENGTH = 128

_TENANT_CONTEXT = b"c360ads:tenant:"
_DEVELOPMENT_SECRET = "development-only-visitor-key-secret"


class VisitorKeyUnavailable(RuntimeError):
    """No master secret is configured, so visitor keys cannot be derived."""


def configured_master_secret() -> Optional[str]:
    """The master secret to use, or None when keys must not be derived."""
    for name in ("AD_VISITOR_KEY_MASTER_SECRET", "c360_PROMOTION_VISITOR_KEY_MASTER_SECRET"):
        value = os.environ.get(name)
        if value:
            return value
    environment = os.environ.get("c360_PROMOTION_ENVIRONMENT", "development")
    return _DEVELOPMENT_SECRET if environment == "development" else None


def tenant_secret(master_secret: str, tenant_id: int) -> bytes:
    """Derive the per-tenant secret from the master secret."""
    return hmac.new(
        master_secret.encode("utf-8"),
        _TENANT_CONTEXT + str(int(tenant_id)).encode("ascii"),
        hashlib.sha256,
    ).digest()


def normalise_visitor_id(leovid: str) -> str:
    """Trim and validate a visitor id. Raises ValueError when unusable."""
    if not isinstance(leovid, str):
        raise ValueError("visitor id must be a string")
    cleaned = leovid.strip()
    if not cleaned:
        raise ValueError("visitor id must not be empty")
    if len(cleaned) > MAX_VISITOR_ID_LENGTH:
        raise ValueError(f"visitor id is longer than {MAX_VISITOR_ID_LENGTH} characters")
    return cleaned


def visitor_key(tenant_id: int, leovid: str, master_secret: Optional[str] = None) -> bytes:
    """Derive the 16-byte visitor key for one tenant.

    Raises :class:`VisitorKeyUnavailable` when there is no master secret.
    """
    if master_secret is None:
        master_secret = configured_master_secret()
    if not master_secret:
        raise VisitorKeyUnavailable("the visitor key master secret is not configured")

    digest = hmac.new(
        tenant_secret(master_secret, tenant_id),
        normalise_visitor_id(leovid).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return digest[:KEY_BYTES]


def fingerprint(key: Optional[bytes]) -> Optional[str]:
    """Short, non-reversible label for logs and responses (first 4 bytes, hex)."""
    if key is None:
        return None
    return bytes(key)[:4].hex()
