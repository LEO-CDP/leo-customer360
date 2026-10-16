"""Test-only demo flow (the studio's "Cart abandonment" recipe) and UAT suite."""

from __future__ import annotations

from typing import Any

from leo_customer360_dao.models.identity import CdpMasterProfile
from leo_customer360_dao.workflow.engine import FlowEngine
from leo_customer360_dao.workflow.models import FlowDefinition
from leo_customer360_dao.workflow.models import JourneyResult
from leo_customer360_dao.workflow.services import ScriptedEventSource, Services

from .samples import SampleProfileSource


from pydantic import BaseModel, ConfigDict, Field


class UatCase(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    profile: CdpMasterProfile
    events: dict[str, float] = Field(default_factory=dict)
    expect_end: str | None = None
    expect_path: list[str] | None = None  # exact node ids visited, in order
    expect_sent: list[str] | None = None  # channels actually sent, in order
    expect_tags: list[str] = Field(default_factory=list)  # must all be present at the end


class CaseOutcome(BaseModel):
    case: str
    passed: bool
    failures: list[str]
    result: JourneyResult


class UatRunner:
    def __init__(self, engine: FlowEngine) -> None:
        self.engine = engine

    def check(self, case: UatCase) -> CaseOutcome:
        current_services = self.engine.services
        services = Services(
            gateways=current_services.gateways,
            events=ScriptedEventSource({case.profile.master_profile_id: case.events}),
            profile_writer=current_services.profile_writer,
        )
        result = FlowEngine(
            self.engine.definition,
            services=services,
            max_steps=self.engine.max_steps,
        ).run(case.profile)
        failures: list[str] = []
        if result.error:
            failures.append(f"error: {result.error}")
        if case.expect_end is not None and result.end != case.expect_end:
            failures.append(f"end: expected {case.expect_end}, got {result.end}")
        if case.expect_path is not None and result.path != case.expect_path:
            failures.append(f"path: expected {case.expect_path}, got {result.path}")
        if case.expect_sent is not None and result.sent != case.expect_sent:
            failures.append(f"sent: expected {case.expect_sent}, got {result.sent}")
        missing = [tag for tag in case.expect_tags if tag not in result.tags]
        if missing:
            failures.append(f"tags missing: {missing}")
        return CaseOutcome(case=case.name, passed=not failures, failures=failures, result=result)

    def run(self, cases: list[UatCase]) -> list[CaseOutcome]:
        return [self.check(case) for case in cases]


def print_journeys(results: list[JourneyResult]) -> None:
    print(f"\n{'profile':<12}{'end':<7}{'days':>6}  path")
    for result in results:
        print(
            f"{result.name:<12}{result.end:<7}{result.elapsed_hours / 24:>6.1f}  "
            f"{' > '.join(result.path)}   sent={result.sent} tags={result.tags}"
        )
    ends = {end: sum(result.end == end for result in results) for end in ("goal", "exit", "ended", "error")}
    print(f"\n{len(results)} profiles: " + ", ".join(f"{key}={value}" for key, value in ends.items()))


def print_uat(outcomes: list[CaseOutcome]) -> int:
    for outcome in outcomes:
        print(f"[{'PASS' if outcome.passed else 'FAIL'}] {outcome.case}")
        for failure in outcome.failures:
            print(f"       - {failure}")
    failed = sum(not outcome.passed for outcome in outcomes)
    print(f"\nUAT: {len(outcomes) - failed}/{len(outcomes)} passed")
    return 1 if failed else 0



def _node(
    node_id: str,
    node_type: str,
    config: dict[str, Any],
    children: list[dict[str, Any]] | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"id": node_id, "type": node_type,
                            "config": config, "children": list(children or [])}
    if branch:
        node["branch"] = branch
    return node


ENGAGEMENT_SCORE_FOLLOW_UP = FlowDefinition.model_validate(
    {
        "schema_version": 2,
        "root": _node(
            "n1",
            "trigger",
            {"event": "engagement_follow_up"},
            [
                _node(
                    "n2",
                    "wait",
                    {"amount": 1, "unit": "hours"},
                    [
                        _node(
                            "n3",
                            "condition",
                            {"field": "engagement_score", "op": "ge", "value": "60"},
                            [
                                _node(
                                    "n4",
                                    "email",
                                    {
                                        "subject": "{{first_name}}, your personalized update",
                                        "body": "Your personalized recommendations are ready.",
                                    },
                                    [
                                        _node(
                                            "n5",
                                            "wait_event",
                                            {"event": "purchase",
                                                "amount": 2, "unit": "days"},
                                            [
                                                _node("n6", "exit", {
                                                      "kind": "goal"}, branch="happened"),
                                                _node(
                                                    "n7",
                                                    "push",
                                                    {
                                                        "title": "Flash sale: 15% off today",
                                                        "body": "{{first_name}}, your saved items are on sale.",
                                                    },
                                                    [_node("n8", "exit", {
                                                           "kind": "exit"})],
                                                    branch="timeout",
                                                ),
                                            ],
                                        )
                                    ],
                                    branch="yes",
                                ),
                                _node(
                                    "n9",
                                    "push",
                                    {
                                        "title": "Still thinking it over?",
                                        "body": "{{first_name}}, your recommendations are one tap away.",
                                    },
                                    [
                                        _node(
                                            "n10",
                                            "wait_event",
                                            {"event": "purchase",
                                                "amount": 2, "unit": "days"},
                                            [
                                                _node("n11", "exit", {
                                                      "kind": "goal"}, branch="happened"),
                                                _node("n12", "exit", {
                                                      "kind": "exit"}, branch="timeout"),
                                            ],
                                        )
                                    ],
                                    branch="no",
                                ),
                            ],
                        )
                    ],
                )
            ],
        ),
    }
)


def engagement_score_follow_up_cases() -> list[UatCase]:
    ines, dev, ben, aiko = SampleProfileSource.scenarios()
    return [
        UatCase(
            name="High engagement customer converts after the email",
            profile=ines,
            events={"purchase": 10},
            expect_end="goal",
            expect_sent=["email"],
            expect_path=["n1", "n2", "n3", "n4", "n5", "n6"],
        ),
        UatCase(
            name="Lower engagement customer converts after the push",
            profile=dev,
            events={"purchase": 30},
            expect_end="goal",
            expect_sent=["push"],
            expect_path=["n1", "n2", "n3", "n9", "n10", "n11"],
        ),
        UatCase(
            name="High engagement customer ignores email and exits after push",
            profile=ben,
            expect_end="exit",
            expect_sent=["email", "push"],
            expect_path=["n1", "n2", "n3", "n4", "n5", "n7", "n8"],
        ),
        UatCase(
            name="No push consent: nothing sent, exits",
            profile=aiko,
            expect_end="exit",
            expect_sent=[],
            expect_path=["n1", "n2", "n3", "n9", "n10", "n12"],
        ),
    ]
