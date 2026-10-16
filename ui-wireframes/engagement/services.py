"""Integration points. Subclass these and pass them in `Services` to connect real providers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from .models import DeliveryReceipt, OutboundMessage, Profile

activation_log = logging.getLogger("engagement.activation")


class ChannelGateway(ABC):
    """One outbound channel. Replace the console versions below with your provider."""

    channel: ClassVar[str]

    @abstractmethod
    def send(self, message: OutboundMessage) -> DeliveryReceipt: ...


class ConsoleGateway(ChannelGateway):
    def send(self, message: OutboundMessage) -> DeliveryReceipt:
        activation_log.info(
            "[%s] t=%.1fh node=%s to=%s | %s%s",
            self.channel.upper(), message.at_hours, message.node_id, message.to or "-",
            f"{message.subject} | " if message.subject else "", message.body,
        )
        return DeliveryReceipt(status="sent", provider_id=f"console-{self.channel}")


class EmailGateway(ConsoleGateway):
    channel = "email"
    # PLACEHOLDER: call your email provider here (SES, SendGrid, SMTP...). Return DeliveryReceipt(status="failed") on errors.


class SmsGateway(ConsoleGateway):
    channel = "sms"
    # PLACEHOLDER: call your SMS provider here (Twilio, eSMS, ...).


class PushGateway(ConsoleGateway):
    channel = "push"
    # PLACEHOLDER: call your web push / FCM / APNs sender here. `message.subject` is the title.


class ProfileWriter(ABC):
    """Persists profile changes made by Update profile / tag nodes."""

    @abstractmethod
    def apply(self, profile: Profile, action: str, tag: str) -> None: ...


class ConsoleProfileWriter(ProfileWriter):
    def apply(self, profile: Profile, action: str, tag: str) -> None:
        # PLACEHOLDER: write the tag to your customer profile store.
        activation_log.info("[PROFILE] %s profile=%s tag=%s", action, profile.id, tag)


class EventSource(ABC):
    """Answers 'does this customer do X within the next N hours?' for Wait for event nodes."""

    @abstractmethod
    def occurs(self, profile: Profile, event: str, start_hours: float, limit_hours: float) -> float | None:
        """Return the journey hour at which the event happens inside the window, else None."""


class ScriptedEventSource(EventSource):
    """UAT: events come from `profile.events`. An event dated before the wait starts does not count."""

    def occurs(self, profile: Profile, event: str, start_hours: float, limit_hours: float) -> float | None:
        at = profile.events.get(event)
        return at if at is not None and start_hours <= at <= start_hours + limit_hours else None


@dataclass
class Services:
    gateways: dict[str, ChannelGateway] = field(default_factory=lambda: {
        g.channel: g for g in (EmailGateway(), SmsGateway(), PushGateway())})
    events: EventSource = field(default_factory=ScriptedEventSource)
    profile_writer: ProfileWriter = field(default_factory=ConsoleProfileWriter)
