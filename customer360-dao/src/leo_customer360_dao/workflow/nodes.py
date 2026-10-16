"""Node classes. To add a node type, subclass ``BaseNode`` (or ``ActivationNode``) and decorate it with ``@register_node``."""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from typing import ClassVar, Literal

from .context import JourneyContext
from .models import NodeDef, NodeResult, OutboundMessage

NODE_REGISTRY: dict[str, type[BaseNode]] = {}
HOURS = {"minutes": 1 / 60, "hours": 1.0, "days": 24.0}
NUMERIC_PROFILE_FIELDS = {
    "engagement_score",
    "lead_conversion_probability",
    "churn_probability",
    "historical_clv",
    "predictive_clv",
    "latest_nps_score",
    "average_csat",
    "overall_sentiment_score",
    "profile_completeness_score",
    "identity_confidence_score",
    "status_code",
}
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def register_node(cls: type[BaseNode]) -> type[BaseNode]:
    NODE_REGISTRY[cls.type] = cls
    return cls


def to_hours(amount: float, unit: str) -> float:
    return float(amount) * HOURS[unit]


def _duration_problems(node_id: str, config: dict) -> list[str]:
    problems: list[str] = []
    try:
        amount = float(config["amount"])
    except (KeyError, OverflowError, TypeError, ValueError):
        problems.append(f"{node_id}: amount must be a number")
    else:
        if not math.isfinite(amount) or amount < 0:
            problems.append(f"{node_id}: amount must be finite and non-negative")
    unit = config.get("unit")
    if not isinstance(unit, str) or unit not in HOURS:
        problems.append(f"{node_id}: unit must be minutes, hours or days")
    return problems


def render(template: str | None, variables: dict[str, str]) -> str:
    """Replace ``{{name}}`` placeholders. Unknown names are left as written."""
    return _PLACEHOLDER.sub(lambda m: variables.get(m.group(1), m.group(0)), template or "")


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
            raise ValueError(
                f'Unknown node type "{definition.type}" (node {definition.id}). Known: {sorted(NODE_REGISTRY)}'
            )
        return cls(definition)


# ---------------------------------------------------------------- Input / Logic
@register_node
class TriggerNode(BaseNode):
    type = "trigger"

    def problems(self) -> list[str]:
        event = self.config.get("event")
        return [] if isinstance(event, str) and event.strip() else [f"{self.id}: event is required"]

    def execute(self, ctx: JourneyContext) -> NodeResult:
        return NodeResult(note=f"entered on {self.config.get('event')}")


@register_node
class ConditionNode(BaseNode):
    type = "condition"
    NUMERIC_OPS = {"ge": lambda a, b: a >= b, "lt": lambda a, b: a < b, "eq": lambda a, b: a == b}

    def problems(self) -> list[str]:
        field, op, value = (self.config.get(k) for k in ("field", "op", "value"))
        if field == "segmentation_tags":
            if op not in ("has", "hasnt"):
                return [f"{self.id}: segmentation_tags needs op has/hasnt"]
            return [] if value is not None else [f"{self.id}: segmentation_tags value is required"]
        if not isinstance(field, str) or field not in NUMERIC_PROFILE_FIELDS:
            return [f"{self.id}: unknown numeric field {field!r}"]
        if not isinstance(op, str) or op not in self.NUMERIC_OPS:
            return [f"{self.id}: op must be ge, lt or eq"]
        if value is None:
            return [f"{self.id}: value must be a number"]
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            return [f"{self.id}: value must be a number"]
        if not math.isfinite(number):
            return [f"{self.id}: value must be finite"]
        return []

    def evaluate(self, ctx: JourneyContext) -> bool:
        field, op, value = (self.config[k] for k in ("field", "op", "value"))
        if field == "segmentation_tags":
            has = str(value) in ctx.tags
            return not has if op == "hasnt" else has
        actual = getattr(ctx.profile, field)
        if actual is None:
            raise ValueError(f"node {self.id}: profile field {field} is null")
        return self.NUMERIC_OPS[op](float(actual), float(value))

    def execute(self, ctx: JourneyContext) -> NodeResult:
        c = self.config
        branch = "yes" if self.evaluate(ctx) else "no"
        return NodeResult(branch=branch, note=f"{c['field']} {c['op']} {c['value']} -> {branch}")


@register_node
class WaitNode(BaseNode):
    type = "wait"

    def problems(self) -> list[str]:
        return _duration_problems(self.id, self.config)

    def execute(self, ctx: JourneyContext) -> NodeResult:
        hours = to_hours(self.config["amount"], self.config["unit"])
        return NodeResult(wait_hours=hours, note=f"waited {self.config['amount']} {self.config['unit']}")


