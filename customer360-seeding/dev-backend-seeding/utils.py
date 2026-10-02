"""Shared helpers for Customer 360 backend demo-data seeders."""

import hashlib
from typing import Optional

HASHED_PII_FIELDS = ("full_name", "email", "phone_number", "national_id")


def hash_pii(value: Optional[object]) -> Optional[str]:
    """Return a normalized SHA-256 digest for a PII value."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
