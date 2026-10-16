"""Pydantic models: flow JSON (schema_version 2), profiles and run results."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


class NodeDef(BaseModel):
    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    branch: str | None = None  # set on children of If / else (yes|no) and Wait for event (happened|timeout)
    children: list[NodeDef] = Field(default_factory=list)


class FlowDefinition(BaseModel):
    schema_version: int = 2
    root: NodeDef

    def walk(self) -> Iterator[tuple[NodeDef, NodeDef | None]]:
        stack: list[tuple[NodeDef, NodeDef | None]] = [(self.root, None)]
        while stack:
            node, parent = stack.pop()
            yield node, parent
            stack.extend((child, node) for child in reversed(node.children))


class Profile(BaseModel):
    id: int
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    score: int = 50
    cart: float = 0
    recency: int = 0  # days since last purchase
    push: int = 1  # push opt-in and token present
    sms: int = 1  # SMS opt-in
    email_opt_in: int = 1
    tags: list[str] = Field(default_factory=list)
    # UAT script: event name -> hours after the journey starts at which the customer does it.
    events: dict[str, float] = Field(default_factory=dict)

    def template_vars(self) -> dict[str, str]:
        cart = int(self.cart) if float(self.cart).is_integer() else self.cart
        return {"first_name": self.name, "cart_value": str(cart)}


class OutboundMessage(BaseModel):
    channel: Literal["email", "sms", "push"]
    profile_id: int
    to: str | None
    subject: str | None = None  # email subject or push title
    body: str
    node_id: str
    at_hours: float


class DeliveryReceipt(BaseModel):
    status: Literal["sent", "skipped", "failed"]
    provider_id: str | None = None
    detail: str = ""


class NodeResult(BaseModel):
    branch: str | None = None  # which child to follow; None = the single child
    wait_hours: float = 0  # virtual time to spend before moving on
    stop: Literal["goal", "exit"] | None = None
    note: str = ""


class StepRecord(BaseModel):
    node_id: str
    node_type: str
    at_hours: float
    branch: str | None = None
    note: str = ""


class JourneyResult(BaseModel):
    profile_id: int
    name: str
    end: Literal["goal", "exit", "ended", "error"]
    elapsed_hours: float
    steps: list[StepRecord]
    deliveries: list[tuple[str, str]]  # (channel, status) in order
    tags: list[str]
    error: str | None = None

    @property
    def path(self) -> list[str]:
        return [s.node_id for s in self.steps]

    @property
    def sent(self) -> list[str]:
        return [channel for channel, status in self.deliveries if status == "sent"]
