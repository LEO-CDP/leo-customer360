"""The real PgPromptStore must accept every seeded use-case prompt (it silently
drops any key that fails its own validation), and the agents must read the same
text through it. Skipped unless AGENT_TEST_DATABASE_URL is set."""

import os
from unittest.mock import patch

import pytest

pytest.importorskip("sqlalchemy")

import use_cases.instructions as ins  # noqa: E402
from prompts import PgPromptStore  # noqa: E402
from seed_prompts import load_seeded_prompts  # noqa: E402

URL = os.environ.get("AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="AGENT_TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def store():
    from sqlalchemy import create_engine

    assert URL.rstrip("/").endswith("customer360_agents_test"), "refusing to run outside the test database"
    # The store's query uses unqualified table names, so set the schema as the service's own engine does.
    engine = create_engine(URL, future=True, connect_args={"options": "-csearch_path=customer360,public"})
    real = PgPromptStore()
    with patch.object(real, "_engine", return_value=engine):
        real.refresh()
    yield real
    engine.dispose()


@pytest.mark.parametrize("use_case", sorted(ins.KEYS))
def test_the_real_store_loads_the_seeded_prompt(store, use_case):
    key = ins.KEYS[use_case]

    template = store.get(key)  # raises PromptNotFound if the store dropped it for failing validation

    assert (template.version, template.engine, template.required_vars) == (1, "none", ())
    assert template.body == load_seeded_prompts()[key]["body"]


def test_the_agents_read_the_same_text_through_the_real_store(store, monkeypatch):
    monkeypatch.setattr(ins, "get_store", lambda: store)

    for use_case, key in ins.KEYS.items():
        assert ins.instructions(use_case) == load_seeded_prompts()[key]["body"].strip()
