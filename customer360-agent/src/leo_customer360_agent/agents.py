"""Client for the customer360-agent use-case routes.

The routes are stateless: the caller (customer360-api) owns the tenant and the
database, runs any SQL or tool, and sends only the data an agent needs. Each
function POSTs one request through `client._post` (same bearer token, timeout and
error mapping as the planning routes) and returns a small dataclass. An agent
failure, or a reply missing a required field, raises `AIProviderError`.

Results that say `needs_review`, `not_found`, `refused` or `kind == "error"` are
normal answers, not failures: the caller decides what a person should look at."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from leo_customer360_agent import client as _client
from leo_customer360_agent.client import AIProviderError


def _post(path: str, payload: dict) -> dict:
    # Looked up through the client module on every call, so tests can replace client._post.
    return _client._post(path, payload)


def _need(data: dict, *keys: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise AIProviderError(f"AI agent response is missing: {', '.join(missing)}")


# --- /analytics/sql and /analytics/narrative -------------------------------------


@dataclass(frozen=True)
class SqlProposal:
    """status "ok": `sql` is one SELECT for the caller to check again and run.
    "not_found": the schema cannot answer. "refused": not a single SELECT; `sql`
    holds the raw text for logging and must never be run."""

    status: str
    sql: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class NarrativeResult:
    narrative: str
    ungrounded: list[str] = field(default_factory=list)
    chart: Optional[dict[str, Any]] = None


def propose_sql(
    question: str,
    schema: dict[str, str],
    history: Optional[list[dict[str, str]]] = None,
    k: int = 3,
) -> SqlProposal:
    data = _post(
        "/analytics/sql",
        {"question": question, "schema": schema, "history": list(history or []), "k": k},
    )
    _need(data, "status")
    return SqlProposal(status=data["status"], sql=data.get("sql"), error=data.get("error"))


def narrate_rows(question: str, rows: list[dict[str, Any]]) -> NarrativeResult:
    data = _post("/analytics/narrative", {"question": question, "rows": rows})
    _need(data, "narrative")
    return NarrativeResult(
        narrative=data["narrative"],
        ungrounded=list(data.get("ungrounded") or []),
        chart=data.get("chart"),
    )


# --- /marketing/orchestrate/step -------------------------------------------------


@dataclass(frozen=True)
class NextStep:
    """kind "tool": run `tool` with `args` (ask for approval when `requires_approval`).
    "finish": `summary` is the result. "error": the reply was unusable; ask again."""

    kind: str
    tool: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    summary: Optional[str] = None
    requires_approval: bool = False
    reason: Optional[str] = None


def plan_next_step(
    goal: str,
    tools: list[dict[str, Any]],
    steps: Optional[list[dict[str, Any]]] = None,
    context: Optional[list[str]] = None,
) -> NextStep:
    data = _post(
        "/marketing/orchestrate/step",
        {"goal": goal, "tools": tools, "steps": list(steps or []), "context": list(context or [])},
    )
    _need(data, "kind")
    return NextStep(
        kind=data["kind"],
        tool=data.get("tool"),
        args=dict(data.get("args") or {}),
        summary=data.get("summary"),
        requires_approval=bool(data.get("requires_approval", False)),
        reason=data.get("reason"),
    )


# --- /tickets/classify -----------------------------------------------------------


@dataclass(frozen=True)
class TicketLabel:
    ticket_id: Optional[str]
    urgency: Optional[str]
    sentiment: Optional[str]
    team: Optional[str]
    needs_review: bool
    reason: Optional[str] = None


def classify_tickets(tickets: list[dict[str, Any]], teams: Optional[list[str]] = None) -> list[TicketLabel]:
    payload: dict[str, Any] = {"tickets": tickets}
    if teams is not None:
        payload["teams"] = teams
    data = _post("/tickets/classify", payload)
    _need(data, "results")
    return [
        TicketLabel(
            ticket_id=item.get("ticket_id"),
            urgency=item.get("urgency"),
            sentiment=item.get("sentiment"),
            team=item.get("team"),
            needs_review=bool(item.get("needs_review", False)),
            reason=item.get("reason"),
        )
        for item in data["results"]
    ]


# --- /onboarding/map-schema ------------------------------------------------------


@dataclass(frozen=True)
class FieldMappingProposal:
    """status "mapped" is a confident, unique, valid mapping; "needs_review" and
    "unmapped" are for a person to decide."""

    source: str
    status: str
    target: Optional[str] = None
    suggested: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None


def propose_field_mappings(
    source_columns: list[dict[str, Any]],
    target_schema: dict[str, str],
    include_samples: bool = False,
    max_targets: int = 25,
) -> list[FieldMappingProposal]:
    data = _post(
        "/onboarding/map-schema",
        {
            "source_columns": source_columns,
            "target_schema": target_schema,
            "include_samples": include_samples,
            "max_targets": max_targets,
        },
    )
    _need(data, "mappings")
    return [
        FieldMappingProposal(
            source=item["source"],
            status=item["status"],
            target=item.get("target"),
            suggested=item.get("suggested"),
            confidence=item.get("confidence"),
            reason=item.get("reason"),
        )
        for item in data["mappings"]
    ]


# --- /marketing/draft-copy -------------------------------------------------------


@dataclass(frozen=True)
class CopyCheck:
    rule: str
    passed: bool
    detail: Optional[str] = None
    blocking: bool = False


@dataclass(frozen=True)
class CopyDraftResult:
    subject: str
    preview_text: str
    email_body: str
    social_caption: str
    checks: list[CopyCheck] = field(default_factory=list)
    blocked: bool = False


def draft_marketing_copy(
    brief: dict[str, Any],
    guidelines: list[str],
    max_guidelines: Optional[int] = None,
    max_subject: Optional[int] = None,
    max_preview: Optional[int] = None,
    max_body: Optional[int] = None,
    max_caption: Optional[int] = None,
) -> CopyDraftResult:
    payload: dict[str, Any] = {"brief": brief, "guidelines": guidelines}
    limits = {
        "max_guidelines": max_guidelines,
        "max_subject": max_subject,
        "max_preview": max_preview,
        "max_body": max_body,
        "max_caption": max_caption,
    }
    payload.update({name: value for name, value in limits.items() if value is not None})
    data = _post("/marketing/draft-copy", payload)
    _need(data, "subject", "preview_text", "email_body", "social_caption")
    return CopyDraftResult(
        subject=data["subject"],
        preview_text=data["preview_text"],
        email_body=data["email_body"],
        social_caption=data["social_caption"],
        checks=[
            CopyCheck(
                rule=c["rule"],
                passed=bool(c["passed"]),
                detail=c.get("detail"),
                blocking=bool(c.get("blocking", False)),
            )
            for c in data.get("checks") or []
        ],
        blocked=bool(data.get("blocked", False)),
    )


# --- /support/answer -------------------------------------------------------------


@dataclass(frozen=True)
class SupportAnswerResult:
    answer: str
    found: bool
    source_kind: Optional[str] = None
    sources: list[str] = field(default_factory=list)


def answer_support_question(
    question: str,
    kb_passages: list[dict[str, str]],
    web_passages: Optional[list[dict[str, str]]] = None,
    allowed_domain: Optional[str] = None,
) -> SupportAnswerResult:
    payload: dict[str, Any] = {"question": question, "kb_passages": kb_passages}
    if web_passages:
        payload["web_passages"] = web_passages
    if allowed_domain:
        payload["allowed_domain"] = allowed_domain
    data = _post("/support/answer", payload)
    _need(data, "answer", "found")
    return SupportAnswerResult(
        answer=data["answer"],
        found=bool(data["found"]),
        source_kind=data.get("source_kind"),
        sources=list(data.get("sources") or []),
    )


# --- /persona/decide -------------------------------------------------------------


@dataclass(frozen=True)
class PersonaDecisionResult:
    needs_review: bool
    perception: list[str] = field(default_factory=list)
    interpretation: Optional[str] = None
    action: Optional[str] = None
    channel: Optional[str] = None
    rationale: Optional[str] = None
    cited: list[str] = field(default_factory=list)
    reason: Optional[str] = None


def decide_next_best_action(
    evidence: dict[str, Any],
    allowed_actions: Optional[list[str]] = None,
    suppressed: Optional[list[str]] = None,
) -> PersonaDecisionResult:
    payload: dict[str, Any] = {"evidence": evidence}
    if allowed_actions is not None:
        payload["allowed_actions"] = allowed_actions
    if suppressed:
        payload["suppressed"] = suppressed
    data = _post("/persona/decide", payload)
    _need(data, "needs_review")
    return PersonaDecisionResult(
        needs_review=bool(data["needs_review"]),
        perception=list(data.get("perception") or []),
        interpretation=data.get("interpretation"),
        action=data.get("action"),
        channel=data.get("channel"),
        rationale=data.get("rationale"),
        cited=list(data.get("cited") or []),
        reason=data.get("reason"),
    )
