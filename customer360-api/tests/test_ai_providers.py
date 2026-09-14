"""Unit tests for core.ai_providers: GeminiProvider/OpenAIProvider's
complete() (mocked HTTP responses, no live network) and parse_json_object().
"""

import json
import unittest
import urllib.error
from unittest.mock import patch

from core.ai_providers.base import AIProviderError, parse_json_object
from core.ai_providers.gemini_provider import GeminiProvider
from core.ai_providers.openai_provider import OpenAIProvider


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class ParseJsonObjectTests(unittest.TestCase):
    def test_parses_plain_json(self):
        parsed = parse_json_object('{"name": "Q4 Win-Back"}')
        self.assertEqual(parsed, {"name": "Q4 Win-Back"})

    def test_strips_json_labeled_code_fence(self):
        parsed = parse_json_object('```json\n{"name": "Q4 Win-Back"}\n```')
        self.assertEqual(parsed, {"name": "Q4 Win-Back"})

    def test_strips_plain_code_fence(self):
        parsed = parse_json_object('```\n{"name": "Q4 Win-Back"}\n```')
        self.assertEqual(parsed, {"name": "Q4 Win-Back"})

    def test_raises_ai_provider_error_on_invalid_json(self):
        with self.assertRaises(AIProviderError):
            parse_json_object("not json")

    def test_raises_ai_provider_error_on_non_object_json(self):
        with self.assertRaises(AIProviderError):
            parse_json_object("[1, 2, 3]")

    def test_lone_fence_raises_ai_provider_error_not_index_error(self):
        """Regression: a lone "```" (no newline, no body) used to crash with
        an uncaught IndexError from content.split("\\n", 1)[1], skipping the
        AIProviderError handling entirely and surfacing as a 500."""
        with self.assertRaises(AIProviderError):
            parse_json_object("```")

    def test_fence_with_no_closing_marker_raises_ai_provider_error(self):
        with self.assertRaises(AIProviderError):
            parse_json_object("```json\n{not valid")


class GeminiProviderCompleteTests(unittest.TestCase):
    def test_complete_success(self):
        response_payload = {"candidates": [{"content": {"parts": [{"text": "generated text"}]}}]}
        with patch("core.ai_providers.gemini_provider.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.gemini_model = "gemini-2.5-flash"
            with patch(
                "core.ai_providers.gemini_provider.urllib.request.urlopen",
                return_value=_FakeHTTPResponse(response_payload),
            ):
                result = GeminiProvider().complete("some prompt")

        self.assertEqual(result, "generated text")

    def test_missing_api_key_raises(self):
        with patch("core.ai_providers.gemini_provider.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            with self.assertRaises(AIProviderError):
                GeminiProvider().complete("some prompt")

    def test_http_error_raises_provider_error(self):
        with patch("core.ai_providers.gemini_provider.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.gemini_model = "gemini-2.5-flash"
            with patch(
                "core.ai_providers.gemini_provider.urllib.request.urlopen",
                side_effect=urllib.error.URLError("boom"),
            ):
                with self.assertRaises(AIProviderError):
                    GeminiProvider().complete("some prompt")

    def test_unparsable_response_raises_provider_error(self):
        with patch("core.ai_providers.gemini_provider.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.gemini_model = "gemini-2.5-flash"
            with patch(
                "core.ai_providers.gemini_provider.urllib.request.urlopen",
                return_value=_FakeHTTPResponse({"unexpected": "shape"}),
            ):
                with self.assertRaises(AIProviderError):
                    GeminiProvider().complete("some prompt")


class OpenAIProviderCompleteTests(unittest.TestCase):
    def test_complete_success(self):
        response_payload = {"choices": [{"message": {"content": "generated text"}}]}
        with patch("core.ai_providers.openai_provider.settings") as mock_settings:
            mock_settings.openai_api_key = "test-key"
            mock_settings.openai_model = "gpt-5.6-luna"
            with patch(
                "core.ai_providers.openai_provider.urllib.request.urlopen",
                return_value=_FakeHTTPResponse(response_payload),
            ):
                result = OpenAIProvider().complete("some prompt")

        self.assertEqual(result, "generated text")

    def test_missing_api_key_raises(self):
        with patch("core.ai_providers.openai_provider.settings") as mock_settings:
            mock_settings.openai_api_key = ""
            with self.assertRaises(AIProviderError):
                OpenAIProvider().complete("some prompt")

    def test_http_error_raises_provider_error(self):
        with patch("core.ai_providers.openai_provider.settings") as mock_settings:
            mock_settings.openai_api_key = "test-key"
            mock_settings.openai_model = "gpt-5.6-luna"
            with patch(
                "core.ai_providers.openai_provider.urllib.request.urlopen",
                side_effect=urllib.error.URLError("boom"),
            ):
                with self.assertRaises(AIProviderError):
                    OpenAIProvider().complete("some prompt")

    def test_unparsable_response_raises_provider_error(self):
        with patch("core.ai_providers.openai_provider.settings") as mock_settings:
            mock_settings.openai_api_key = "test-key"
            mock_settings.openai_model = "gpt-5.6-luna"
            with patch(
                "core.ai_providers.openai_provider.urllib.request.urlopen",
                return_value=_FakeHTTPResponse({"unexpected": "shape"}),
            ):
                with self.assertRaises(AIProviderError):
                    OpenAIProvider().complete("some prompt")


if __name__ == "__main__":
    unittest.main()
