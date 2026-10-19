"""Use case 3: tool-calling campaign orchestration.

The model plans a campaign workflow one step at a time by replying with a JSON
action: call a registered tool, or finish. Tool results are fed back as
observations. Nothing the model says can reach beyond the registry:

- unknown tools and missing arguments become observations, not crashes;
- tools flagged `side_effect` are dry-run unless the caller passes
  `dry_run=False` AND an `approve` callback that says yes;
- the loop stops at `max_steps`.

Two ways to drive it. `run_orchestration` runs the whole loop in-process with
real tool functions. `plan_next_step` is the stateless form for an HTTP caller:
it returns the next validated action and never runs anything, so the caller
executes tools, handles approval and sends the observations back.

The rules come from the published instruction (prompt store); the tools,
background, goal and steps so far are appended here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from ai_providers.base import AIProviderError, parse_json_object
from use_cases.instructions import instructions
from use_cases.text_clean import strip_em_dashes
from use_cases.text_to_sql import select_relevant_tables

MAX_OBSERVATION_CHARS = 2000

_DATA = """Tools:
{tools}

Background:
{context}

Goal: {goal}

Steps so far:
{history}
"""


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    run: Optional[Callable[..., Any]] = None  # None when a caller only describes a tool it runs itself
    required: tuple[str, ...] = ()
    side_effect: bool = False


@dataclass(frozen=True)
class Step:
    tool: Optional[str]
    args: dict[str, Any]
    observation: str
    executed: bool  # True when the tool function was actually invoked


@dataclass(frozen=True)
class OrchestrationResult:
    status: str  # "finished" | "step_limit"
    steps: list[Step] = field(default_factory=list)
    summary: Optional[str] = None


@dataclass(frozen=True)
class NextStep:
    """The model's next action, checked against the tool registry but not run.

    kind "tool": call `tool` with `args`; `requires_approval` is True for tools
    that change data. kind "finish": `summary` is the result. kind "error": the
    reply was unusable (`reason` says why) and the caller may ask again."""

    kind: str
    tool: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    summary: Optional[str] = None
    requires_approval: bool = False
    reason: Optional[str] = None


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_OBSERVATION_CHARS else f"{text[:MAX_OBSERVATION_CHARS]} [truncated]"


def _describe(tool: Tool) -> str:
    args = f" (args: {', '.join(tool.required)})" if tool.required else ""
    flag = " [changes data]" if tool.side_effect else ""
    return f"- {tool.name}: {tool.description}{args}{flag}"


def _unknown_tool(name: Any, tools: dict[str, Tool]) -> str:
    return f"unknown tool {name!r}; available: {sorted(tools)}"


def _missing_args(tool: Tool, args: dict[str, Any]) -> str:
    return ", ".join(a for a in tool.required if a not in args)


def _build_prompt(
    goal: str,
    tools: dict[str, Tool],
    steps: list[Step],
    context: Optional[list[str]],
    max_context: int,
) -> str:
    snippets = {f"context_{i}": c for i, c in enumerate(context or [])}
    chosen = select_relevant_tables(goal, snippets, k=max_context, require_match=True) if snippets else []
    background = "\n".join(f"- {snippets[c]}" for c in chosen) or "(none)"
    history = "\n".join(
        f"{i}. {s.tool or 'no tool'} {json.dumps(s.args, default=str)} -> {_truncate(s.observation)}"
        for i, s in enumerate(steps, 1)
    ) or "(none yet)"
    return f"{instructions('orchestrator')}\n\n" + _DATA.format(
        tools="\n".join(_describe(t) for t in tools.values()), context=background, goal=goal, history=history
    )


def _execute(
    action: dict[str, Any],
    tools: dict[str, Tool],
    dry_run: bool,
    approve: Optional[Callable[[Tool, dict[str, Any]], bool]],
) -> Step:
    name = action.get("tool")
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    tool = tools.get(name) if isinstance(name, str) else None
    if tool is None:
        return Step(None, args, _unknown_tool(name, tools), False)

    missing = _missing_args(tool, args)
    if missing:
        return Step(name, args, f"missing required argument(s): {missing}", False)

    if tool.side_effect:
        if dry_run:
            return Step(name, args, f"dry run: {name} was not executed", False)
        if approve is None or not approve(tool, args):
            return Step(name, args, f"not approved: {name} was not executed", False)

    try:
        result = tool.run(**args)
    except Exception as exc:  # a tool failure is information for the next step
        return Step(name, args, _truncate(f"error: {exc}"), True)
    return Step(name, args, _truncate(json.dumps(result, default=str)), True)


def plan_next_step(
    goal: str,
    tools: dict[str, Tool],
    provider: Provider,
    steps: list[Step],
    context: Optional[list[str]] = None,
    max_context: int = 5,
) -> NextStep:
    prompt = _build_prompt(goal, tools, steps, context, max_context)
    raw = provider.complete(prompt)  # a provider outage propagates; the caller decides what to do

    try:
        action = parse_json_object(raw)
    except AIProviderError as exc:
        return NextStep("error", reason=str(exc))

    kind = action.get("action")
    if kind == "finish":
        return NextStep("finish", summary=strip_em_dashes(str(action.get("summary", ""))))
    if kind != "tool":
        return NextStep("error", reason=f"unrecognised action {kind!r}")

    name = action.get("tool")
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    tool = tools.get(name) if isinstance(name, str) else None
    if tool is None:
        return NextStep("error", reason=_unknown_tool(name, tools))
    missing = _missing_args(tool, args)
    if missing:
        return NextStep("error", reason=f"missing required argument(s): {missing}")
    return NextStep("tool", tool=name, args=args, requires_approval=tool.side_effect)


def run_orchestration(
    goal: str,
    tools: dict[str, Tool],
    provider: Provider,
    context: Optional[list[str]] = None,
    max_context: int = 5,
    max_steps: int = 8,
    dry_run: bool = True,
    approve: Optional[Callable[[Tool, dict[str, Any]], bool]] = None,
) -> OrchestrationResult:
    steps: list[Step] = []
    for _ in range(max_steps):
        prompt = _build_prompt(goal, tools, steps, context, max_context)

        try:
            action = parse_json_object(provider.complete(prompt))
        except AIProviderError as exc:
            steps.append(Step(None, {}, str(exc), False))
            continue

        if action.get("action") == "finish":
            summary = strip_em_dashes(str(action.get("summary", "")))
            return OrchestrationResult("finished", steps, summary)
        if action.get("action") == "tool":
            steps.append(_execute(action, tools, dry_run, approve))
        else:
            steps.append(Step(None, {}, f"unrecognised action {action.get('action')!r}", False))

    return OrchestrationResult("step_limit", steps, None)
