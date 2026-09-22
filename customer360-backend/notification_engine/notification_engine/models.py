"""Data models for the notification engine."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DispatchResult:
    """Outcome of one adapter send. ``ok`` drives the dispatch-log status
    (Sent vs Failed); ``provider_message_id`` / ``error`` are recorded as-is."""

    ok: bool
    provider_message_id: Optional[str] = None
    error: Optional[str] = None
