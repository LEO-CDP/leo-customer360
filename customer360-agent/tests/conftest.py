"""Shared fixtures.

The use-case agents take their instruction text from the prompt store, whose
bodies are seeded by customer360-database. These tests run them against exactly
the seeded text, through an in-memory store, so the SQL seed and the code cannot
drift apart. Only `use_cases.instructions` is pointed at it; nothing else changes."""

import pytest
from seed_prompts import SEED_SQL, load_seeded_prompts

from prompts import PromptNotFound, PromptTemplate


class SeededStore:
    """A prompt store holding the bodies found in the database seed."""

    def get(self, key: str) -> PromptTemplate:
        seeded = load_seeded_prompts().get(key)
        if seeded is None:
            raise PromptNotFound(f"{key} (not in the seed {SEED_SQL})")
        return PromptTemplate(key=key, body=seeded["body"])


@pytest.fixture(autouse=True)
def _seeded_use_case_prompts(monkeypatch):
    import use_cases.instructions as ins

    store = SeededStore()
    monkeypatch.setattr(ins, "get_store", lambda: store)
