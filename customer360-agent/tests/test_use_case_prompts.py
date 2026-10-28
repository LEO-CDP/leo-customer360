"""Contract between the database seed and the use-case agents: every agent has a
published, valid, static instruction, puts it first in the prompt, and stops
cleanly when it is not published."""

import pytest
from seed_prompts import SEED_SQL, load_seeded_prompts

import use_cases.instructions as ins
from ai_providers.base import AIProviderError
from prompts import PromptNotFound, PromptTemplate, declared_vars, validate
from use_cases.campaign_orchestrator import Tool, plan_next_step
from use_cases.copy_drafter import draft_copy
from use_cases.performance_narrative import narrate
from use_cases.persona_decision_loop import decide
from use_cases.schema_mapper import propose_mappings
from use_cases.support_answerer import answer_support_question
from use_cases.text_to_sql import NOT_FOUND, generate_sql
from use_cases.ticket_classifier import classify_ticket

SEEDED = load_seeded_prompts()
USE_CASES = sorted(ins.KEYS)


class _Store:
    def __init__(self, bodies):
        self.bodies = bodies

    def get(self, key):
        if key not in self.bodies:
            raise PromptNotFound(key)
        return PromptTemplate(key=key, body=self.bodies[key])


class _Capture:
    """Records prompts and replies with junk, because only the prompt matters here."""

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "not json"


# One minimal, valid call per agent.
CALLS = {
    "sql": lambda p: generate_sql("how many segments", p, {"cdp_segments": "cdp_segments: segment_id"}),
    "narrative": lambda p: narrate("q", [{"a": 1}], p),
    "support": lambda p: answer_support_question("q", p, lambda q: [{"url": "https://h.example.test/a", "text": "t"}]),
    "tickets": lambda p: classify_ticket("FIXTURE: help", p),
    "schema_mapping": lambda p: propose_mappings([{"name": "c"}], {"f": "f: d"}, p),
    "copy": lambda p: draft_copy({"objective": "x", "audience": "y"}, ["g"], p),
    "orchestrator": lambda p: plan_next_step("g", {"t": Tool("t", "d")}, p, []),
    "persona": lambda p: decide({"persona": {"risk_level": "high"}}, p),
}


def test_the_call_table_covers_every_use_case():
    assert sorted(CALLS) == USE_CASES


@pytest.mark.parametrize("use_case", USE_CASES)
def test_the_seed_publishes_a_valid_static_prompt(use_case):
    key = ins.KEYS[use_case]
    assert key in SEEDED, f"{key} is not seeded in {SEED_SQL.name}"
    body = SEEDED[key]["body"]

    validate(key, body, "none", ())  # the store's own publish rail
    assert declared_vars(body) == () and "$" not in body
    assert chr(0x2014) not in body


def test_each_prompt_key_is_seeded_once_under_its_own_agent_code():
    text = SEED_SQL.read_text(encoding="utf-8")
    codes = [SEEDED[key]["agent_code"] for key in ins.KEYS.values()]

    assert len(set(codes)) == len(codes)
    for key in ins.KEYS.values():
        assert text.count(f"'{key}'") == 1


@pytest.mark.parametrize("use_case", ["sql", "support"])
def test_prompts_that_rely_on_the_not_found_sentinel_name_it(use_case):
    assert NOT_FOUND in SEEDED[ins.KEYS[use_case]]["body"]


@pytest.mark.parametrize("use_case", USE_CASES)
def test_the_agent_puts_the_published_instruction_first(use_case, monkeypatch):
    marker = f"PUBLISHED INSTRUCTION FOR {use_case}"
    monkeypatch.setattr(ins, "get_store", lambda: _Store({ins.KEYS[use_case]: marker}))
    provider = _Capture()

    try:
        CALLS[use_case](provider)
    except AIProviderError:
        pass  # the junk reply may be rejected; the prompt has already been sent

    assert provider.prompts, "the agent never called the model"
    assert provider.prompts[0].startswith(marker)


@pytest.mark.parametrize("use_case", USE_CASES)
def test_an_unpublished_prompt_stops_the_agent_before_any_model_call(use_case, monkeypatch):
    monkeypatch.setattr(ins, "get_store", lambda: _Store({}))
    provider = _Capture()

    with pytest.raises(PromptNotFound):
        CALLS[use_case](provider)

    assert provider.prompts == []
