"""Graph repository: the partitioned ``graph_edges`` knowledge-graph table.

Custom (not CRUDBase-backed) because graph_edges has a composite primary key
(edge_id, relation) required by PostgreSQL list partitioning. edge_id alone
is still globally unique (UUID on the parent table), so lookups here key off
edge_id only for convenience.
"""

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models.graph import GraphEdge


class GraphRepository:
    """Repository for graph edge CRUD."""

    def __init__(self, session: Session):
        self.session = session

    def list_edges(
        self,
        relation: Optional[str] = None,
        from_id: Optional[str] = None,
        to_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[GraphEdge]:
        stmt = select(GraphEdge)
        if relation:
            stmt = stmt.where(GraphEdge.relation == relation)
        if from_id:
            stmt = stmt.where(GraphEdge.from_id == from_id)
        if to_id:
            stmt = stmt.where(GraphEdge.to_id == to_id)
        stmt = stmt.offset(skip).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def count_edges(self) -> int:
        return self.session.execute(select(func.count()).select_from(GraphEdge)).scalar_one()

    def get_edge(self, edge_id: uuid.UUID) -> Optional[GraphEdge]:
        return self.session.execute(select(GraphEdge).where(GraphEdge.edge_id == edge_id)).scalar_one_or_none()

    def create_edge(self, payload: dict) -> GraphEdge:
        obj = GraphEdge(**payload)
        self.session.add(obj)
        self.session.commit()
        self.session.refresh(obj)
        return obj

    def delete_edge(self, edge_id: uuid.UUID) -> None:
        obj = self.get_edge(edge_id)
        if obj is None:
            raise ValueError(f"GraphEdge '{edge_id}' not found")
        self.session.delete(obj)
        self.session.commit()
