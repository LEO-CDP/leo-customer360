"""UAT: run a flow for known profiles and assert the path, sends and outcome."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .engine import FlowEngine
from .models import FlowDefinition, JourneyResult, Profile
from .repository import SampleProfileSource


class UatCase(BaseModel):
    name: str
    profile: Profile
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
        r = self.engine.run(case.profile)
        fails: list[str] = []
        if r.error:
            fails.append(f"error: {r.error}")
        if case.expect_end is not None and r.end != case.expect_end:
            fails.append(f"end: expected {case.expect_end}, got {r.end}")
        if case.expect_path is not None and r.path != case.expect_path:
            fails.append(f"path: expected {case.expect_path}, got {r.path}")
        if case.expect_sent is not None and r.sent != case.expect_sent:
            fails.append(f"sent: expected {case.expect_sent}, got {r.sent}")
        missing = [t for t in case.expect_tags if t not in r.tags]
        if missing:
            fails.append(f"tags missing: {missing}")
        return CaseOutcome(case=case.name, passed=not fails, failures=fails, result=r)

    def run(self, cases: list[UatCase]) -> list[CaseOutcome]:
        return [self.check(c) for c in cases]


def print_journeys(results: list[JourneyResult]) -> None:
    print(f"\n{'profile':<12}{'end':<7}{'days':>6}  path")
    for r in results:
        print(f"{r.name:<12}{r.end:<7}{r.elapsed_hours / 24:>6.1f}  {' > '.join(r.path)}   sent={r.sent} tags={r.tags}")
    ends = {e: sum(r.end == e for r in results) for e in ("goal", "exit", "ended", "error")}
    print(f"\n{len(results)} profiles: " + ", ".join(f"{k}={v}" for k, v in ends.items()))


def print_uat(outcomes: list[CaseOutcome]) -> int:
    for o in outcomes:
        print(f"[{'PASS' if o.passed else 'FAIL'}] {o.case}")
        for f in o.failures:
            print(f"       - {f}")
    failed = sum(not o.passed for o in outcomes)
    print(f"\nUAT: {len(outcomes) - failed}/{len(outcomes)} passed")
    return 1 if failed else 0


# ---- built-in demo flow and its UAT suite (same as the studio's "Cart abandonment" recipe)
def _n(id, type, config, children=(), branch=None):  # noqa: A002
    node = {"id": id, "type": type, "config": config, "children": list(children)}
    if branch:
        node["branch"] = branch
    return node


CART_ABANDONMENT = FlowDefinition.model_validate({"schema_version": 2, "root": _n("n1", "trigger", {"event": "cart_abandoned"}, [
    _n("n2", "wait", {"amount": 1, "unit": "hours"}, [
        _n("n3", "condition", {"field": "cart", "op": "ge", "value": "150"}, [
            _n("n4", "email", {"subject": "{{first_name}}, early access to your cart",
                               "body": "Your ${{cart_value}} picks are held for 24 hours with free express shipping."}, [
                _n("n5", "wait_event", {"event": "purchase", "amount": 2, "unit": "days"}, [
                    _n("n6", "exit", {"kind": "goal"}, branch="happened"),
                    _n("n7", "push", {"title": "Flash sale: 15% off today", "body": "{{first_name}}, your saved items are on sale."}, [
                        _n("n8", "exit", {"kind": "exit"})], branch="timeout")])], branch="yes"),
            _n("n9", "push", {"title": "Still thinking it over?", "body": "{{first_name}}, your cart is one tap away."}, [
                _n("n10", "wait_event", {"event": "purchase", "amount": 2, "unit": "days"}, [
                    _n("n11", "exit", {"kind": "goal"}, branch="happened"),
                    _n("n12", "exit", {"kind": "exit"}, branch="timeout")])], branch="no")])])])})


def cart_abandonment_cases() -> list[UatCase]:
    ines, dev, ben, aiko = SampleProfileSource.scenarios()
    return [
        UatCase(name="Big cart buys after the email", profile=ines, expect_end="goal", expect_sent=["email"],
                expect_path=["n1", "n2", "n3", "n4", "n5", "n6"]),
        UatCase(name="Small cart buys after the push", profile=dev, expect_end="goal", expect_sent=["push"],
                expect_path=["n1", "n2", "n3", "n9", "n10", "n11"]),
        UatCase(name="Big cart ignores email, gets push, exits", profile=ben, expect_end="exit", expect_sent=["email", "push"],
                expect_path=["n1", "n2", "n3", "n4", "n5", "n7", "n8"]),
        UatCase(name="No push consent: nothing sent, exits", profile=aiko, expect_end="exit", expect_sent=[],
                expect_path=["n1", "n2", "n3", "n9", "n10", "n12"]),
    ]
