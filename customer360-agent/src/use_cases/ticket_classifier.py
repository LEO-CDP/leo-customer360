"""Use case 6: support ticket classification.

A small, cheap model labels each ticket with urgency, sentiment and a routing
team. Labels outside the allowed sets are never repaired or guessed: the field
is left empty and the ticket is flagged `needs_review` for a person.

The rules come from the published instruction (prompt store). The allowed
values, which can vary per request (teams), and the ticket are appended here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ai_providers.base import AIProviderError, parse_json_object
from use_cases.instructions import instructions

MAX_CHARS = 4000
URGENCIES = ("low", "medium", "high", "critical")
SENTIMENTS = ("negative", "neutral", "positive")
TEAMS = ("billing", "technical", "account", "general")

_DATA = """Allowed values:
urgency: {urgencies}
sentiment: {sentiments}
team: {teams}

Ticket:
{ticket}
"""


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class TicketClassification:
    ticket_id: Optional[str]
    urgency: Optional[str]
    sentiment: Optional[str]
    team: Optional[str]
    needs_review: bool
    reason: Optional[str]


def _review(ticket_id: Optional[str], reason: str, **labels: Optional[str]) -> TicketClassification:
    return TicketClassification(
        ticket_id=ticket_id,
        urgency=labels.get("urgency"),
        sentiment=labels.get("sentiment"),
        team=labels.get("team"),
        needs_review=True,
        reason=reason,
    )


def classify_ticket(
    text: str,
    provider: Provider,
    teams: tuple[str, ...] = TEAMS,
    ticket_id: Optional[str] = None,
) -> TicketClassification:
    if not text.strip():
        return _review(ticket_id, "empty ticket text")

    prompt = f"{instructions('tickets')}\n\n" + _DATA.format(
        urgencies=list(URGENCIES), sentiments=list(SENTIMENTS), teams=list(teams), ticket=text[:MAX_CHARS]
    )
    try:
        data: dict[str, Any] = parse_json_object(provider.complete(prompt))
    except AIProviderError as exc:
        return _review(ticket_id, str(exc))

    allowed = {"urgency": URGENCIES, "sentiment": SENTIMENTS, "team": teams}
    labels: dict[str, Optional[str]] = {}
    problems: list[str] = []
    for name, options in allowed.items():
        value = data.get(name)
        if value in options:
            labels[name] = value
        else:
            labels[name] = None
            problems.append(f"{name} {value!r} is not one of {list(options)}")

    if problems:
        return _review(ticket_id, "; ".join(problems), **labels)
    return TicketClassification(ticket_id, labels["urgency"], labels["sentiment"], labels["team"], False, None)


def classify_batch(
    tickets: list[dict[str, str]], provider: Provider, teams: tuple[str, ...] = TEAMS
) -> list[TicketClassification]:
    """One result per ticket, in order. A failed ticket is flagged, not fatal."""
    return [classify_ticket(t["text"], provider, teams, ticket_id=t.get("id")) for t in tickets]
