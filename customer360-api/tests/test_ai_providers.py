"""Unit tests for core.ai_providers: GeminiProvider/OpenAIProvider (mocked
HTTP responses, no live network) and the required-placeholders safety check.
"""

import json
import unittest
import urllib.error
from unittest.mock import patch

from core.ai_providers.base import (
    AIProviderError,
    EmailGenerationBrief,
    check_content_length,
    check_forbidden_claims,
    check_html_safety,
    check_required_placeholders,
    run_safety_checks,
)
from core.ai_providers.gemini_provider import GeminiProvider
from core.ai_providers.openai_provider import OpenAIProvider


def _brief(**overrides) -> EmailGenerationBrief:
    defaults = dict(
        segment_context={"segment_id": "seg-1", "name": "Lapsed VIPs"},
        objective="Win back lapsed customers",
        tone_brand_constraints="Friendly, concise",
        language="en",
        locale="en-AU",
    )
    defaults.update(overrides)
    return EmailGenerationBrief(**defaults)


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class CheckRequiredPlaceholdersTests(unittest.TestCase):
    def test_passes_when_both_tokens_present_in_either_body(self):
        result = check_required_placeholders(
            html_body="Hi {{first_name}}, <a href='{{unsubscribe_url}}'>unsubscribe</a>",
            text_body="Hi {{first_name}}, unsubscribe: {{unsubscribe_url}}",
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.rule, "required_placeholders")

    def test_fails_when_unsubscribe_placeholder_missing(self):
        result = check_required_placeholders(
            html_body="Hi {{first_name}}, thanks!",
            text_body="Hi {{first_name}}, thanks!",
        )
        self.assertFalse(result.passed)
        self.assertIn("unsubscribe", result.detail)

    def test_fails_when_recipient_name_placeholder_missing(self):
        result = check_required_placeholders(
            html_body="Hi there, <a href='{{unsubscribe_url}}'>unsubscribe</a>",
            text_body="Hi there, unsubscribe: {{unsubscribe_url}}",
        )
        self.assertFalse(result.passed)
        self.assertIn("recipient name", result.detail)

    def test_fails_when_placeholder_present_in_html_but_missing_from_text(self):
        """A token present in only one body must not mask its absence from
        the other -- both bodies are checked independently."""
        result = check_required_placeholders(
            html_body="Hi {{first_name}}, <a href='{{unsubscribe_url}}'>unsubscribe</a>",
            text_body="Hi {{first_name}}, thanks for being a customer!",
        )
        self.assertFalse(result.passed)
        self.assertIn("unsubscribe link placeholder in text_body", result.detail)


class CheckContentLengthTests(unittest.TestCase):
    def test_passes_within_defaults(self):
        result = check_content_length(subject="Short subject", html_body="<p>hi</p>", text_body="hi")
        self.assertTrue(result.passed)
        self.assertEqual(result.rule, "length")

    def test_fails_when_subject_exceeds_default_max(self):
        with patch("core.ai_providers.base.settings") as mock_settings:
            mock_settings.crm_email_max_subject_length = 10
            mock_settings.crm_email_max_body_length = 20000
            result = check_content_length(subject="x" * 20, html_body="ok", text_body="ok")
        self.assertFalse(result.passed)
        self.assertIn("subject", result.detail)

    def test_fails_when_body_exceeds_default_max(self):
        with patch("core.ai_providers.base.settings") as mock_settings:
            mock_settings.crm_email_max_subject_length = 150
            mock_settings.crm_email_max_body_length = 10
            result = check_content_length(subject="ok", html_body="x" * 20, text_body="ok")
        self.assertFalse(result.passed)
        self.assertIn("html_body", result.detail)


