"""Pydantic models for customer engagement flows: workflow JSON, profiles and journey results.

The workflow JSON is the object the studio produces with ``workflowAgent.getWorkflow()``:
``{"schema_version": 2, "root": {id, type, config, children[], branch?}}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 2


class NodeDef(BaseModel):
    """One node of the flow tree. ``branch`` is set on children of If / else and Wait for event."""

    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    branch: str | None = None
    children: list[NodeDef] = Field(default_factory=list)


class FlowDefinition(BaseModel):
    """A complete flow: the studio's JSON, validated."""

    schema_version: int = SCHEMA_VERSION
    root: NodeDef

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported workflow schema_version {value}; expected {SCHEMA_VERSION}")
        return value

    def walk(self) -> Iterator[tuple[NodeDef, NodeDef | None]]:
        """Depth-first walk yielding (node, parent)."""
        stack: list[tuple[NodeDef, NodeDef | None]] = [(self.root, None)]
        while stack:
            node, parent = stack.pop()
            yield node, parent
            stack.extend((child, node) for child in reversed(node.children))


class OutboundMessage(BaseModel):
    channel: Literal["email", "sms", "push"]
    profile_id: UUID
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
    profile_id: UUID
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
