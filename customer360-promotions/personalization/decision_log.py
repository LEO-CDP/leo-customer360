"""Serving-decision log: a compact record of why the chosen ads were chosen.

One ``leo_ads.serving_decision`` row per serve request. It stores only the CHOSEN
ads (never the whole candidate list, research D12), each with its rank, score and
the few signals that moved the score most, so a decision can be explained later
without the log growing with catalogue size.

Writing is best effort and never blocks or fails a serve request: records go to a
bounded buffer and, if it is full, the record is dropped and counted.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Optional, Sequence

from personalization.buffered_writer import BufferOverflow
from personalization.scoring import (
    SIGNAL_BASE_WEIGHT,
    SIGNAL_COHORT_PATTERN,
    SIGNAL_EXPLORATION,
    SIGNAL_OBJECTIVE_RATE_LIFT,
    SIGNAL_PAGE_CONTEXT,
    SIGNAL_VISITOR_AFFINITY,
    ScoredAd,
)

logger = logging.getLogger(__name__)

TOP_SIGNALS = 3


def top_signals(scored: ScoredAd, count: int = TOP_SIGNALS) -> list[dict[str, Any]]:
    """The signals that moved this score most, strongest first.

    Signals multiply, so "most" is the largest distance from 1.0 in log terms: a
    factor of 2.0 and a factor of 0.5 are equally strong.
    """
    ranked = sorted(
        scored.signals,
        key=lambda s: abs(math.log(s.contribution)) if s.contribution > 0 else float("inf"),
        reverse=True,
    )
    return [
        {"name": s.name, "contribution": round(s.contribution, 4), "note": s.note}
        for s in ranked[:count]
    ]


# The signal names the admin contract allows (contracts/admin-plane.openapi.yaml,
# Signal.name). They are the constants scoring.py emits; a test checks both.
SIGNAL_NAMES = (
    SIGNAL_OBJECTIVE_RATE_LIFT,
    SIGNAL_VISITOR_AFFINITY,
    SIGNAL_COHORT_PATTERN,
    SIGNAL_EXPLORATION,
    SIGNAL_BASE_WEIGHT,
    SIGNAL_PAGE_CONTEXT,
)


def explain_signals(signals: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stored signals as the admin contract's ``Signal`` objects.

    Only contract names are returned, each with its multiplicative ``contribution``
    and a readable ``note`` (for example "click rate 2.60% vs placement baseline
    2.00%"). A signal with an unknown name or no usable contribution is dropped
    rather than shown wrongly; a missing note becomes a plain description.
    """
    explained = []
    for signal in signals or []:
        name = signal.get("name")
        try:
            contribution = float(signal["contribution"])
        except (KeyError, TypeError, ValueError):
            continue
        if name not in SIGNAL_NAMES:
            continue
        explained.append(
            {
                "name": name,
                "contribution": contribution,
                "note": signal.get("note") or f"{name.replace('_', ' ')} x{contribution:.2f}",
            }
        )
    return explained


def build_record(
    *,
    request_id: str,
    decided_at: datetime,
    tenant_id: int,
    group_code: str,
    candidate_count: int,
    placement_id: Optional[int] = None,
    user_key: Optional[bytes] = None,
    chosen: Sequence[ScoredAd] = (),
) -> dict[str, Any]:
    """Build the ``serving_decision`` row. ``user_key`` is None for visitors not eligible."""
    return {
        "request_id": request_id,
        "decided_at": decided_at,
        "tenant_id": tenant_id,
        "user_key_hash": bytes(user_key) if user_key is not None else None,
        "placement_id": placement_id,
        "group_code": group_code,
        "chosen": [
            {
                "ad_id": scored.ad_id,
                "rank": rank,
                "score": round(scored.score, 6),
                "signals": top_signals(scored),
            }
            for rank, scored in enumerate(chosen, start=1)
        ],
        "candidate_count": candidate_count,
    }


class DecisionLog:
    """Queue decision records on a BufferedWriter; never raises."""

    def __init__(self, writer: Any) -> None:
        self._writer = writer
        self.dropped = 0

    def log(self, record: dict[str, Any]) -> bool:
        """Queue one record. Returns False (and counts it) if the buffer was full."""
        try:
            self._writer.put(record)
            return True
        except BufferOverflow:
            self.dropped += 1
            logger.warning("decision log buffer full; dropped a serving decision")
            return False
        except Exception as exc:  # noqa: BLE001 - logging must never fail a request
            self.dropped += 1
            logger.error("decision log failed (%s)", type(exc).__name__)
            return False
