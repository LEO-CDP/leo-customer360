"""Single generic LLM provider via the State pattern.

`LLMProvider` (the Context) delegates completion to its current `ProviderState`
(the State) -- a config object carrying the LiteLLM model string, credentials,
and an open-ended `extra_config` dict. Switching the state switches model/creds
with no new class. The model string carries the provider:
    gemini/<model> | openai/<model> | anthropic/<model> | openai/<name> + api_base (local)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ai_providers.base import AIProviderError, complete_with_litellm
from config import settings


@dataclass
class ProviderState:
    """The State = config for one completion. `extra_config` is forwarded verbatim
    to litellm.completion (temperature, max_tokens, timeout, ...)."""

    model: str
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    extra_config: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """A hosted provider needs a key; a local runtime needs a base_url. With
        neither, litellm has nothing to talk to -- fail early with a clear message."""
        if not self.api_key and not self.api_base:
            raise AIProviderError(
                "LLM not configured: set LLM_API_KEY (hosted) or LLM_BASE_URL (local)"
            )


class LLMProvider:
    """Context: delegates complete() to the current ProviderState (State)."""

    def __init__(self, state: ProviderState):
        self._state = state

    @property
    def state(self) -> ProviderState:
        return self._state

    def set_state(self, state: ProviderState) -> None:
        self._state = state

    def complete(self, prompt: str) -> str:
        self._state.validate()
        return complete_with_litellm(
            self._state.model,
            prompt,
            api_key=self._state.api_key,
            api_base=self._state.api_base,
            extra=self._state.extra_config,
        )


def build_state(
    model: Optional[str] = None,
    extra_config: Optional[dict[str, Any]] = None,
) -> ProviderState:
    """Build a ProviderState from `settings`, overriding the model/extra per call.
    `extra_config` is merged on top of the service-wide `settings.llm_extra_config`."""
    return ProviderState(
        model=model or settings.llm_model,
        api_key=settings.llm_api_key or None,
        api_base=settings.llm_base_url or None,
        extra_config={**(settings.llm_extra_config or {}), **(extra_config or {})},
    )
