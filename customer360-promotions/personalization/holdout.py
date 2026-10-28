"""Deterministic hold-out assignment.

A configurable share of visitors is held out from personalisation and served
default ranking only, so personalised and default delivery can be compared
fairly. Assignment is per visitor, not per request: the same visitor must stay
in the same group for the whole measurement period, otherwise the comparison is
contaminated.

The group is a pure function of (tenant, measurement-period salt, visitor key):
nothing is stored per visitor, so there is nothing extra to erase. Changing the
salt (``new_measurement_period``) reshuffles who is held out.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Union

# Resolution of the hold-out share: hundredths of a percent.
_BUCKETS = 10_000


def holdout_bucket(tenant_id: int, user_key: bytes, salt: str) -> int:
    """Map a visitor to a stable bucket in ``0 .. 9999``."""
    material = f"{int(tenant_id)}:{salt}:".encode("utf-8") + bytes(user_key)
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:4], "big") % _BUCKETS


def is_holdout(
    tenant_id: int,
    user_key: bytes,
    salt: str,
    holdout_pct: Union[float, int, Decimal],
) -> bool:
    """True when this visitor is in the hold-out group.

    ``holdout_pct`` of 0 (or less) never holds anyone out; 100 (or more) holds
    everyone out.
    """
    pct = float(holdout_pct)
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    return holdout_bucket(tenant_id, user_key, salt) < round(pct * 100)
