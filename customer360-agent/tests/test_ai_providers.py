"""Unit tests for ai_providers: complete_with_litellm(), parse_json_object() plus 
the generic State-pattern provider (ProviderState / LLMProvider / build_state) 
over a mocked LiteLLM SDK."""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# The provider tests patch litellm.completion, so the SDK must be importable.
pytest.importorskip("litellm")

from ai_providers.base import AIProviderError, parse_json_object, complete_with_litellm
from ai_providers.provider import LLMProvider, ProviderState, build_state


def _fake_completion(text: str):
    """Mimic the OpenAI-shaped object LiteLLM returns."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class CompleteWithLitellmTests(unittest.TestCase):
    @patch("litellm.completion")
    def test_complete_success_with_default_temperature(self, mock_completion):
        mock_completion.return_value = _fake_completion("Expected generated text")
        
        result = complete_with_litellm(
            model="openai/gpt-5.6-luna", 
            prompt="Hello", 
            api_key="sk-test"
        )
        
        self.assertEqual(result, "Expected generated text")
        mock_completion.assert_called_once_with(
            model="openai/gpt-5.6-luna",
            messages=[{"role": "user", "content": "Hello"}],
            api_key="sk-test",
            api_base=None,
            temperature=0.4  # Asserting default params are passed
        )

    @patch("litellm.completion")
    def test_raises_ai_provider_error_on_api_failure(self, mock_completion):
        mock_completion.side_effect = Exception("API Timeout")
        
        with self.assertRaisesRegex(AIProviderError, "request failed: API Timeout"):
            complete_with_litellm("openai/gpt-5.6-luna", "prompt")

    @patch("litellm.completion")
    def test_raises_ai_provider_error_on_empty_content(self, mock_completion):
        mock_completion.return_value = _fake_completion("   \n   ")
        
        with self.assertRaisesRegex(AIProviderError, "did not contain generated content"):
            complete_with_litellm("openai/gpt-5.6-luna", "prompt")


class ParseJsonObjectTests(unittest.TestCase):
    def test_parses_plain_json(self):
        self.assertEqual(parse_json_object('{"name": "Q4 Win-Back"}'), {"name": "Q4 Win-Back"})

    def test_strips_json_labeled_code_fence(self):
        self.assertEqual(parse_json_object('```json\n{"name": "Q4 Win-Back"}\n```'), {"name": "Q4 Win-Back"})

    def test_strips_malformed_unclosed_fence(self):
        # Starts a fence but doesn't close it, which was handled by the split logic
        raw = '```json\n{"incomplete": "fence"}'
        self.assertEqual(parse_json_object(raw), {"incomplete": "fence"})

    def test_raises_ai_provider_error_on_invalid_json(self):
        with self.assertRaises(AIProviderError):
            parse_json_object("not json")

    def test_raises_ai_provider_error_on_non_object_json(self):
        with self.assertRaises(AIProviderError):
            parse_json_object("[1, 2, 3]")

    def test_lone_fence_raises_ai_provider_error_not_index_error(self):
        with self.assertRaises(AIProviderError):
            parse_json_object("```")


class ProviderStateTests(unittest.TestCase):
    def test_validate_ok_with_api_key(self):
        ProviderState("gemini/gemini-2.5-flash", api_key="k").validate()

    def test_validate_ok_with_local_base_url_no_key(self):
        ProviderState("openai/llama3.1", api_key="", api_base="http://localhost:11434/v1").validate()

    def test_validate_raises_without_key_or_base(self):
        with self.assertRaises(AIProviderError):
            ProviderState("gemini/gemini-2.5-flash", api_key="", api_base="").validate()


class LLMProviderTests(unittest.TestCase):
    def test_complete_passes_model_base_and_merged_extra(self):
        state = ProviderState(
            "openai/llama3.1", api_key="", api_base="http://localhost:11434/v1",
            extra_config={"max_tokens": 512},
        )
        with patch("litellm.completion", return_value=_fake_completion("ok")) as mock_completion:
            result = LLMProvider(state).complete("prompt")
        self.assertEqual(result, "ok")
        kwargs = mock_completion.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/llama3.1")
        self.assertEqual(kwargs["api_base"], "http://localhost:11434/v1")
        self.assertEqual(kwargs["max_tokens"], 512)  # extra_config forwarded

    def test_set_state_switches_model(self):
        provider = LLMProvider(ProviderState("gemini/g", api_key="k"))
        with patch("litellm.completion", return_value=_fake_completion("x")) as mock_completion:
            provider.complete("p")
            self.assertEqual(mock_completion.call_args.kwargs["model"], "gemini/g")
            provider.set_state(ProviderState("anthropic/c", api_key="k"))
            provider.complete("p")
            self.assertEqual(mock_completion.call_args.kwargs["model"], "anthropic/c")

    def test_unconfigured_raises_before_sdk(self):
        with patch("litellm.completion") as mock_completion:
            with self.assertRaises(AIProviderError):
                LLMProvider(ProviderState("gemini/g", api_key="", api_base="")).complete("p")
            mock_completion.assert_not_called()


class BuildStateTests(unittest.TestCase):
    def test_reads_settings_and_overrides_model(self):
        with patch("ai_providers.provider.settings") as s:
            s.llm_model = "gemini/gemini-2.5-flash"
            s.llm_api_key = "gk"
            s.llm_base_url = ""
            s.llm_extra_config = {}
            state = build_state(model="anthropic/claude-3-5-sonnet-latest")
        self.assertEqual(state.model, "anthropic/claude-3-5-sonnet-latest")  # per-call override wins
        self.assertEqual(state.api_key, "gk")

    def test_extra_config_merges_settings_then_per_call(self):
        with patch("ai_providers.provider.settings") as s:
            s.llm_model = "gemini/g"
            s.llm_api_key = "gk"
            s.llm_base_url = ""
            s.llm_extra_config = {"timeout": 30, "max_tokens": 100}
            state = build_state(extra_config={"max_tokens": 999})
        self.assertEqual(state.extra_config, {"timeout": 30, "max_tokens": 999})


class AgentSeedModelTests(unittest.TestCase):
    def test_generative_agent_seeds_use_litellm_openai_model_identifier(self):
        repository_root = Path(__file__).resolve().parents[2]
        seed_paths = (
            repository_root / "customer360-database" / "init-core-database.sql",
            repository_root / "customer360-database" / "init-prompt-store-seed.sql",
        )

        for seed_path in seed_paths:
            models = re.findall(
                r"'generative_llm',\s*'([^']+)'", seed_path.read_text(encoding="utf-8")
            )
            self.assertTrue(models, f"Expected generative agent seeds in {seed_path}")
            self.assertEqual(
                models,
                ["openai/gpt-5.6-luna"] * len(models),
                f"Generative agent seed models must match LiteLLM's OpenAI identifier in {seed_path}",
            )


if __name__ == "__main__":
    unittest.main()