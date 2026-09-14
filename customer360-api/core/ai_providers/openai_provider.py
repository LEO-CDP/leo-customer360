"""OpenAI-compatible AIProvider: raw HTTP POST to a chat-completions
endpoint, no SDK dependency -- same request/response pattern as
tools/docs-vector-search/src/providers.py's _openai_request, adapted to
return a structured subject/html_body/text_body GeneratedContent.
"""

import json
import urllib.error
import urllib.request

from core.ai_providers.base import (
    AIProvider,
    AIProviderError,
    EmailGenerationBrief,
    GeneratedContent,
    parse_json_object,
)
from core.config import settings

_OPENAI_API_BASE_URL = "https://api.openai.com/v1"
_REQUEST_TIMEOUT_SECONDS = 30

_SYSTEM_INSTRUCTION = (
    "You are an email marketing copywriter. Given a segment description, an "
    "objective, and optional tone/brand constraints, write one marketing "
    "email. Respond with ONLY a JSON object with exactly three string keys: "
    '"subject", "html_body", "text_body". html_body and text_body MUST each '
    "include the literal placeholder tokens {{first_name}} and "
    "{{unsubscribe_url}}; never invent recipient data."
)


def _build_user_prompt(brief: EmailGenerationBrief) -> str:
    lines = [
        f"Segment: {brief.segment_context}",
        f"Objective: {brief.objective}",
        f"Language: {brief.language}",
    ]
    if brief.locale:
        lines.append(f"Locale: {brief.locale}")
    if brief.tone_brand_constraints:
        lines.append(f"Tone/brand constraints: {brief.tone_brand_constraints}")
    return "\n".join(lines)


class OpenAIProvider(AIProvider):
    def generate(self, brief: EmailGenerationBrief) -> GeneratedContent:
        if not settings.openai_api_key:
            raise AIProviderError("OpenAI provider selected but OPENAI_API_KEY is not configured")

        payload = {
            "model": settings.openai_model,
            "messages": [
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": _build_user_prompt(brief)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
        }
        request = urllib.request.Request(
            f"{_OPENAI_API_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            detail = getattr(exc, "reason", exc)
            raise AIProviderError(f"OpenAI request failed: {detail}") from exc

        try:
            raw_text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("OpenAI response did not contain generated content") from exc

        parsed = parse_json_object(raw_text)
        try:
            return GeneratedContent(
                subject=str(parsed["subject"]),
                html_body=str(parsed["html_body"]),
                text_body=str(parsed["text_body"]),
            )
        except KeyError as exc:
            raise AIProviderError(f"OpenAI response JSON missing required field: {exc}") from exc

    def complete(self, prompt: str) -> str:
        if not settings.openai_api_key:
            raise AIProviderError("OpenAI provider selected but OPENAI_API_KEY is not configured")

        payload = {
            "model": settings.openai_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        }
        request = urllib.request.Request(
            f"{_OPENAI_API_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            detail = getattr(exc, "reason", exc)
            raise AIProviderError(f"OpenAI request failed: {detail}") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("OpenAI response did not contain generated content") from exc
