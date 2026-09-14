"""OpenAI-compatible AIProvider: raw HTTP POST to a chat-completions
endpoint, no SDK dependency -- same request/response pattern as
tools/docs-vector-search/src/providers.py's _openai_request.
"""

import json
import urllib.error
import urllib.request

from core.ai_providers.base import AIProvider, AIProviderError
from core.config import settings

_OPENAI_API_BASE_URL = "https://api.openai.com/v1"
_REQUEST_TIMEOUT_SECONDS = 30


class OpenAIProvider(AIProvider):
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
