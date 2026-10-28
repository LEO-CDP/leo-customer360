"""Use case 8: map a client's raw columns onto the CDP schema. Anything the
model is unsure about, or gets wrong, goes to a person instead of being
silently accepted."""

import json

import pytest

from ai_providers.base import AIProviderError
from use_cases.schema_mapper import propose_mappings

# Synthetic target schema and client columns, test-only.
TARGET = {
    "primary_email": "primary_email: the customer's main email address",
    "primary_phone": "primary_phone: the customer's main mobile number",
    "date_of_birth": "date_of_birth: the customer's birth date",
    "first_name": "first_name: given name",
    "city": "city: city of residence",
}
SOURCE = [
    {"name": "cust_email", "samples": ["a@example.test"]},
    {"name": "mobile_no", "samples": ["0400000001"]},
    {"name": "dob", "samples": ["1990-01-01"]},
]


class _Provider:
    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def _reply(*rows):
    return {"mappings": [{"source": s, "target": t, "confidence": c, "reason": "r"} for s, t, c in rows]}


def _by_source(results):
    return {m.source: m for m in results}


def test_confident_valid_mapping_is_accepted():
    provider = _Provider(_reply(("cust_email", "primary_email", 0.97)))

    result = _by_source(propose_mappings(SOURCE[:1], TARGET, provider))

    assert result["cust_email"].status == "mapped"
    assert result["cust_email"].target == "primary_email"


def test_low_confidence_keeps_the_suggestion_but_needs_review():
    provider = _Provider(_reply(("mobile_no", "primary_phone", 0.55)))

    mapping = propose_mappings(SOURCE[1:2], TARGET, provider)[0]

    assert mapping.status == "needs_review"
    assert mapping.target == "primary_phone"
    assert "confidence" in mapping.reason


def test_target_outside_the_schema_is_not_accepted():
    provider = _Provider(_reply(("dob", "birthday_unix", 0.99)))

    mapping = propose_mappings(SOURCE[2:3], TARGET, provider)[0]

    assert mapping.status == "needs_review"
    assert mapping.target is None
    assert mapping.suggested == "birthday_unix"


def test_two_sources_claiming_one_target_both_need_review():
    provider = _Provider(_reply(("cust_email", "primary_email", 0.95), ("mobile_no", "primary_email", 0.9)))

    results = propose_mappings(SOURCE[:2], TARGET, provider)

    assert [m.status for m in results] == ["needs_review", "needs_review"]
    assert all("primary_email" in m.reason for m in results)


def test_source_column_the_model_skipped_is_unmapped():
    provider = _Provider(_reply(("cust_email", "primary_email", 0.95)))

    result = _by_source(propose_mappings(SOURCE[:2], TARGET, provider))

    assert result["mobile_no"].status == "unmapped"
    assert result["mobile_no"].target is None


def test_null_target_means_unmapped():
    provider = _Provider(_reply(("dob", None, 0.9)))

    assert propose_mappings(SOURCE[2:3], TARGET, provider)[0].status == "unmapped"


def test_columns_the_model_invented_are_ignored():
    provider = _Provider(_reply(("cust_email", "primary_email", 0.95), ("ghost_col", "city", 0.99)))

    results = propose_mappings(SOURCE[:1], TARGET, provider)

    assert [m.source for m in results] == ["cust_email"]


def test_non_numeric_confidence_needs_review():
    provider = _Provider({"mappings": [{"source": "dob", "target": "date_of_birth", "confidence": "high"}]})

    assert propose_mappings(SOURCE[2:3], TARGET, provider)[0].status == "needs_review"


def test_sample_values_stay_out_of_the_prompt_by_default():
    provider = _Provider(_reply(("cust_email", "primary_email", 0.95)))

    propose_mappings(SOURCE[:1], TARGET, provider)

    assert "a@example.test" not in provider.prompts[0]


def test_sample_values_are_sent_only_when_asked():
    provider = _Provider(_reply(("cust_email", "primary_email", 0.95)))

    propose_mappings(SOURCE[:1], TARGET, provider, include_samples=True)

    assert "a@example.test" in provider.prompts[0]


def test_prompt_lists_only_the_most_relevant_target_fields():
    provider = _Provider(_reply(("cust_email", "primary_email", 0.95)))

    propose_mappings(SOURCE[:1], TARGET, provider, max_targets=1)

    assert "primary_email" in provider.prompts[0]
    assert "date_of_birth" not in provider.prompts[0]


def test_unparseable_model_output_raises():
    with pytest.raises(AIProviderError):
        propose_mappings(SOURCE[:1], TARGET, _Provider("not json"))
