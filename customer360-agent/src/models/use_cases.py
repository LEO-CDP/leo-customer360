"""Contracts for the use-case agent endpoints.

Every request is bounded and rejects unknown fields. None carries a tenant or
database access: the caller (customer360-api) owns both, resolves the tenant,
runs any SQL in its own session and sends this service only the data an agent
needs. Responses report model failures as data (`needs_review`, `not_found`,
`kind: error`) where the agent has a safe default; an unusable provider reply
elsewhere becomes a 502 through the app-wide handler."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_HTTP_URL = re.compile(r"^https?://", re.IGNORECASE)

# Direct identifiers never belong in persona evidence: the decision is made from
# behaviour and scores, so a field like these is a caller bug, not data to forward.
_DIRECT_IDENTIFIERS = frozenset(
    "email phone phone_number full_name first_name last_name national_id address date_of_birth "
    "secondary_emails secondary_phones".split()
)
MAX_EVIDENCE_CHARS = 32_000
MAX_ROWS_CHARS = 200_000
MAX_SCHEMA_CHARS = 200_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _json_size(value: Any) -> int:
    return len(json.dumps(value, default=str))


# ---- /analytics/sql ----


class SqlTurn(_Strict):
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=4000)


class SqlRequest(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question: str = Field(min_length=1, max_length=2000)
    # Table name to retrieval doc. Sent as "schema"; named schema_docs to avoid
    # shadowing BaseModel.schema.
    schema_docs: dict[str, str] = Field(alias="schema", min_length=1, max_length=200)
    history: list[SqlTurn] = Field(default_factory=list, max_length=5)
    k: int = Field(default=3, ge=1, le=10)

    @field_validator("schema_docs")
    @classmethod
    def _bounded_schema(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not name or len(name) > 128 for name in value):
            raise ValueError("table names must be 1 to 128 characters")
        if any(len(doc) > 3000 for doc in value.values()):
            raise ValueError("each schema doc must not exceed 3000 characters")
        if sum(len(n) + len(d) for n, d in value.items()) > MAX_SCHEMA_CHARS:
            raise ValueError(f"schema must not exceed {MAX_SCHEMA_CHARS} characters")
        return value


class SqlResponse(BaseModel):
    status: Literal["ok", "not_found", "refused"]
    sql: Optional[str] = None
    error: Optional[str] = None


# ---- /analytics/narrative ----


class NarrativeRequest(_Strict):
    question: str = Field(min_length=1, max_length=2000)
    rows: list[dict[str, Any]] = Field(max_length=200)

    @field_validator("rows")
    @classmethod
    def _bounded_rows(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if _json_size(value) > MAX_ROWS_CHARS:
            raise ValueError(f"rows must not exceed {MAX_ROWS_CHARS} characters")
        return value


class NarrativeResponse(BaseModel):
    narrative: str
    ungrounded: list[str]
    chart: Optional[dict[str, Any]] = None


# ---- /marketing/orchestrate/step ----


class ToolSpecIn(_Strict):
    name: str
    description: str = Field(min_length=1, max_length=500)
    required: list[str] = Field(default_factory=list, max_length=10)
    side_effect: bool = False

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("tool names use letters, digits, underscore, dot or hyphen, and start with a letter")
        return value

    @field_validator("required")
    @classmethod
    def _valid_required(cls, value: list[str]) -> list[str]:
        if any(not _NAME.match(a) for a in value):
            raise ValueError("required argument names use letters, digits, underscore, dot or hyphen")
        return value


class StepIn(_Strict):
    tool: Optional[str] = Field(default=None, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    observation: str = Field(default="", max_length=20_000)
    executed: bool = False


class OrchestrateStepRequest(_Strict):
    goal: str = Field(min_length=1, max_length=2000)
    tools: list[ToolSpecIn] = Field(min_length=1, max_length=30)
    steps: list[StepIn] = Field(default_factory=list, max_length=20)
    context: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _unique_tools_and_bounded_context(self) -> OrchestrateStepRequest:
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique within a request")
        if any(len(c) > 2000 for c in self.context):
            raise ValueError("each context snippet must not exceed 2000 characters")
        return self


class NextStepResponse(BaseModel):
    kind: Literal["tool", "finish", "error"]
    tool: Optional[str] = None
    args: dict[str, Any] = Field(default_factory=dict)
    summary: Optional[str] = None
    requires_approval: bool = False
    reason: Optional[str] = None


# ---- /tickets/classify ----


class TicketIn(_Strict):
    id: Optional[str] = Field(default=None, max_length=128)
    text: str = Field(max_length=20_000)


class TicketsRequest(_Strict):
    tickets: list[TicketIn] = Field(min_length=1, max_length=50)
    teams: Optional[list[str]] = Field(default=None, min_length=1, max_length=20)

    @field_validator("teams")
    @classmethod
    def _valid_teams(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is not None and any(not t.strip() or len(t) > 64 for t in value):
            raise ValueError("team names must be 1 to 64 characters")
        return value


class TicketResult(BaseModel):
    ticket_id: Optional[str] = None
    urgency: Optional[str] = None
    sentiment: Optional[str] = None
    team: Optional[str] = None
    needs_review: bool
    reason: Optional[str] = None


class TicketsResponse(BaseModel):
    results: list[TicketResult]


# ---- /onboarding/map-schema ----


class SourceColumn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    samples: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("samples")
    @classmethod
    def _short_samples(cls, value: list[str]) -> list[str]:
        if any(len(s) > 200 for s in value):
            raise ValueError("each sample must not exceed 200 characters")
        return value


class MapSchemaRequest(_Strict):
    source_columns: list[SourceColumn] = Field(min_length=1, max_length=300)
    target_schema: dict[str, str] = Field(min_length=1, max_length=500)
    include_samples: bool = False
    max_targets: int = Field(default=25, ge=1, le=100)

    @field_validator("target_schema")
    @classmethod
    def _bounded_targets(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not name or len(name) > 128 for name in value):
            raise ValueError("target field names must be 1 to 128 characters")
        if any(len(doc) > 2000 for doc in value.values()):
            raise ValueError("each target doc must not exceed 2000 characters")
        return value


class MappingOut(BaseModel):
    source: str
    target: Optional[str] = None
    suggested: Optional[str] = None
    confidence: Optional[float] = None
    status: Literal["mapped", "needs_review", "unmapped"]
    reason: Optional[str] = None


class MapSchemaResponse(BaseModel):
    mappings: list[MappingOut]


# ---- /marketing/draft-copy ----


class CopyBrief(_Strict):
    objective: str = Field(min_length=1, max_length=1000)
    audience: str = Field(default="", max_length=1000)


class DraftCopyRequest(_Strict):
    brief: CopyBrief
    guidelines: list[str] = Field(default_factory=list, max_length=50)
    max_guidelines: int = Field(default=5, ge=1, le=20)
    max_subject: int = Field(default=120, ge=20, le=300)
    max_preview: int = Field(default=150, ge=20, le=500)
    max_body: int = Field(default=10_000, ge=200, le=50_000)
    max_caption: int = Field(default=280, ge=20, le=1000)

    @field_validator("guidelines")
    @classmethod
    def _short_guidelines(cls, value: list[str]) -> list[str]:
        if any(len(g) > 2000 for g in value):
            raise ValueError("each guideline must not exceed 2000 characters")
        return value


class CheckOut(BaseModel):
    rule: str
    passed: bool
    detail: Optional[str] = None
    blocking: bool = False


class DraftCopyResponse(BaseModel):
    subject: str
    preview_text: str
    email_body: str
    social_caption: str
    checks: list[CheckOut]
    blocked: bool


# ---- /support/answer ----


class PassageIn(_Strict):
    url: str = Field(max_length=2000)
    title: str = Field(default="", max_length=500)
    text: str = Field(max_length=20_000)

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if not _HTTP_URL.match(value):
            raise ValueError("url must start with http:// or https://")
        return value


class SupportAnswerRequest(_Strict):
    question: str = Field(min_length=1, max_length=2000)
    kb_passages: list[PassageIn] = Field(default_factory=list, max_length=10)
    web_passages: list[PassageIn] = Field(default_factory=list, max_length=10)
    allowed_domain: Optional[str] = None

    @field_validator("allowed_domain")
    @classmethod
    def _hostname(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _HOST.match(value):
            raise ValueError("allowed_domain must be a hostname such as help.example.com")
        return value


class SupportAnswerResponse(BaseModel):
    answer: str
    found: bool
    source_kind: Optional[Literal["kb", "web"]] = None
    sources: list[str] = Field(default_factory=list)


# ---- /persona/decide ----


def _find_identifier(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            if str(key).lower() in _DIRECT_IDENTIFIERS:
                return str(key)
            found = _find_identifier(inner)
            if found:
                return found
    elif isinstance(value, list):
        for inner in value:
            found = _find_identifier(inner)
            if found:
                return found
    return None


class PersonaEvidence(_Strict):
    """Behavioural evidence for one customer. Sections the caller has nothing for stay empty."""

    persona: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    transactions: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _no_identifiers_and_bounded(self) -> PersonaEvidence:
        sections = self.model_dump()
        found = _find_identifier(sections)
        if found:
            raise ValueError(f"evidence must not contain direct identifiers (found {found!r})")
        if _json_size(sections) > MAX_EVIDENCE_CHARS:
            raise ValueError(f"evidence must not exceed {MAX_EVIDENCE_CHARS} characters")
        return self


class PersonaDecideRequest(_Strict):
    evidence: PersonaEvidence
    allowed_actions: Optional[list[str]] = Field(default=None, min_length=1, max_length=20)
    suppressed: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("allowed_actions", "suppressed")
    @classmethod
    def _short_actions(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is not None and any(not a.strip() or len(a) > 300 for a in value):
            raise ValueError("actions must be 1 to 300 characters")
        return value


class PersonaDecisionResponse(BaseModel):
    perception: list[str] = Field(default_factory=list)
    interpretation: Optional[str] = None
    action: Optional[str] = None
    channel: Optional[str] = None
    rationale: Optional[str] = None
    cited: list[str] = Field(default_factory=list)
    needs_review: bool
    reason: Optional[str] = None
