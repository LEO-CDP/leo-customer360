from __future__ import annotations

import logging

from .context import JourneyContext
from .models import FlowDefinition, JourneyResult, NodeDef, NodeResult, Profile, StepRecord
from .nodes import BaseNode, NodeFactory
from .services import Services

log = logging.getLogger("engagement.engine")


class FlowEngine:
    """Runs one flow definition for any number of profiles. Wait nodes advance virtual time (no sleeping)."""

    def __init__(self, definition: FlowDefinition, services: Services | None = None, max_steps: int = 100) -> None:
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
        """Config errors across the whole flow. Run before UAT."""
        found = [p for node in self.nodes.values() for p in node.problems()]
        if self.definition.root.type != "trigger":
            found.append(f"{self.definition.root.id}: the flow must start with a trigger")
        return found

    def _next(self, node_id: str, result: NodeResult) -> NodeDef | None:
        kids = self.children[node_id]
        if result.branch:
            return next((k for k in kids if k.branch == result.branch), None)
        return kids[0] if kids else None

    def run(self, profile: Profile) -> JourneyResult:
        ctx = JourneyContext(profile=profile, services=self.services)
        steps: list[StepRecord] = []
        current: NodeDef | None = self.definition.root
        end, error = "ended", None
        try:
            while current is not None:
                if len(steps) >= self.max_steps:
                    raise RuntimeError(f"more than {self.max_steps} steps; check the flow for loops")
                result = self.nodes[current.id].execute(ctx)
                steps.append(StepRecord(node_id=current.id, node_type=current.type, at_hours=round(ctx.elapsed_hours, 2),
                                        branch=result.branch, note=result.note))
                ctx.elapsed_hours += result.wait_hours
                if result.stop:
                    end = result.stop
                    break
                current = self._next(current.id, result)
        except Exception as exc:  # one bad profile must not stop the run
            log.exception("profile %s failed at node %s", profile.id, current.id if current else "?")
            end, error = "error", str(exc)
        return JourneyResult(profile_id=profile.id, name=profile.name, end=end, elapsed_hours=round(ctx.elapsed_hours, 2),
                             steps=steps, deliveries=ctx.deliveries, tags=sorted(ctx.tags), error=error)

    def run_many(self, profiles: list[Profile]) -> list[JourneyResult]:
        return [self.run(p) for p in profiles]