class CheckForbiddenClaimsTests(unittest.TestCase):
    def test_passes_clean_content(self):
        result = check_forbidden_claims(subject="Win back offer", html_body="Enjoy 10% off", text_body="Enjoy 10% off")
        self.assertTrue(result.passed)
        self.assertEqual(result.rule, "forbidden_claims")

    def test_fails_on_guaranteed_language(self):
        result = check_forbidden_claims(
            subject="Guaranteed results!",
            html_body="This offer is 100% guaranteed to work.",
            text_body="Guaranteed to work.",
        )
        self.assertFalse(result.passed)
        self.assertIn("guaranteed", result.detail.lower())

    def test_passes_ordinary_words_containing_a_forbidden_substring(self):
        """Word-boundary matching: "procure"/"secure"/"accurate" must not
        false-positive match the "cure" pattern."""
        result = check_forbidden_claims(
            subject="Secure your spot",
            html_body="We procure only accurate, verified data for this offer.",
            text_body="Thanks for being a secure, valued customer.",
        )
        self.assertTrue(result.passed)


class CheckHtmlSafetyTests(unittest.TestCase):
    def test_passes_clean_html(self):
        result = check_html_safety("<p>Hi {{first_name}}</p>")
        self.assertTrue(result.passed)
        self.assertEqual(result.rule, "html_safety")

    def test_fails_on_embedded_script_tag(self):
        result = check_html_safety("<p>Hi</p><script>alert('x')</script>")
        self.assertFalse(result.passed)
        self.assertIn("script", result.detail.lower())

    def test_fails_on_inline_event_handler(self):
        result = check_html_safety("<img src=x onerror=\"alert(1)\">")
        self.assertFalse(result.passed)


class RunSafetyChecksTests(unittest.TestCase):
    def test_aggregates_all_four_rules(self):
        results = run_safety_checks(
            subject="Come back!",
            html_body="Hi {{first_name}}, {{unsubscribe_url}}",
            text_body="Hi {{first_name}}, {{unsubscribe_url}}",
        )
        rules = {r.rule for r in results}
        self.assertEqual(rules, {"required_placeholders", "length", "forbidden_claims", "html_safety"})
        self.assertTrue(all(r.passed for r in results))


class GeminiProviderTests(unittest.TestCase):
    def test_generate_success(self):
        response_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "subject": "Come back!",
                                        "html_body": "Hi {{first_name}}, {{unsubscribe_url}}",
                                        "text_body": "Hi {{first_name}}, {{unsubscribe_url}}",
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
        with patch("core.ai_providers.gemini_provider.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.gemini_model = "gemini-2.5-flash"
            with patch(
                "core.ai_providers.gemini_provider.urllib.request.urlopen",
                return_value=_FakeHTTPResponse(response_payload),
            ):
                content = GeminiProvider().generate(_brief())

        self.assertEqual(content.subject, "Come back!")
        self.assertIn("{{first_name}}", content.html_body)

    def test_missing_api_key_raises(self):
        with patch("core.ai_providers.gemini_provider.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            with self.assertRaises(AIProviderError):
                GeminiProvider().generate(_brief())

    def test_http_error_raises_provider_error(self):
        with patch("core.ai_providers.gemini_provider.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-key"
            mock_settings.gemini_model = "gemini-2.5-flash"
            with patch(
                "core.ai_providers.gemini_provider.urllib.request.urlopen",
                side_effect=urllib.error.URLError("boom"),
            ):
                with self.assertRaises(AIProviderError):
                    GeminiProvider().generate(_brief())


class OpenAIProviderTests(unittest.TestCase):
    def test_generate_success(self):
        response_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "subject": "Come back!",
                                "html_body": "Hi {{first_name}}, {{unsubscribe_url}}",
                                "text_body": "Hi {{first_name}}, {{unsubscribe_url}}",
                            }
                        )
                    }
                }
            ]
        }
        with patch("core.ai_providers.openai_provider.settings") as mock_settings:
            mock_settings.openai_api_key = "test-key"
            mock_settings.openai_model = "gpt-5.6-luna"
            with patch(
                "core.ai_providers.openai_provider.urllib.request.urlopen",
                return_value=_FakeHTTPResponse(response_payload),
            ):
                content = OpenAIProvider().generate(_brief())

        self.assertEqual(content.subject, "Come back!")

    def test_missing_api_key_raises(self):
        with patch("core.ai_providers.openai_provider.settings") as mock_settings:
            mock_settings.openai_api_key = ""
            with self.assertRaises(AIProviderError):
                OpenAIProvider().generate(_brief())


if __name__ == "__main__":
    unittest.main()
