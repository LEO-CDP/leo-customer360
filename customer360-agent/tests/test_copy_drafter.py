"""Use case 4: brand-grounded copy drafting. Placeholders are enforced, unsafe
HTML blocks, and claim, length and buzzword problems are flagged for the
reviewer without being silently rewritten."""

import json

import pytest

from ai_providers.base import AIProviderError
from use_cases.copy_drafter import draft_copy

# Synthetic brand snippets and brief, test-only.
GUIDELINES = [
    "Voice: plain, warm and direct. Short sentences.",
    "Never promise results. Describe what the product does.",
    "Always address the reader by first name.",
    "Pricing is quoted in AUD.",
]
BRIEF = {"objective": "win back lapsed customers", "audience": "customers inactive for 90 days"}


def _fields(**overrides):
    base = {
        "subject": "We saved your spot",
        "preview_text": "A quick note before your plan lapses",
        "email_body": "Hi {{first_name}},\n\nYour plan is still here when you are ready.\n\nUnsubscribe: {{unsubscribe_url}}",
        "social_caption": "Your plan is waiting. Pick up where you left off.",
    }
    base.update(overrides)
    return base


class _Provider:
    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def _checks(draft):
    return {c.rule: c for c in draft.checks}


def test_clean_draft_returns_all_four_fields_and_passes_every_check():
    draft = draft_copy(BRIEF, GUIDELINES, _Provider(_fields()))

    assert draft.subject == "We saved your spot"
    assert draft.preview_text.startswith("A quick note")
    assert "Your plan is still here" in draft.email_body
    assert draft.social_caption.startswith("Your plan is waiting")
    assert all(c.passed for c in draft.checks)
    assert draft.blocked is False


def test_prompt_uses_only_the_most_relevant_guidelines():
    provider = _Provider(_fields())

    draft_copy({"objective": "quote pricing in AUD", "audience": "new leads"}, GUIDELINES, provider, max_guidelines=1)

    assert "AUD" in provider.prompts[0]
    assert "Short sentences" not in provider.prompts[0]


def test_prompt_carries_the_brief():
    provider = _Provider(_fields())

    draft_copy(BRIEF, GUIDELINES, provider)

    assert "win back lapsed customers" in provider.prompts[0]


def test_em_dashes_are_replaced_in_every_field():
    reply = _fields(
        subject="Still here — ready when you are",
        preview_text="Quick note — no rush",
        social_caption="Pick up — where you left off",
    )

    draft = draft_copy(BRIEF, GUIDELINES, _Provider(reply))

    for text in (draft.subject, draft.preview_text, draft.email_body, draft.social_caption):
        assert "—" not in text
    assert draft.subject == "Still here, ready when you are"


def test_missing_unsubscribe_placeholder_is_added_and_recorded():
    reply = _fields(email_body="Hi {{first_name}},\n\nYour plan is still here.")

    draft = draft_copy(BRIEF, GUIDELINES, _Provider(reply))

    assert "{{unsubscribe_url}}" in draft.email_body
    assert _checks(draft)["placeholders"].passed is True
    assert "added" in _checks(draft)["placeholders"].detail


def test_missing_name_placeholder_is_added():
    reply = _fields(email_body="Your plan is still here.\n\nUnsubscribe: {{unsubscribe_url}}")

    draft = draft_copy(BRIEF, GUIDELINES, _Provider(reply))

    assert draft.email_body.startswith("Hi {{first_name}},")


def test_present_placeholders_are_left_alone():
    draft = draft_copy(BRIEF, GUIDELINES, _Provider(_fields()))

    assert draft.email_body.count("{{unsubscribe_url}}") == 1
    assert draft.email_body.count("{{first_name}}") == 1
    assert "added" not in (_checks(draft)["placeholders"].detail or "")


def test_guaranteed_outcome_claim_is_flagged_but_not_blocking():
    reply = _fields(subject="Guaranteed results in 7 days")

    draft = draft_copy(BRIEF, GUIDELINES, _Provider(reply))

    assert _checks(draft)["forbidden_claims"].passed is False
    assert "Guaranteed" in _checks(draft)["forbidden_claims"].detail
    assert draft.blocked is False


def test_script_in_the_body_is_flagged_and_blocks():
    reply = _fields(email_body="Hi {{first_name}}<script>alert(1)</script>\n{{unsubscribe_url}}")

    draft = draft_copy(BRIEF, GUIDELINES, _Provider(reply))

    assert _checks(draft)["html_safety"].passed is False
    assert draft.blocked is True


def test_inline_event_handler_is_flagged_as_unsafe_html():
    reply = _fields(email_body='Hi {{first_name}} <a href="#" onclick="x()">go</a>\n{{unsubscribe_url}}')

    assert _checks(draft_copy(BRIEF, GUIDELINES, _Provider(reply)))["html_safety"].passed is False


def test_overlong_subject_is_flagged():
    reply = _fields(subject="x" * 130)

    draft = draft_copy(BRIEF, GUIDELINES, _Provider(reply), max_subject=120)

    assert _checks(draft)["length"].passed is False
    assert "subject" in _checks(draft)["length"].detail


def test_marketing_buzzwords_are_flagged_for_the_reviewer():
    reply = _fields(preview_text="A seamless, revolutionary way back")

    draft = draft_copy(BRIEF, GUIDELINES, _Provider(reply))

    assert _checks(draft)["buzzwords"].passed is False
    assert "seamless" in _checks(draft)["buzzwords"].detail.lower()
    assert draft.blocked is False


def test_missing_field_in_the_model_output_raises():
    incomplete = _fields()
    del incomplete["social_caption"]

    with pytest.raises(AIProviderError, match="social_caption"):
        draft_copy(BRIEF, GUIDELINES, _Provider(incomplete))
