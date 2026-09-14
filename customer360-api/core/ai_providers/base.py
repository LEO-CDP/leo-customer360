"""AIProvider interface shared by every AI-backed feature in this API.
Concrete providers: gemini_provider.py, openai_provider.py. Both expose a
single generic complete(prompt) -> str method, consumed by
core/ai_providers/campaign_planner.py (002-ai-campaign-draft-creation).
"""

from __future__ import annotations

import abc
import json


class AIProviderError(RuntimeError):
    """Raised on a missing API key, an HTTP/timeout failure, or a response
    that cannot be parsed into the caller's expected shape."""


class AIProvider(abc.ABC):
    """One pluggable text-completion backend (Gemini, OpenAI, ...)."""

    @abc.abstractmethod
    def complete(self, prompt: str) -> str:
        """Generic single-turn text completion, with no structured-JSON
        contract of its own -- callers (e.g. campaign_planner.py) define
        their own prompt/response shape on top of this.

        Raises AIProviderError on missing API key or HTTP/timeout failure.
        """


def parse_json_object(raw_text: str) -> dict:
    """Strips an optional ```/```json fence and parses a JSON object,
    raising AIProviderError (not json.JSONDecodeError) on failure so every
    provider surfaces the same error type to its caller."""
    content = raw_text.strip()
    if content.startswith("```"):
        # A malformed/truncated fence (e.g. a lone "```" with no body or
        # closing fence) must still fall through to json.loads() below, not
        # raise IndexError here -- that would skip the AIProviderError
        # handling and surface as a 500 instead of the intended 409/502.
        parts = content.split("\n", 1)
        content = parts[1] if len(parts) > 1 else ""
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"AI provider response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AIProviderError("AI provider response JSON was not an object")
    return parsed
