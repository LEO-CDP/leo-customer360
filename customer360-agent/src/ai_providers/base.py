"""Shared LLM primitives: the error type, the LiteLLM call, and JSON parsing.

The concrete provider is a single generic State-pattern class in
ai_providers/provider.py; everything routes through complete_with_litellm()
here, whose unified LiteLLM call speaks every provider's wire format.
"""

from __future__ import annotations

import json
from typing import Any, Optional


class AIProviderError(RuntimeError):
    """Raised on a missing API key, an SDK/HTTP/timeout failure, or a response
    that cannot be parsed into the caller's expected shape."""


def complete_with_litellm(
    model: str,
    prompt: str,
    *,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    import litellm  # deferred: heavy import, and only needed at completion time

    # Reasoning models (e.g. gpt-5.x) reject temperature != 1; drop_params makes LiteLLM
    # silently drop any param a given model doesn't support instead of raising.
    litellm.drop_params = True

    params: dict[str, Any] = {"temperature": 0.4}
    params.update(extra or {})
    try:
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key or None,
            api_base=api_base or None,
            **params,
        )
    except Exception as exc:  # LiteLLM raises many provider-specific types
        raise AIProviderError(f"{model} request failed: {exc}") from exc

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise AIProviderError(f"{model} response did not contain generated content") from exc
    if not isinstance(content, str) or not content.strip():
        raise AIProviderError(f"{model} response did not contain generated content")
    return content


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
