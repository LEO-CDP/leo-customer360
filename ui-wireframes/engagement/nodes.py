"""Node classes. To add a node type: subclass BaseNode (or ActivationNode), decorate with @register_node."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import ClassVar

from .context import JourneyContext
from .models import NodeDef, NodeResult, OutboundMessage

NODE_REGISTRY: dict[str, type[BaseNode]] = {}
HOURS = {"minutes": 1 / 60, "hours": 1.0, "days": 24.0}


def register_node(cls: type[BaseNode]) -> type[BaseNode]:
    NODE_REGISTRY[cls.type] = cls
    return cls


def to_hours(amount: float, unit: str) -> float:
    return float(amount) * HOURS[unit]


def render(template: str, variables: dict[str, str]) -> str:
    """Replace {{name}} placeholders. Unknown names are left as written."""
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: variables.get(m.group(1), m.group(0)), template or "")


class BaseNode(ABC):
    type: ClassVar[str]

    def __init__(self, definition: NodeDef) -> None:
        self.definition = definition

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def config(self) -> dict:
        return self.definition.config

    def problems(self) -> list[str]:
        """Config errors found before running. Override to add checks."""
        return []

    @abstractmethod
    def execute(self, ctx: JourneyContext) -> NodeResult: ...


class NodeFactory:
    @staticmethod
    def create(definition: NodeDef) -> BaseNode:
        cls = NODE_REGISTRY.get(definition.type)
        if cls is None:
            raise ValueError(f'Unknown node type "{definition.type}" (node {definition.id}). Known: {sorted(NODE_REGISTRY)}')
        return cls(definition)


# ---------------------------------------------------------------- Input / Logic
@register_node
class TriggerNode(BaseNode):
    type = "trigger"

    def execute(self, ctx: JourneyContext) -> NodeResult:
        # PLACEHOLDER: in production the platform starts the journey when this event arrives.
        return NodeResult(note=f"entered on {self.config.get('event')}")


@register_node
class ConditionNode(BaseNode):
    type = "condition"
    NUMERIC_OPS = {"ge": lambda a, b: a >= b, "lt": lambda a, b: a < b, "eq": lambda a, b: a == b}

    def problems(self) -> list[str]:
        field, op, value = (self.config.get(k) for k in ("field", "op", "value"))
        if field == "tags":
            return [] if op in ("has", "hasnt") else [f"{self.id}: tags needs op has/hasnt"]
        if op not in self.NUMERIC_OPS:
            return [f"{self.id}: op must be ge, lt or eq"]
        try:
            float(value)
        except (TypeError, ValueError):
            return [f"{self.id}: value must be a number"]
        return []

    def evaluate(self, ctx: JourneyContext) -> bool:
        field, op, value = (self.config[k] for k in ("field", "op", "value"))
        if field == "tags":
            has = str(value) in ctx.tags
            return not has if op == "hasnt" else has
        actual = getattr(ctx.profile, field)
        return self.NUMERIC_OPS[op](float(actual), float(value))

    def execute(self, ctx: JourneyContext) -> NodeResult:
        c = self.config
        branch = "yes" if self.evaluate(ctx) else "no"
        return NodeResult(branch=branch, note=f"{c['field']} {c['op']} {c['value']} -> {branch}")


@register_node
class WaitNode(BaseNode):
    type = "wait"

    def execute(self, ctx: JourneyContext) -> NodeResult:
        hours = to_hours(self.config["amount"], self.config["unit"])
        return NodeResult(wait_hours=hours, note=f"waited {self.config['amount']} {self.config['unit']}")


@register_node
class WaitEventNode(BaseNode):
    type = "wait_event"

    def execute(self, ctx: JourneyContext) -> NodeResult:
        c = self.config
        limit = to_hours(c["amount"], c["unit"])
        at = ctx.services.events.occurs(ctx.profile, c["event"], ctx.elapsed_hours, limit)
        if at is None:
            return NodeResult(branch="timeout", wait_hours=limit, note=f"no {c['event']} within {c['amount']} {c['unit']}")
        return NodeResult(branch="happened", wait_hours=at - ctx.elapsed_hours, note=f"{c['event']} after {at - ctx.elapsed_hours:.1f}h")


@register_node
class ExitNode(BaseNode):
    type = "exit"

    def execute(self, ctx: JourneyContext) -> NodeResult:
        kind = self.config.get("kind", "exit")
        return NodeResult(stop="goal" if kind == "goal" else "exit", note=kind)


# ---------------------------------------------------------------- Activate
class ActivationNode(BaseNode):
    """Base for message nodes. Subclasses declare the channel and how to build / address the message."""

    channel: ClassVar[str]

    @abstractmethod
    def reachable(self, ctx: JourneyContext) -> bool:
        """Consent and contact details for this channel."""

    @abstractmethod
    def build(self, ctx: JourneyContext) -> OutboundMessage: ...

    def message(self, ctx: JourneyContext, to: str | None, subject: str | None, body: str) -> OutboundMessage:
        v = ctx.profile.template_vars()
        return OutboundMessage(channel=self.channel, profile_id=ctx.profile.id, to=to, node_id=self.id,
                               at_hours=ctx.elapsed_hours, subject=render(subject, v) if subject else None, body=render(body, v))

    def execute(self, ctx: JourneyContext) -> NodeResult:
        if not self.reachable(ctx):
            ctx.deliveries.append((self.channel, "skipped"))
            return NodeResult(note=f"{self.channel} skipped: no consent or contact")
        receipt = ctx.services.gateways[self.channel].send(self.build(ctx))
        ctx.deliveries.append((self.channel, receipt.status))
        return NodeResult(note=f"{self.channel} {receipt.status}" + (f" ({receipt.detail})" if receipt.detail else ""))


@register_node
class SendEmailNode(ActivationNode):
    type, channel = "email", "email"

    def reachable(self, ctx: JourneyContext) -> bool:
        return bool(ctx.profile.email) and ctx.profile.email_opt_in == 1

    def build(self, ctx: JourneyContext) -> OutboundMessage:
        return self.message(ctx, ctx.profile.email, self.config["subject"], self.config["body"])


@register_node
class SendSmsNode(ActivationNode):
    type, channel = "sms", "sms"

    def reachable(self, ctx: JourneyContext) -> bool:
        return bool(ctx.profile.phone) and ctx.profile.sms == 1

    def build(self, ctx: JourneyContext) -> OutboundMessage:
        return self.message(ctx, ctx.profile.phone, None, self.config["body"])


@register_node
class SendPushNode(ActivationNode):
    type, channel = "push", "push"

    def reachable(self, ctx: JourneyContext) -> bool:
        return ctx.profile.push == 1

    def build(self, ctx: JourneyContext) -> OutboundMessage:
        return self.message(ctx, f"profile:{ctx.profile.id}", self.config["title"], self.config["body"])


@register_node
class UpdateProfileNode(BaseNode):
    type = "update_profile"

    def execute(self, ctx: JourneyContext) -> NodeResult:
        action, tag = self.config["action"], str(self.config["tag"])
        (ctx.tags.add if action == "add_tag" else ctx.tags.discard)(tag)
        ctx.services.profile_writer.apply(ctx.profile, action, tag)
        return NodeResult(note=f"{action} {tag}")
