"""Test-only sample profiles and sources for workflow demos and UAT."""

from __future__ import annotations

import random
import uuid
from abc import ABC, abstractmethod

from leo_customer360_dao.models.identity import CdpMasterProfile


class ProfileSource(ABC):
    """Platform hook: return the profiles that enter the flow."""

    @abstractmethod
    def fetch(self) -> list[CdpMasterProfile]: ...


class SampleProfileSource(ProfileSource):
    """Hand-written UAT scenarios, plus optional random profiles."""

    def __init__(self, extra_random: int = 0, seed: int = 42) -> None:
        self.extra_random, self.seed = extra_random, seed

    @staticmethod
    def scenarios() -> list[CdpMasterProfile]:
        def mail(name: str) -> str:
            return f"{name.lower()}@example.com"

        return [
            CdpMasterProfile(
                master_profile_id=uuid.uuid4(),
                full_name="Ines",
                first_name="Ines",
                email=mail("ines"),
                engagement_score=70,
                push_tokens={"push": "test-token"},
                communication_preferences={"email_opt_in": True, "push_opt_in": True},
            ),
            CdpMasterProfile(
                master_profile_id=uuid.uuid4(),
                full_name="Dev",
                first_name="Dev",
                email=mail("dev"),
                engagement_score=40,
                push_tokens={"push": "test-token"},
                communication_preferences={"email_opt_in": True, "push_opt_in": True},
            ),
            CdpMasterProfile(
                master_profile_id=uuid.uuid4(),
                full_name="Ben",
                first_name="Ben",
                email=mail("ben"),
                engagement_score=75,
                push_tokens={"push": "test-token"},
                communication_preferences={"email_opt_in": True, "push_opt_in": True},
            ),
            CdpMasterProfile(
                master_profile_id=uuid.uuid4(),
                full_name="Aiko",
                first_name="Aiko",
                email=mail("aiko"),
                engagement_score=20,
                push_tokens={},
                communication_preferences={"email_opt_in": True, "push_opt_in": False},
            ),
        ]

    def fetch(self) -> list[CdpMasterProfile]:
        rnd = random.Random(self.seed)
        extra = [
            CdpMasterProfile(
                master_profile_id=uuid.uuid4(),
                full_name=f"Sample{i}",
                first_name=f"Sample{i}",
                email=f"sample{i}@example.com",
                phone_number=f"+8490000{i:04d}",
                engagement_score=rnd.randint(10, 95),
                push_tokens={"push": "test-token"} if rnd.random() < 0.7 else {},
                communication_preferences={
                    "email_opt_in": rnd.random() < 0.7,
                    "sms_opt_in": rnd.random() < 0.85,
                    "push_opt_in": rnd.random() < 0.7,
                },
            )
            for i in range(self.extra_random)
        ]
        return self.scenarios() + extra
