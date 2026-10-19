"""Use case 6: ticket classification. A label outside the allowed set, or an
unusable model answer, is flagged for human review rather than guessed."""

import json

from ai_providers.base import AIProviderError
from use_cases.ticket_classifier import MAX_CHARS, classify_batch, classify_ticket

# FIXTURE tickets: synthetic, written for these tests, not real customer data.
BILLING = "FIXTURE: I was charged twice this month and need a refund today."
OUTAGE = "FIXTURE: The dashboard has been down for our whole team since this morning."


class _Provider:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


def _label(urgency="high", sentiment="negative", team="billing"):
    return {"urgency": urgency, "sentiment": sentiment, "team": team}


def test_valid_answer_becomes_a_classification():
    result = classify_ticket(BILLING, _Provider(_label()))

    assert (result.urgency, result.sentiment, result.team) == ("high", "negative", "billing")
    assert result.needs_review is False
    assert result.reason is None


def test_fenced_json_is_accepted():
    fenced = "```json\n" + json.dumps(_label(urgency="low", sentiment="neutral", team="account")) + "\n```"

    result = classify_ticket("FIXTURE: How do I change my email?", _Provider(fenced))

    assert result.team == "account"
    assert result.needs_review is False


def test_unknown_urgency_is_flagged_not_guessed():
    result = classify_ticket(BILLING, _Provider(_label(urgency="apocalyptic")))

    assert result.needs_review is True
    assert result.urgency is None
    assert "urgency" in result.reason


def test_unknown_team_is_flagged_not_guessed():
    result = classify_ticket(BILLING, _Provider(_label(team="legal")))

    assert result.needs_review is True
    assert result.team is None
    assert "team" in result.reason


def test_malformed_json_is_flagged_not_raised():
    result = classify_ticket(BILLING, _Provider("this is not json"))

    assert result.needs_review is True
    assert "valid JSON" in result.reason


def test_provider_failure_is_flagged_not_raised():
    result = classify_ticket(BILLING, _Provider(AIProviderError("timeout")))

    assert result.needs_review is True
    assert "timeout" in result.reason


def test_blank_ticket_skips_the_model():
    provider = _Provider()

    result = classify_ticket("   ", provider)

    assert provider.prompts == []
    assert result.needs_review is True
    assert "empty" in result.reason


def test_long_ticket_text_is_truncated_in_the_prompt():
    provider = _Provider(_label())

    classify_ticket("x" * (MAX_CHARS + 500), provider)

    assert "x" * MAX_CHARS in provider.prompts[0]
    assert "x" * (MAX_CHARS + 1) not in provider.prompts[0]


def test_custom_team_list_is_enforced():
    result = classify_ticket(BILLING, _Provider(_label(team="billing")), teams=("sales", "support"))

    assert result.needs_review is True
    assert result.team is None


def test_batch_keeps_order_ids_and_isolates_failures():
    provider = _Provider(_label(), AIProviderError("boom"), _label(urgency="critical", team="technical"))
    tickets = [
        {"id": "FIXTURE-1", "text": BILLING},
        {"id": "FIXTURE-2", "text": "FIXTURE: second"},
        {"id": "FIXTURE-3", "text": OUTAGE},
    ]

    results = classify_batch(tickets, provider)

    assert [r.ticket_id for r in results] == ["FIXTURE-1", "FIXTURE-2", "FIXTURE-3"]
    assert [r.needs_review for r in results] == [False, True, False]
    assert results[2].urgency == "critical"
