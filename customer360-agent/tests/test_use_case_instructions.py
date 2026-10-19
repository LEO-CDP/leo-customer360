"""The use-case agents read their instruction text from the prompt store, like
the planners do, so operators can version it. Only static instructions are
stored; each agent appends its data in code."""

import pytest

import use_cases.instructions as ins
from config import USE_CASES
from prompts import PromptNotFound, PromptTemplate


class _Store:
    def __init__(self, bodies):
        self.bodies = bodies

    def get(self, key):
        if key not in self.bodies:
            raise PromptNotFound(key)
        return PromptTemplate(key=key, body=self.bodies[key])


def test_every_use_case_has_exactly_one_prompt_key():
    assert set(ins.KEYS) == set(USE_CASES)
    assert len(set(ins.KEYS.values())) == len(ins.KEYS)


def test_keys_follow_the_dotted_instructions_convention():
    for key in ins.KEYS.values():
        assert key.endswith(".instructions") and key == key.lower() and " " not in key


def test_instructions_returns_the_published_body(monkeypatch):
    monkeypatch.setattr(ins, "get_store", lambda: _Store({ins.KEYS["sql"]: "Rules for SQL."}))

    assert ins.instructions("sql") == "Rules for SQL."


def test_literal_braces_in_a_published_body_survive(monkeypatch):
    body = 'Reply with {"mappings": [{"source": "x"}]} only.'
    monkeypatch.setattr(ins, "get_store", lambda: _Store({ins.KEYS["schema_mapping"]: body}))

    assert ins.instructions("schema_mapping") == body


def test_blank_lines_around_a_stored_body_are_trimmed(monkeypatch):
    # Seeded bodies are written between $prompt$ markers, so they start and end with a newline.
    monkeypatch.setattr(ins, "get_store", lambda: _Store({ins.KEYS["sql"]: "\nLine one.\n\nLine two.\n"}))

    assert ins.instructions("sql") == "Line one.\n\nLine two."


def test_an_unpublished_prompt_raises_prompt_not_found(monkeypatch):
    monkeypatch.setattr(ins, "get_store", lambda: _Store({}))

    with pytest.raises(PromptNotFound):
        ins.instructions("sql")


def test_an_unknown_use_case_raises_key_error():
    with pytest.raises(KeyError):
        ins.instructions("nope")
