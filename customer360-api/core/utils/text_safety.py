"""Masking and whitespace helpers shared by the assistant router and the screen-fact loaders.

Staff may paste a customer's details into the question box or a staff-written free-text field, and
PII must never reach the model or an audit row. These helpers live outside ``assistant_api`` so the
screen-fact loaders can reuse them without importing a router (which would be circular).
"""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_PHONE_MIN_DIGITS = 9  # a date (2026-10-06) or a figure (1 000 000) has fewer digits than a phone number
_WHITESPACE = re.compile(r"\s+")


def collapse_whitespace(text: str) -> str:
    """Runs of whitespace (newlines, tabs, repeated spaces) become single spaces, then trimmed."""
    return _WHITESPACE.sub(" ", text or "").strip()


def _mask_phone(match: re.Match) -> str:
    return "[phone]" if sum(c.isdigit() for c in match.group()) >= _PHONE_MIN_DIGITS else match.group()


def mask_text(text: str, cap: int) -> str:
    """Emails and phone numbers masked, length capped.

    Staff may paste a customer's details into the box, and PII must not reach the model or a log.
    """
    return _PHONE.sub(_mask_phone, _EMAIL.sub("[email]", text or ""))[:cap]
