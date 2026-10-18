"""Use case 4: brand-grounded marketing copy drafting.

Produces a subject line, preview text, email body and social caption from a
brief, grounded in the most relevant brand-guideline snippets. Checks follow
specs/001-ai-email-template-authoring: required placeholders are enforced,
unsafe HTML blocks the draft, and length, claim and buzzword problems are
flagged for the reviewer without being rewritten.

The rules come from the published instruction (prompt store). The placeholder
tokens, guidelines and brief are appended here."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ai_providers.base import AIProviderError, parse_json_object
from use_cases.instructions import instructions
from use_cases.text_clean import strip_em_dashes
from use_cases.text_to_sql import select_relevant_tables

UNSUBSCRIBE = "{{unsubscribe_url}}"
NAME = "{{first_name}}"
_FIELDS = ("subject", "preview_text", "email_body", "social_caption")

_CLAIMS = re.compile(
    r"\bguarantee[sd]?\b|\b100%|\brisk[- ]free\b|\bno risk\b|\binstant results?\b|\bget rich\b",
    re.IGNORECASE,
)
_BUZZWORDS = re.compile(
    r"\bai[- ]powered\b|\bnext[- ]generation\b|\brevolutionary\b|\bseamless(?:ly)?\b|\bcutting[- ]edge\b"
    r"|\bintelligent\b|\bultimate\b|\bpowerful\b|\beffortless(?:ly)?\b",
    re.IGNORECASE,
)
_UNSAFE_HTML = re.compile(r"<\s*(?:script|iframe|object|embed)\b|javascript:|\bon\w+\s*=", re.IGNORECASE)

_DATA = """Tokens:
Greeting token: {name}
Unsubscribe token: {unsubscribe}

Brand guidelines:
{guidelines}

Objective: {objective}
Audience: {audience}
"""


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class Check:
    rule: str
    passed: bool
    detail: Optional[str] = None
    blocking: bool = False


@dataclass(frozen=True)
class CopyDraft:
    subject: str
    preview_text: str
    email_body: str
    social_caption: str
    checks: list[Check]
    blocked: bool


def _clean(text: str) -> str:
    return strip_em_dashes(text)


def _enforce_placeholders(body: str) -> tuple[str, Optional[str]]:
    added = []
    if NAME not in body:
        body = f"Hi {NAME},\n\n{body}"
        added.append("first_name")
    if UNSUBSCRIBE not in body:
        body = f"{body}\n\nUnsubscribe: {UNSUBSCRIBE}"
        added.append("unsubscribe_url")
    return body, (f"added: {', '.join(added)}" if added else None)


def _matches(pattern: re.Pattern[str], fields: dict[str, str]) -> list[str]:
    return [m.group(0) for text in fields.values() for m in pattern.finditer(text)]


def draft_copy(
    brief: dict[str, Any],
    guidelines: list[str],
    provider: Provider,
    max_guidelines: int = 5,
    max_subject: int = 120,
    max_preview: int = 150,
    max_body: int = 10_000,
    max_caption: int = 280,
) -> CopyDraft:
    snippets = {f"guideline_{i}": g for i, g in enumerate(guidelines)}
    query = f"{brief.get('objective', '')} {brief.get('audience', '')}"
    chosen = select_relevant_tables(query, snippets, k=max_guidelines)
    prompt = f"{instructions('copy')}\n\n" + _DATA.format(
        name=NAME,
        unsubscribe=UNSUBSCRIBE,
        guidelines="\n".join(f"- {snippets[c]}" for c in chosen),
        objective=brief.get("objective", ""),
        audience=brief.get("audience", ""),
    )

    data = parse_json_object(provider.complete(prompt))
    missing = [f for f in _FIELDS if not isinstance(data.get(f), str)]
    if missing:
        raise AIProviderError(f"AI provider response missing fields: {', '.join(missing)}")

    fields = {f: _clean(data[f]) for f in _FIELDS}
    fields["email_body"], added = _enforce_placeholders(fields["email_body"])

    limits = {"subject": max_subject, "preview_text": max_preview, "email_body": max_body, "social_caption": max_caption}
    too_long = [f"{f} is {len(fields[f])} characters (max {m})" for f, m in limits.items() if len(fields[f]) > m]
    claims = _matches(_CLAIMS, fields)
    hype = _matches(_BUZZWORDS, fields)
    unsafe = _matches(_UNSAFE_HTML, fields)

    checks = [
        Check("placeholders", True, added),
        Check("length", not too_long, "; ".join(too_long) or None),
        Check("forbidden_claims", not claims, ", ".join(claims) or None),
        Check("buzzwords", not hype, ", ".join(hype) or None),
        Check("html_safety", not unsafe, ", ".join(unsafe) or None, blocking=True),
    ]
    return CopyDraft(
        subject=fields["subject"],
        preview_text=fields["preview_text"],
        email_body=fields["email_body"],
        social_caption=fields["social_caption"],
        checks=checks,
        blocked=any(c.blocking and not c.passed for c in checks),
    )
