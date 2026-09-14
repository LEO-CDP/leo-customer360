"""AIProvider interface + safety-check result shape for AI email template
authoring. Concrete providers: gemini_provider.py, openai_provider.py.
Concrete safety-check functions are added incrementally below (see
specs/001-ai-email-template-authoring/tasks.md US1/US3).
"""

from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass
from typing import Optional

from core.config import settings


@dataclass
class EmailGenerationBrief:
    """Input to AIProvider.generate(...) -- one authoring request."""

    segment_context: dict
    objective: str
    tone_brand_constraints: Optional[str] = None
    language: str = "en"
    locale: Optional[str] = None


@dataclass
class GeneratedContent:
    """Structured output of AIProvider.generate(...)."""

    subject: str
    html_body: str
    text_body: str


@dataclass
class SafetyCheckResult:
    rule: str
    passed: bool
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {"rule": self.rule, "passed": self.passed, "detail": self.detail}


class AIProviderError(RuntimeError):
    """Raised on a missing API key, an HTTP/timeout failure, or a response
    that cannot be parsed into the required subject/html_body/text_body
    fields (FR-015 -- callers must not save a partial/empty template)."""


class AIProvider(abc.ABC):
    """One pluggable email-content generation backend (Gemini, OpenAI, ...)."""

    @abc.abstractmethod
    def generate(self, brief: EmailGenerationBrief) -> GeneratedContent:
        """Generate subject/html_body/text_body for the given brief.

        Raises AIProviderError on missing API key, HTTP/timeout failure, or
        an unparsable response.
        """

    @abc.abstractmethod
    def complete(self, prompt: str) -> str:
        """Generic single-turn text completion, with no structured-JSON
        contract of its own -- used by other features (e.g.
        002-ai-campaign-draft-creation's campaign_planner.py) that need a raw
        text response from the same shared provider/credentials, since
        generate() above is specific to the three-field email shape.

        Raises AIProviderError on missing API key or HTTP/timeout failure.
        """


_UNSUBSCRIBE_PLACEHOLDER_TOKENS = ("{{unsubscribe_url}}", "{{unsubscribe_link}}")
_RECIPIENT_NAME_PLACEHOLDER_TOKENS = ("{{first_name}}", "{{recipient_name}}", "{{name}}")


def check_required_placeholders(html_body: str, text_body: str) -> SafetyCheckResult:
    """FR-004: every generated (or edited) draft must contain an
    unsubscribe-link placeholder and a recipient name/personalisation
    placeholder in both bodies, before it is ever saved. Checked
    independently per body -- a token present only in html_body must not
    mask its absence from text_body (or vice versa)."""
    missing = []
    for label, body in (("html_body", html_body), ("text_body", text_body)):
        if not any(token in body for token in _UNSUBSCRIBE_PLACEHOLDER_TOKENS):
            missing.append(f"unsubscribe link placeholder in {label}")
        if not any(token in body for token in _RECIPIENT_NAME_PLACEHOLDER_TOKENS):
            missing.append(f"recipient name/personalisation placeholder in {label}")

    if not missing:
        return SafetyCheckResult(rule="required_placeholders", passed=True)
    return SafetyCheckResult(
        rule="required_placeholders",
        passed=False,
        detail=f"Missing required placeholder(s): {', '.join(missing)}",
    )


def check_content_length(subject: str, html_body: str, text_body: str) -> SafetyCheckResult:
    """FR-012: flags content exceeding the configured maximum length."""
    issues = []
    if len(subject) > settings.crm_email_max_subject_length:
        issues.append(f"subject exceeds {settings.crm_email_max_subject_length} characters")
    if len(html_body) > settings.crm_email_max_body_length:
        issues.append(f"html_body exceeds {settings.crm_email_max_body_length} characters")
    if len(text_body) > settings.crm_email_max_body_length:
        issues.append(f"text_body exceeds {settings.crm_email_max_body_length} characters")

    if not issues:
        return SafetyCheckResult(rule="length", passed=True)
    return SafetyCheckResult(rule="length", passed=False, detail="; ".join(issues))


# Generic marketing-compliance red flags (spec Assumptions: "false or
# misleading statements, guaranteed-outcome language, or other legally risky
# claims"), not an industry-specific claims list -- see research.md's
# content-safety rule-set decision.
_FORBIDDEN_CLAIM_PATTERNS = (
    "guaranteed",
    "100% guaranteed",
    "risk-free",
    "no risk",
    "cure",
    "miracle",
    "guaranteed results",
    "guaranteed to work",
    "clinically proven" ,
    "fda approved",
    "double your money",
    "get rich quick",
)


def check_forbidden_claims(subject: str, html_body: str, text_body: str) -> SafetyCheckResult:
    """FR-012: flags generic marketing-compliance issues (false/misleading
    statements, guaranteed-outcome language) -- advisory, does not block
    approval (spec Assumptions)."""
    combined = f"{subject}\n{html_body}\n{text_body}".lower()
    # Word-boundary match: unanchored substring matching would flag ordinary
    # words like "procure"/"secure"/"accurate" as containing "cure".
    hits = [phrase for phrase in _FORBIDDEN_CLAIM_PATTERNS if re.search(rf"\b{re.escape(phrase)}\b", combined)]
    if not hits:
        return SafetyCheckResult(rule="forbidden_claims", passed=True)
    return SafetyCheckResult(
        rule="forbidden_claims",
        passed=False,
        detail=f"Contains potentially misleading/guaranteed-outcome language: {', '.join(hits)}",
    )


_UNSAFE_HTML_MARKERS = ("<script", "javascript:", "onerror=", "onload=", "<iframe", "<object", "<embed")


def check_html_safety(html_body: str) -> SafetyCheckResult:
    """FR-012: flags embedded scripts or other executable/unsafe markup."""
    lowered = html_body.lower()
    hits = [marker for marker in _UNSAFE_HTML_MARKERS if marker in lowered]
    if not hits:
        return SafetyCheckResult(rule="html_safety", passed=True)
    return SafetyCheckResult(
        rule="html_safety",
        passed=False,
        detail=f"Contains unsafe/executable HTML markup: {', '.join(hits)}",
    )


def run_safety_checks(subject: str, html_body: str, text_body: str) -> list[SafetyCheckResult]:
    """Aggregates every safety check (FR-012/FR-013): required placeholders,
    length, forbidden claims, HTML safety."""
    return [
        check_required_placeholders(html_body, text_body),
        check_content_length(subject, html_body, text_body),
        check_forbidden_claims(subject, html_body, text_body),
        check_html_safety(html_body),
    ]


def parse_json_object(raw_text: str) -> dict:
    """Strips an optional ```/```json fence and parses a JSON object,
    raising AIProviderError (not json.JSONDecodeError) on failure so every
    provider surfaces the same error type to its caller."""
    content = raw_text.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"AI provider response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AIProviderError("AI provider response JSON was not an object")
    return parsed
