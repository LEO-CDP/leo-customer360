"""Router for the partitioned graph_edges table.

Custom (not the generic factory) because graph_edges has a composite primary
key (edge_id, relation) required by PostgreSQL list partitioning. edge_id
alone is still globally unique (UUID on the parent table), so lookups
below key off edge_id only for convenience.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_db
from core.repositories.graph_repository import GraphRepository
from core.schemas.graph import GraphEdgeCreate, GraphEdgeRead

router = APIRouter(prefix="/graph-edges", tags=["Graph"])


@router.get("/", response_model=list[GraphEdgeRead])
def list_edges(
    relation: str | None = None,
    from_id: str | None = None,
    to_id: str | None = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    repo = GraphRepository(db)
    return repo.list_edges(relation=relation, from_id=from_id, to_id=to_id, skip=skip, limit=limit)


@router.get("/count")
def count_edges(db: Session = Depends(get_db)):
    repo = GraphRepository(db)
    return {"count": repo.count_edges()}


@router.get("/{edge_id}", response_model=GraphEdgeRead)
def get_edge(edge_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = GraphRepository(db)
    obj = repo.get_edge(edge_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"GraphEdge '{edge_id}' not found")
    return obj


@router.post("/", response_model=GraphEdgeRead, status_code=201)
def create_edge(payload: GraphEdgeCreate, db: Session = Depends(get_db)):
    repo = GraphRepository(db)
    return repo.create_edge(payload.model_dump())


@router.delete("/{edge_id}", status_code=204)
def delete_edge(edge_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = GraphRepository(db)
    try:
        repo.delete_edge(edge_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
