"""Runs one flow definition for any number of profiles. Wait nodes advance virtual time, so nothing sleeps."""

from __future__ import annotations

import logging

from leo_customer360_dao.models.identity import CdpMasterProfile

from .context import JourneyContext
from .models import FlowDefinition, JourneyResult, NodeDef, NodeResult, StepRecord
from .nodes import BaseNode, NodeFactory
from .services import Services

log = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 100


class FlowEngine:
    def __init__(
        self,
        definition: FlowDefinition,
        services: Services | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.definition = definition
        self.services = services or Services()
        self.max_steps = max_steps
        self.nodes: dict[str, BaseNode] = {}
        self.children: dict[str, list[NodeDef]] = {}
        for node_def, _ in definition.walk():
            if node_def.id in self.nodes:
                raise ValueError(f"Duplicate node id {node_def.id}")
            self.nodes[node_def.id] = NodeFactory.create(node_def)  # fails fast on unknown types
            self.children[node_def.id] = node_def.children

    def problems(self) -> list[str]:
        """Config errors across the whole flow. Check before a run."""
        found = [p for node in self.nodes.values() for p in node.problems()]
        if self.definition.root.type != "trigger":
            found.append(f"{self.definition.root.id}: the flow must start with a trigger")
        if self.definition.root.branch is not None:
            found.append(f"{self.definition.root.id}: the root cannot have a branch")
        for node, _ in self.definition.walk():
            kids = node.children
            if node.type in ("condition", "wait_event"):
                expected = {"yes", "no"} if node.type == "condition" else {"happened", "timeout"}
                actual = [child.branch for child in kids]
                if len(actual) != len(expected) or set(actual) != expected:
                    found.append(f"{node.id}: children must have branches {', '.join(sorted(expected))}")
            elif node.type == "exit":
                if kids:
                    found.append(f"{node.id}: exit nodes cannot have children")
            elif len(kids) > 1 or any(child.branch is not None for child in kids):
                found.append(f"{node.id}: this node can have at most one unbranched child")
        return found

    def _next(self, node_id: str, result: NodeResult) -> NodeDef | None:
        kids = self.children[node_id]
        if result.branch is not None:
            matches = [k for k in kids if k.branch == result.branch]
            if len(matches) != 1:
                raise RuntimeError(f"node {node_id} has no unique child for branch {result.branch!r}")
            return matches[0]
        if len(kids) > 1 or any(child.branch is not None for child in kids):
            raise RuntimeError(f"node {node_id} has ambiguous children without a branch")
        return kids[0] if kids else None

    def run(self, profile: CdpMasterProfile) -> JourneyResult:
        ctx = JourneyContext(profile=profile, services=self.services)
        steps: list[StepRecord] = []
        current: NodeDef | None = self.definition.root
        end, error = "ended", None
        try:
            while current is not None:
                if len(steps) >= self.max_steps:
                    raise RuntimeError(f"more than {self.max_steps} steps; check the flow for loops")
                result = self.nodes[current.id].execute(ctx)
                steps.append(
                    StepRecord(
                        node_id=current.id,
                        node_type=current.type,
                        at_hours=round(ctx.elapsed_hours, 2),
                        branch=result.branch,
                        note=result.note,
                    )
                )
                ctx.elapsed_hours += result.wait_hours
                if result.stop:
                    end = result.stop
                    break
                current = self._next(current.id, result)
        except Exception as exc:  # one bad profile must not stop the run
            log.exception(
                "profile %s failed at node %s",
                profile.master_profile_id,
                current.id if current else "?",
            )
            end, error = "error", str(exc)
        return JourneyResult(
            profile_id=profile.master_profile_id,
            name=ctx.name,
            end=end,
            elapsed_hours=round(ctx.elapsed_hours, 2),
            steps=steps,
            deliveries=ctx.deliveries,
            tags=sorted(ctx.tags),
            error=error,
        )

    def run_many(self, profiles: list[CdpMasterProfile]) -> list[JourneyResult]:
        return [self.run(profile) for profile in profiles]
