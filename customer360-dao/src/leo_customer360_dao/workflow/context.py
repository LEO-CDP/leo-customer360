"""Mutable state of one profile moving through one flow."""

from __future__ import annotations

from dataclasses import dataclass, field

from leo_customer360_dao.models.identity import CdpMasterProfile

from .services import Services


@dataclass
class JourneyContext:
    profile: CdpMasterProfile
    services: Services
    elapsed_hours: float = 0.0
    tags: set[str] = field(default_factory=set)
    deliveries: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tags = set(self.profile.segmentation_tags or [])

    @property
    def name(self) -> str:
        return self.profile.full_name or " ".join(
            part for part in (self.profile.first_name, self.profile.last_name) if part
        ) or str(self.profile.master_profile_id)

    def template_vars(self) -> dict[str, str]:
        first_name = self.profile.first_name
        if not first_name and self.profile.full_name:
            first_name = self.profile.full_name.split(maxsplit=1)[0]
        return {"first_name": first_name or ""}
