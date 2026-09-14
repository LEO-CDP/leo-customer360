"""Gemini-backed AIProvider: raw HTTP POST to the generateContent endpoint,
no SDK dependency -- same request/response pattern as
tools/docs-vector-search/src/providers.py's _gemini_request.
"""

import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from core.ai_providers.base import AIProvider, AIProviderError
from core.config import settings

_GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_REQUEST_TIMEOUT_SECONDS = 30


class GeminiProvider(AIProvider):
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
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            detail = getattr(exc, "reason", exc)
            raise AIProviderError(f"Gemini request failed: {detail}") from exc

        try:
            parts = body["candidates"][0]["content"]["parts"]
            return "".join(part["text"] for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Gemini response did not contain generated content") from exc
