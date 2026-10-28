"""Use case 3: tool-calling campaign orchestration. Side-effecting tools are
dry-run unless explicitly approved, and bad model output or tool failures
become observations the agent can recover from."""

import json

import pytest

from ai_providers.base import AIProviderError
from use_cases.campaign_orchestrator import (
    MAX_OBSERVATION_CHARS,
    NextStep,
    Step,
    Tool,
    plan_next_step,
    run_orchestration,
)


class _Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)


def _call(tool, **args):
    return {"action": "tool", "tool": tool, "args": args}


def _finish(summary="Done."):
    return {"action": "finish", "summary": summary}


def _tools(log=None):
    log = log if log is not None else []

    def segment_size(segment_id):
        log.append(("segment_size", segment_id))
        return {"segment_id": segment_id, "members": 120}

    def schedule_campaign(segment_id, name):
        log.append(("schedule_campaign", segment_id, name))
        return {"scheduled": name}

    return {
        "segment_size": Tool("segment_size", "Count members in a segment", segment_size, required=("segment_id",)),
        "schedule_campaign": Tool(
            "schedule_campaign", "Schedule a campaign", schedule_campaign,
            required=("segment_id", "name"), side_effect=True,
        ),
    }, log


def test_runs_a_tool_then_finishes():
    tools, log = _tools()
    provider = _Scripted(_call("segment_size", segment_id="s1"), _finish("Segment s1 has 120 members."))

    result = run_orchestration("size the segment", tools, provider)

    assert result.status == "finished"
    assert result.summary == "Segment s1 has 120 members."
    assert log == [("segment_size", "s1")]
    assert result.steps[0].tool == "segment_size"
    assert result.steps[0].executed is True


def test_tool_observation_is_fed_back_into_the_next_prompt():
    tools, _ = _tools()
    provider = _Scripted(_call("segment_size", segment_id="s1"), _finish())

    run_orchestration("size the segment", tools, provider)

    assert '"members": 120' in provider.prompts[1]


def test_unknown_tool_becomes_an_observation_and_the_loop_continues():
    tools, log = _tools()
    provider = _Scripted(_call("delete_everything"), _finish())

    result = run_orchestration("g", tools, provider)

    assert result.status == "finished"
    assert "unknown tool" in result.steps[0].observation
    assert log == []


def test_missing_required_argument_is_reported_and_the_tool_is_not_called():
    tools, log = _tools()
    provider = _Scripted(_call("segment_size"), _finish())

    result = run_orchestration("g", tools, provider)

    assert "segment_id" in result.steps[0].observation
    assert result.steps[0].executed is False
    assert log == []


def test_side_effect_tool_is_dry_run_by_default():
    tools, log = _tools()
    provider = _Scripted(_call("schedule_campaign", segment_id="s1", name="Winback"), _finish())

    result = run_orchestration("g", tools, provider)

    assert log == []
    assert result.steps[0].executed is False
    assert "dry run" in result.steps[0].observation


def test_side_effect_tool_runs_only_when_live_and_approved():
    tools, log = _tools()
    provider = _Scripted(_call("schedule_campaign", segment_id="s1", name="Winback"), _finish())

    result = run_orchestration("g", tools, provider, dry_run=False, approve=lambda tool, args: True)

    assert log == [("schedule_campaign", "s1", "Winback")]
    assert result.steps[0].executed is True


def test_side_effect_tool_is_refused_when_approval_is_denied():
    tools, log = _tools()
    provider = _Scripted(_call("schedule_campaign", segment_id="s1", name="Winback"), _finish())

    result = run_orchestration("g", tools, provider, dry_run=False, approve=lambda tool, args: False)

    assert log == []
    assert "not approved" in result.steps[0].observation


def test_live_mode_without_an_approver_refuses_side_effects():
    tools, log = _tools()
    provider = _Scripted(_call("schedule_campaign", segment_id="s1", name="Winback"), _finish())

    result = run_orchestration("g", tools, provider, dry_run=False)

    assert log == []
    assert "not approved" in result.steps[0].observation


def test_step_limit_stops_the_loop_without_raising():
    tools, _ = _tools()
    provider = _Scripted(*[_call("segment_size", segment_id="s1")] * 10)

    result = run_orchestration("g", tools, provider, max_steps=3)

    assert result.status == "step_limit"
    assert len(result.steps) == 3


def test_malformed_model_reply_is_an_error_step_not_a_crash():
    tools, _ = _tools()
    provider = _Scripted("this is not json", _finish())

    result = run_orchestration("g", tools, provider)

    assert result.status == "finished"
    assert result.steps[0].tool is None
    assert "valid JSON" in result.steps[0].observation


def test_a_crashing_tool_is_reported_and_the_loop_continues():
    def boom(segment_id):
        raise RuntimeError("database offline")

    tools = {"segment_size": Tool("segment_size", "d", boom, required=("segment_id",))}
    provider = _Scripted(_call("segment_size", segment_id="s1"), _finish())

    result = run_orchestration("g", tools, provider)

    assert result.status == "finished"
    assert "database offline" in result.steps[0].observation


