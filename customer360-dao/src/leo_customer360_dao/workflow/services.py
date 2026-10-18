"""Integration points for the journey engine.

Subclass these and pass them in ``Services`` to connect real providers. The console
implementations log what would be sent, so UAT runs never deliver a message.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

from leo_customer360_dao.models.identity import CdpMasterProfile

from .models import DeliveryReceipt, OutboundMessage

activation_log = logging.getLogger(__name__ + ".activation")


class ChannelGateway(ABC):
    """One outbound channel."""

    channel: ClassVar[str]

    @abstractmethod
    def send(self, message: OutboundMessage) -> DeliveryReceipt: ...


class ConsoleGateway(ChannelGateway):
    def send(self, message: OutboundMessage) -> DeliveryReceipt:
        activation_log.info(
            "[%s] t=%.1fh node=%s to=%s | %s%s",
            self.channel.upper(),
            message.at_hours,
            message.node_id,
            message.to or "-",
            f"{message.subject} | " if message.subject else "",
            message.body,
        )
        return DeliveryReceipt(status="sent", provider_id=f"console-{self.channel}")


class EmailGateway(ConsoleGateway):
    channel = "email"


class SmsGateway(ConsoleGateway):
    channel = "sms"


class PushGateway(ConsoleGateway):
    channel = "push"


class ProfileWriter(ABC):
    """Persists profile changes made by Update profile / tag nodes."""

    @abstractmethod
    def apply(self, profile: CdpMasterProfile, action: str, tag: str) -> None: ...


class ConsoleProfileWriter(ProfileWriter):
    def apply(self, profile: CdpMasterProfile, action: str, tag: str) -> None:
        activation_log.info("[PROFILE] %s profile=%s tag=%s", action, profile.master_profile_id, tag)


class EventSource(ABC):
    """Answers 'does this customer do X within the next N hours?' for Wait for event nodes."""

    @abstractmethod
    def occurs(
        self, profile: CdpMasterProfile, event: str, start_hours: float, limit_hours: float
    ) -> float | None:
        """Return the journey hour at which the event happens inside the window, else None."""


@dataclass
class ScriptedEventSource(EventSource):
    """UAT event times keyed by master profile ID."""

    events: dict[UUID, dict[str, float]] = field(default_factory=dict)

    def occurs(
        self, profile: CdpMasterProfile, event: str, start_hours: float, limit_hours: float
    ) -> float | None:
        at = self.events.get(profile.master_profile_id, {}).get(event)
        return at if at is not None and start_hours <= at <= start_hours + limit_hours else None


@dataclass
class Services:
    gateways: dict[str, ChannelGateway] = field(
        default_factory=lambda: {g.channel: g for g in (EmailGateway(), SmsGateway(), PushGateway())}
    )
    events: EventSource = field(default_factory=ScriptedEventSource)
    profile_writer: ProfileWriter = field(default_factory=ConsoleProfileWriter)