@register_node
class WaitEventNode(BaseNode):
    type = "wait_event"

    def problems(self) -> list[str]:
        problems = _duration_problems(self.id, self.config)
        event = self.config.get("event")
        if not isinstance(event, str) or not event.strip():
            problems.append(f"{self.id}: event is required")
        return problems

    def execute(self, ctx: JourneyContext) -> NodeResult:
        c = self.config
        limit = to_hours(c["amount"], c["unit"])
        at = ctx.services.events.occurs(ctx.profile, c["event"], ctx.elapsed_hours, limit)
        if at is None:
            return NodeResult(
                branch="timeout", wait_hours=limit, note=f"no {c['event']} within {c['amount']} {c['unit']}"
            )
        return NodeResult(
            branch="happened",
            wait_hours=at - ctx.elapsed_hours,
            note=f"{c['event']} after {at - ctx.elapsed_hours:.1f}h",
        )


@register_node
class ExitNode(BaseNode):
    type = "exit"

    def problems(self) -> list[str]:
        kind = self.config.get("kind", "exit")
        return [] if kind in ("goal", "exit") else [f"{self.id}: kind must be goal or exit"]

    def execute(self, ctx: JourneyContext) -> NodeResult:
        kind = self.config.get("kind", "exit")
        if kind not in ("goal", "exit"):
            raise ValueError(f"node {self.id}: kind must be goal or exit")
        return NodeResult(stop="goal" if kind == "goal" else "exit", note=kind)


# ---------------------------------------------------------------- Activate
class ActivationNode(BaseNode):
    """Base for message nodes. Subclasses declare the channel and how to address the message."""

    channel: ClassVar[Literal["email", "sms", "push"]]
    required_config: ClassVar[tuple[str, ...]] = ("body",)

    def problems(self) -> list[str]:
        return [
            f"{self.id}: {key} must be a string"
            for key in self.required_config
            if not isinstance(self.config.get(key), str)
        ]

    @abstractmethod
    def reachable(self, ctx: JourneyContext) -> bool:
        """Consent and contact details for this channel."""

    @abstractmethod
    def build(self, ctx: JourneyContext) -> OutboundMessage: ...

    def message(self, ctx: JourneyContext, to: str | None, subject: str | None, body: str) -> OutboundMessage:
        return OutboundMessage(
            channel=self.channel,
            profile_id=ctx.profile.master_profile_id,
            to=to,
            node_id=self.id,
            at_hours=ctx.elapsed_hours,
            subject=render(subject, ctx.template_vars()) if subject else None,
            body=render(body, ctx.template_vars()),
        )

    def opted_in(self, ctx: JourneyContext) -> bool:
        preferences = ctx.profile.communication_preferences or {}
        return preferences.get(f"{self.channel}_opt_in") is not False

    def execute(self, ctx: JourneyContext) -> NodeResult:
        if not self.reachable(ctx):
            ctx.deliveries.append((self.channel, "skipped"))
            return NodeResult(note=f"{self.channel} skipped: no consent or contact")
        receipt = ctx.services.gateways[self.channel].send(self.build(ctx))
        ctx.deliveries.append((self.channel, receipt.status))
        return NodeResult(
            note=f"{self.channel} {receipt.status}" + (f" ({receipt.detail})" if receipt.detail else "")
        )


@register_node
class SendEmailNode(ActivationNode):
    type, channel = "email", "email"
    required_config = ("subject", "body")

    def reachable(self, ctx: JourneyContext) -> bool:
        return bool(ctx.profile.email) and self.opted_in(ctx)

    def build(self, ctx: JourneyContext) -> OutboundMessage:
        return self.message(ctx, ctx.profile.email, self.config["subject"], self.config["body"])


@register_node
class SendSmsNode(ActivationNode):
    type, channel = "sms", "sms"

    def reachable(self, ctx: JourneyContext) -> bool:
        return bool(ctx.profile.phone_number) and self.opted_in(ctx)

    def build(self, ctx: JourneyContext) -> OutboundMessage:
        return self.message(ctx, ctx.profile.phone_number, None, self.config["body"])


@register_node
class SendPushNode(ActivationNode):
    type, channel = "push", "push"
    required_config = ("title", "body")

    def reachable(self, ctx: JourneyContext) -> bool:
        return self.opted_in(ctx) and bool(ctx.profile.push_tokens)

    def build(self, ctx: JourneyContext) -> OutboundMessage:
        return self.message(
            ctx, f"profile:{ctx.profile.master_profile_id}", self.config["title"], self.config["body"]
        )


@register_node
class UpdateProfileNode(BaseNode):
    type = "update_profile"

    def problems(self) -> list[str]:
        problems = []
        if self.config.get("action") not in ("add_tag", "remove_tag"):
            problems.append(f"{self.id}: action must be add_tag or remove_tag")
        tag = self.config.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            problems.append(f"{self.id}: tag is required")
        return problems

    def execute(self, ctx: JourneyContext) -> NodeResult:
        action, tag = self.config["action"], str(self.config["tag"])
        if action not in ("add_tag", "remove_tag"):
            raise ValueError(f"node {self.id}: action must be add_tag or remove_tag")
        ctx.services.profile_writer.apply(ctx.profile, action, tag)
        if action == "add_tag":
            ctx.tags.add(tag)
        else:
            ctx.tags.discard(tag)
        ctx.profile.segmentation_tags = sorted(ctx.tags)
        return NodeResult(note=f"{action} {tag}")
