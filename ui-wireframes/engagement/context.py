from __future__ import annotations

from dataclasses import dataclass, field

from .models import Profile
from .services import Services


@dataclass
class JourneyContext:
    """Mutable state of one profile moving through one flow."""

    profile: Profile
    services: Services
    elapsed_hours: float = 0.0
    tags: set[str] = field(default_factory=set)
    deliveries: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tags = set(self.profile.tags)
