"""Use case 7: turn a result table into a plain-English summary without
letting the model introduce numbers that are not in the data."""

from use_cases.performance_narrative import MAX_ROWS, narrate, ungrounded_numbers

# Synthetic test rows, not real campaign results.
ROWS = [
    {"campaign": "Alpha", "sent": 1000, "open_rate": 0.25},
    {"campaign": "Beta", "sent": 2500, "open_rate": 0.4},
]


class _Provider:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_empty_rows_skip_the_model_and_say_there_is_no_data():
    provider = _Provider("should not be used")

    result = narrate("How did campaigns do?", [], provider)

    assert provider.prompts == []
    assert "no data" in result.text.lower()
    assert result.ungrounded == []


def test_prompt_carries_the_row_values():
    provider = _Provider("Beta sent 2,500 emails.")

    narrate("How did campaigns do?", ROWS, provider)

    assert "Alpha" in provider.prompts[0]
    assert "2500" in provider.prompts[0]


def test_prompt_is_capped_at_max_rows():
    many = [{"campaign": f"C{i}", "sent": i} for i in range(MAX_ROWS + 25)]
    provider = _Provider("ok")

    narrate("q", many, provider)

    assert f'"C{MAX_ROWS - 1}"' in provider.prompts[0]
    assert f'"C{MAX_ROWS}"' not in provider.prompts[0]


def test_em_dashes_are_replaced_in_the_output():
    result = narrate("q", ROWS, _Provider("Beta led — with 2,500 sent."))

    assert "—" not in result.text
    assert result.text == "Beta led, with 2,500 sent."


def test_numbers_present_in_the_data_are_grounded():
    result = narrate("q", ROWS, _Provider("Beta sent 2,500 emails with a 40% open rate."))

    assert result.ungrounded == []


def test_a_number_not_in_the_data_is_flagged():
    result = narrate("q", ROWS, _Provider("Beta sent 2,500 emails, up 300% on last month."))

    assert result.ungrounded == ["300"]


def test_ungrounded_numbers_helper_ignores_text_without_digits():
    assert ungrounded_numbers("Beta did better than Alpha.", ROWS) == []
