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
# Must match EVENTS, WAIT_EVENTS and TEMPLATE_VARS in static/js/workflow/workflow-core.js.
TRIGGER_EVENTS = frozenset({"signup", "cart_abandoned", "purchase", "inactive_30d", "birthday"})
WAIT_EVENTS = frozenset({"purchase", "email_opened", "email_clicked"})
MESSAGE_VARIABLES = frozenset({"first_name"})
AUDIENCE_PLATFORMS = frozenset({"meta_ads", "google_ads", "tiktok_ads"})
NOTIFICATION_PROVIDERS = frozenset({"sms", "zalo_oa", "whatsapp", "mobile_native"})
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
        if not isinstance(event, str) or not event.strip():
            return [f"{self.id}: event is required"]
        if event not in TRIGGER_EVENTS:
            return [f"{self.id}: event must be one of {', '.join(sorted(TRIGGER_EVENTS))}"]
        return []

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
            # A missing value never matches, so the customer takes the no branch.
            return False
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
        elif event not in WAIT_EVENTS:
            problems.append(f"{self.id}: event must be one of {', '.join(sorted(WAIT_EVENTS))}")
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

    channel: ClassVar[Literal["email", "notification", "push"]]
    required_config: ClassVar[tuple[str, ...]] = ("body",)

    def problems(self) -> list[str]:
        problems = [
            f"{self.id}: {key} must be a non-empty string"
            for key in self.required_config
            if not isinstance(self.config.get(key), str) or not self.config[key].strip()
        ]
        for key in ("subject", "title", "body"):
            text = self.config.get(key)
            if not isinstance(text, str):
                continue
            for name in _PLACEHOLDER.findall(text):
                if name not in MESSAGE_VARIABLES:
                    problems.append(f"{self.id}: unknown placeholder {{{{{name}}}}} in {key}")
        return problems

    @abstractmethod
    def reachable(self, ctx: JourneyContext) -> bool:
        """Consent and contact details for this channel."""

    @abstractmethod
    def build(self, ctx: JourneyContext) -> OutboundMessage: ...

    def message(self, ctx: JourneyContext, to: str | None, subject: str | None, body: str) -> OutboundMessage:
        return OutboundMessage(
            channel=self.channel,
            provider=self.config.get("provider") if self.channel == "notification" else None,
            profile_id=ctx.profile.master_profile_id,
            to=to,
            node_id=self.id,
            at_hours=ctx.elapsed_hours,
            subject=render(subject, ctx.template_vars()) if subject else None,
            body=render(body, ctx.template_vars()),
        )

    def opted_in(self, ctx: JourneyContext) -> bool:
        preferences = ctx.profile.communication_preferences or {}
        return preferences.get(f"{self.channel}_opt_in") is True

    def execute(self, ctx: JourneyContext) -> NodeResult:
        if not self.reachable(ctx):
            ctx.deliveries.append((self.channel, "skipped"))
            return NodeResult(note=f"{self.channel} skipped: no consent or contact")
        receipt = ctx.services.gateways[self.channel].send(self.build(ctx))
        ctx.deliveries.append((self.channel, receipt.status))
        if receipt.status == "failed":
            raise RuntimeError(
                f"{self.channel} delivery failed at node {self.id}: {receipt.detail}"
            )
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
class SendNotificationNode(ActivationNode):
    type, channel = "notification", "notification"

    def problems(self) -> list[str]:
        problems = super().problems()
        provider = self.config.get("provider", "sms")
        if not isinstance(provider, str) or provider not in NOTIFICATION_PROVIDERS:
            problems.append(
                f"{self.id}: provider must be one of {', '.join(sorted(NOTIFICATION_PROVIDERS))}"
            )
        return problems

    def reachable(self, ctx: JourneyContext) -> bool:
        provider = self.config.get("provider", "sms")
        preferences = ctx.profile.communication_preferences or {}
        consent_key = "push_opt_in" if provider == "mobile_native" else "sms_opt_in"
        consented = preferences.get(consent_key) is True
        destination_available = (
            bool(ctx.profile.push_tokens)
            if provider == "mobile_native"
            else bool(ctx.profile.phone_number)
        )
        return destination_available and consented

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
        # The profile itself is never modified here: the ProfileWriter persists the change.
        return NodeResult(note=f"{action} {tag}")


@register_node
class DataSyncNode(BaseNode):
    """Declarative campaign audience sync marker.

    Campaign activation reads this node and submits one batch Dagster export.
    It deliberately does not call a provider per profile during ``FlowEngine``
    execution.
    """

    type = "run_data_synch_job"

    def problems(self) -> list[str]:
        platform = self.config.get("platform")
        audience_name = self.config.get("audience_name")
        problems = []
        if not isinstance(platform, str) or platform not in AUDIENCE_PLATFORMS:
            problems.append(f"{self.id}: platform must be one of {', '.join(sorted(AUDIENCE_PLATFORMS))}")
        if not isinstance(audience_name, str) or not audience_name.strip():
            problems.append(f"{self.id}: audience_name is required")
        return problems

    def execute(self, ctx: JourneyContext) -> NodeResult:
        return NodeResult(
            note=f"audience sync requested for {self.config['platform']}: {self.config['audience_name']}"
        )
