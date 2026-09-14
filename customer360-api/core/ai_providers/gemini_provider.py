"""Gemini-backed AIProvider: raw HTTP POST to the generateContent endpoint,
no SDK dependency -- same request/response pattern as
tools/docs-vector-search/src/providers.py's _gemini_request, adapted to
return a structured subject/html_body/text_body GeneratedContent.
"""

import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from core.ai_providers.base import (
    AIProvider,
    AIProviderError,
    EmailGenerationBrief,
    GeneratedContent,
    parse_json_object,
)
from core.config import settings

_GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_REQUEST_TIMEOUT_SECONDS = 30

_SYSTEM_INSTRUCTION = (
    "You are an email marketing copywriter. Given a segment description, an "
    "objective, and optional tone/brand constraints, write one marketing "
    "email. Respond with ONLY a JSON object with exactly three string keys: "
    '"subject", "html_body", "text_body". html_body and text_body MUST each '
    "include the literal placeholder tokens {{first_name}} and "
    "{{unsubscribe_url}}; never invent recipient data. Do not wrap the JSON "
    "in markdown code fences."
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


class GeminiProvider(AIProvider):
    def generate(self, brief: EmailGenerationBrief) -> GeneratedContent:
        if not settings.gemini_api_key:
            raise AIProviderError("Gemini provider selected but GEMINI_API_KEY is not configured")

        url = (
            f"{_GEMINI_API_BASE_URL}/models/{quote(settings.gemini_model, safe='')}:generateContent"
            f"?{urlencode({'key': settings.gemini_api_key})}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": _build_user_prompt(brief)}]}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            detail = getattr(exc, "reason", exc)
            raise AIProviderError(f"Gemini request failed: {detail}") from exc

        try:
            parts = body["candidates"][0]["content"]["parts"]
            raw_text = "".join(part["text"] for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Gemini response did not contain generated content") from exc

        parsed = parse_json_object(raw_text)
        try:
            return GeneratedContent(
                subject=str(parsed["subject"]),
                html_body=str(parsed["html_body"]),
                text_body=str(parsed["text_body"]),
            )
        except KeyError as exc:
            raise AIProviderError(f"Gemini response JSON missing required field: {exc}") from exc

    def complete(self, prompt: str) -> str:
        if not settings.gemini_api_key:
            raise AIProviderError("Gemini provider selected but GEMINI_API_KEY is not configured")

        url = (
            f"{_GEMINI_API_BASE_URL}/models/{quote(settings.gemini_model, safe='')}:generateContent"
            f"?{urlencode({'key': settings.gemini_api_key})}"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            detail = getattr(exc, "reason", exc)
            raise AIProviderError(f"Gemini request failed: {detail}") from exc

        try:
            parts = body["candidates"][0]["content"]["parts"]
            return "".join(part["text"] for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Gemini response did not contain generated content") from exc
