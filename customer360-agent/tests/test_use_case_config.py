"""Per-use-case model selection: a cheap model can serve tickets and narrative
while a stronger one serves SQL. A typo must fail at startup, not silently fall
back to the default model."""

import pytest

from config import USE_CASES, Settings


def test_a_use_case_without_an_override_uses_the_default_model():
    settings = Settings(llm_model="default/model", llm_use_case_models={})

    assert settings.model_for("sql") == "default/model"


def test_an_override_applies_only_to_its_own_use_case():
    settings = Settings(llm_model="default/model", llm_use_case_models={"tickets": "small/model"})

    assert settings.model_for("tickets") == "small/model"
    assert settings.model_for("sql") == "default/model"


def test_overrides_can_come_from_a_json_environment_variable(monkeypatch):
    monkeypatch.setenv("LLM_USE_CASE_MODELS", '{"sql": "strong/model"}')

    assert Settings().model_for("sql") == "strong/model"


def test_an_unknown_use_case_name_in_the_setting_fails_at_load():
    with pytest.raises(ValueError, match="sqll"):
        Settings(llm_use_case_models={"sqll": "x/y"})


def test_asking_for_an_unknown_use_case_raises():
    with pytest.raises(KeyError):
        Settings().model_for("nope")


def test_the_use_case_list_covers_every_route():
    assert set(USE_CASES) == {"sql", "narrative", "support", "tickets", "schema_mapping", "copy", "orchestrator", "persona"}
