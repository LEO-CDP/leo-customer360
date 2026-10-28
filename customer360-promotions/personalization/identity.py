"""Visitor key derivation.

The ad tables never store a raw visitor identifier. A visitor's ``leovid`` (the
UUID the widget keeps in local storage) is turned into a short keyed hash:

    tenant_secret = HMAC-SHA-256(key=master_secret, msg=b"c360ads:tenant:" + str(tenant_id))
    visitor_key   = HMAC-SHA-256(key=tenant_secret, msg=leovid)[:16]

Because the secret is per tenant, the same visitor id gives a different key in
every tenant, so keys cannot be joined across tenants. Because it is keyed, the
key cannot be recomputed from a guessed visitor id without the secret, which is
why only the holder of the visitor id can record a consent choice for it.

The admin API in ``customer360-api`` derives the same key (to resolve an
erasure request); both implementations are pinned by the shared vectors in
``specs/003-ad-personalization-engine/contracts/visitor-key-test-vectors.json``.

Never log a raw visitor id or a full key; use :func:`fingerprint`.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional

KEY_BYTES = 16
MAX_VISITOR_ID_LENGTH = 128

_TENANT_CONTEXT = b"c360ads:tenant:"


class VisitorKeyUnavailable(RuntimeError):
    """No master secret is configured, so visitor keys cannot be derived."""


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

    ``master_secret`` defaults to the configured secret. Raises
    :class:`VisitorKeyUnavailable` when there is none (outside development this
    means personalisation stays off and serving uses default ranking).
    """
    if master_secret is None:
        from core.config import promotions_settings  # local: keeps this module import-light

        master_secret = promotions_settings.effective_visitor_key_secret
    if not master_secret:
        raise VisitorKeyUnavailable("visitor_key_master_secret is not configured")

    visitor_id = normalise_visitor_id(leovid)
    digest = hmac.new(
        tenant_secret(master_secret, tenant_id),
        visitor_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return digest[:KEY_BYTES]


def describe_error(exc: BaseException) -> str:
    """Exception type names only, for logging a failure that involved a visitor key.

    A database error message can quote the values of the query it failed on, and a
    traceback prints that message, so neither may be logged where a visitor key was
    in play. ``OperationalError(QueryCanceled)`` says what happened without it.
    """
    name = type(exc).__name__
    original = getattr(exc, "orig", None)
    return f"{name}({type(original).__name__})" if original is not None else name


def fingerprint(key: Optional[bytes]) -> Optional[str]:
    """Short, non-reversible label for logs and explanations (first 4 bytes, hex)."""
    if key is None:
        return None
    return bytes(key)[:4].hex()
