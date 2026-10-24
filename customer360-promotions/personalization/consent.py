"""Consent resolution for behaviour-based personalisation.

A visitor is eligible to be personalised (and to have a behaviour profile built
and used) only when:

* their stored choice is ``granted``; or
* they have no recorded choice and the tenant's consent mode is ``opt_out``.

A ``denied`` choice always means default ranking. With no recorded choice in an
``opt_in`` tenant (the default) the visitor is not eligible. Anything
unrecognised is treated as not eligible: when in doubt, do not profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

GRANTED = "granted"
DENIED = "denied"

OPT_IN = "opt_in"
OPT_OUT = "opt_out"

# Serving-decision group used when consent is the reason for default ranking.
REASON_NO_CONSENT = "default_no_consent"


@dataclass(frozen=True)
class ConsentDecision:
    """Whether a visitor may be personalised, and why not when they may not."""

    eligible: bool
    reason: Optional[str] = None


_ELIGIBLE = ConsentDecision(True, None)
_NOT_ELIGIBLE = ConsentDecision(False, REASON_NO_CONSENT)


def is_eligible(status: Optional[str], consent_mode: str) -> ConsentDecision:
    """Resolve consent. ``status`` is None when the visitor has no recorded choice."""
    if status == DENIED:
        return _NOT_ELIGIBLE
    if status == GRANTED:
        return _ELIGIBLE
    if status is None:
        return _ELIGIBLE if consent_mode == OPT_OUT else _NOT_ELIGIBLE
    # An unrecognised stored status: do not profile.
    return _NOT_ELIGIBLE