def test_long_observations_are_truncated():
    tools = {"big": Tool("big", "d", lambda: "y" * (MAX_OBSERVATION_CHARS + 500))}
    provider = _Scripted(_call("big"), _finish())

    result = run_orchestration("g", tools, provider)

    assert len(result.steps[0].observation) <= MAX_OBSERVATION_CHARS + 20


def test_prompt_lists_tools_and_only_the_relevant_context():
    tools, _ = _tools()
    provider = _Scripted(_finish())
    context = ["Winback campaigns target customers inactive for 90 days.", "Office parking rules apply on level two."]

    run_orchestration("plan a winback campaign", tools, provider, context=context, max_context=1)

    assert "segment_size: Count members in a segment" in provider.prompts[0]
    assert "Winback campaigns" in provider.prompts[0]
    assert "parking" not in provider.prompts[0]


def test_unrelated_context_is_left_out_instead_of_padding_the_prompt():
    tools, _ = _tools()
    provider = _Scripted(_finish())

    run_orchestration(
        "plan a winback campaign", tools, provider,
        context=["Office parking rules apply on level two."],
    )

    assert "parking" not in provider.prompts[0]
    assert "Background:\n(none)" in provider.prompts[0]


def test_summary_has_em_dashes_replaced():
    tools, _ = _tools()

    result = run_orchestration("g", tools, _Scripted(_finish("Done — all good.")))

    assert result.summary == "Done, all good."


# ---- plan_next_step: the next action only, the caller runs the tools (stateless endpoint) ----


def _specs():
    """Tool descriptions as a caller would send them: no function to run."""
    return {
        "segment_size": Tool("segment_size", "Count members in a segment", run=None, required=("segment_id",)),
        "schedule_campaign": Tool(
            "schedule_campaign", "Schedule a campaign", run=None, required=("segment_id", "name"), side_effect=True
        ),
    }


def test_next_step_proposes_a_read_tool_without_asking_for_approval():
    provider = _Scripted(_call("segment_size", segment_id="s1"))

    step = plan_next_step("size the segment", _specs(), provider, steps=[])

    assert step == NextStep(kind="tool", tool="segment_size", args={"segment_id": "s1"}, requires_approval=False)


def test_next_step_flags_a_side_effect_tool_as_needing_approval():
    provider = _Scripted(_call("schedule_campaign", segment_id="s1", name="Winback"))

    step = plan_next_step("g", _specs(), provider, steps=[])

    assert step.kind == "tool" and step.requires_approval is True


def test_next_step_finish_carries_a_cleaned_summary():
    step = plan_next_step("g", _specs(), _Scripted(_finish("Done — all good.")), steps=[])

    assert (step.kind, step.summary) == ("finish", "Done, all good.")


def test_next_step_rejects_an_unknown_tool():
    step = plan_next_step("g", _specs(), _Scripted(_call("delete_everything")), steps=[])

    assert step.kind == "error" and "unknown tool" in step.reason


def test_next_step_rejects_a_missing_required_argument():
    step = plan_next_step("g", _specs(), _Scripted(_call("segment_size")), steps=[])

    assert step.kind == "error" and "segment_id" in step.reason


def test_next_step_reports_unparseable_model_output_as_an_error_step():
    step = plan_next_step("g", _specs(), _Scripted("not json"), steps=[])

    assert step.kind == "error" and "valid JSON" in step.reason


def test_next_step_reports_an_unrecognised_action():
    step = plan_next_step("g", _specs(), _Scripted({"action": "dance"}), steps=[])

    assert step.kind == "error" and "unrecognised action" in step.reason


def test_next_step_shows_earlier_observations_truncated():
    provider = _Scripted(_finish())
    steps = [Step("segment_size", {"segment_id": "s1"}, "y" * (MAX_OBSERVATION_CHARS + 500), True)]

    plan_next_step("g", _specs(), provider, steps=steps)

    assert "segment_size" in provider.prompts[0]
    assert "y" * MAX_OBSERVATION_CHARS in provider.prompts[0]
    assert "y" * (MAX_OBSERVATION_CHARS + 1) not in provider.prompts[0]


def test_next_step_leaves_unrelated_context_out():
    provider = _Scripted(_finish())

    plan_next_step("plan a winback campaign", _specs(), provider, steps=[], context=["Office parking rules apply."])

    assert "parking" not in provider.prompts[0]
    assert "Background:\n(none)" in provider.prompts[0]


def test_next_step_lets_a_provider_outage_propagate():
    class _Down:
        def complete(self, prompt):
            raise AIProviderError("upstream timeout")

    with pytest.raises(AIProviderError, match="upstream timeout"):
        plan_next_step("g", _specs(), _Down(), steps=[])
