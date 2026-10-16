"""Loading: flows from PostgreSQL (engagement.load_flow) and profiles from a pluggable source."""
from __future__ import annotations

import json
import random
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, create_engine, text

from .models import FlowDefinition, Profile
from .settings import Settings, get_settings


class FlowNotFound(LookupError):
    pass


class FlowRepository:
    def __init__(self, engine: Engine, schema: str = "engagement") -> None:
        self.engine, self.schema = engine, schema

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> FlowRepository:
        s = settings or get_settings()
        return cls(create_engine(s.database_url, pool_pre_ping=True), s.db_schema)

    def find_flow_id(self, tenant_id: UUID, name: str) -> UUID:
        sql = text(f"SELECT flow_id FROM {self.schema}.flow WHERE tenant_id = CAST(:t AS uuid) AND name = :n")
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"t": str(tenant_id), "n": name}).first()
        if row is None:
            raise FlowNotFound(f'No flow named "{name}" for tenant {tenant_id}')
        return row[0]

    def list_flows(self, tenant_id: UUID) -> list[dict]:
        sql = text(f"SELECT flow_id, name, status, live_version FROM {self.schema}.flow "
                   "WHERE tenant_id = CAST(:t AS uuid) ORDER BY created_at DESC")
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(sql, {"t": str(tenant_id)})]

    def load_definition(self, flow_id: UUID, version: int | None = None) -> FlowDefinition:
        """version=None loads the live version, else the latest draft. Rebuilt from the node and edge tables."""
        sql = text(f"SELECT {self.schema}.load_flow(CAST(:f AS uuid), CAST(:v AS int))")
        with self.engine.connect() as conn:
            payload = conn.execute(sql, {"f": str(flow_id), "v": version}).scalar_one_or_none()
        if payload is None:
            raise FlowNotFound(f"Flow {flow_id} version {version or 'live'} not found")
        return FlowDefinition.model_validate(payload)  # psycopg2 returns jsonb as dict

    @staticmethod
    def load_file(path: str | Path) -> FlowDefinition:
        """Offline: a workflow JSON exported from the studio (workflowAgent.getWorkflow())."""
        return FlowDefinition.model_validate(json.loads(Path(path).read_text()))


class ProfileSource(ABC):
    """Platform hook: return the profiles that enter the flow."""

    @abstractmethod
    def fetch(self) -> list[Profile]: ...


class SampleProfileSource(ProfileSource):
    """Hand-written UAT scenarios (see uat.cart_abandonment_cases) plus optional random fillers."""

    def __init__(self, extra_random: int = 0, seed: int = 42) -> None:
        self.extra_random, self.seed = extra_random, seed

    @staticmethod
    def scenarios() -> list[Profile]:
        mail = lambda n: f"{n.lower()}@example.com"  # noqa: E731
        return [
            Profile(id=1, name="Ines", email=mail("ines"), cart=243, score=70, push=1, events={"purchase": 10}),
            Profile(id=2, name="Dev", email=mail("dev"), cart=80, score=40, push=1, events={"purchase": 30}),
            Profile(id=3, name="Ben", email=mail("ben"), cart=300, score=55, push=1),
            Profile(id=4, name="Aiko", email=mail("aiko"), cart=60, score=20, push=0),
        ]

    def fetch(self) -> list[Profile]:
        rnd = random.Random(self.seed)
        extra = [Profile(id=100 + i, name=f"Sample{i}", email=f"sample{i}@example.com", phone=f"+8490000{i:04d}",
                         score=rnd.randint(10, 95), cart=rnd.randrange(40, 400, 5), recency=rnd.randint(1, 60),
                         push=int(rnd.random() < 0.7), sms=int(rnd.random() < 0.85),
                         events={"purchase": rnd.uniform(1, 60)} if rnd.random() < 0.3 else {})
                 for i in range(self.extra_random)]
        return self.scenarios() + extra
