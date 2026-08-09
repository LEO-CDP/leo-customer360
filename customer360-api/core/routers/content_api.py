"""API for personalized content items (news/videos/products/articles) shown
in the Customer 360 profile dashboard, plus a ``/recommended`` endpoint that
ranks items for a given master profile by ``segment_tags`` overlap with that
profile's ``segmentation_tags`` -- computed in PostgreSQL, not hardcoded.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.database import get_db
from core.repositories.content_repository import ContentRepository
from core.schemas.content import (
    ContentItemCreate,
    ContentItemRead,
    ContentItemUpdate,
    RecommendedContentItem,
)
from core.utils.domains import validate_domain_value

router = APIRouter(prefix="/content-items", tags=["Personalized Content"])


@router.get("/", response_model=list[ContentItemRead])
@cache_response("content_items/list", ttl=60)
def list_content_items(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    item_type: Optional[str] = Query(default=None, pattern="^(news|video|product|article)$"),
    skip: int = 0,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    repo = ContentRepository(db)
    return repo.list_items(skip=skip, limit=limit, tenant_id=tenant_id, domain=domain, item_type=item_type)


@router.get("/recommended", response_model=list[RecommendedContentItem])
@cache_response("content_items/recommended", ttl=60)
def get_recommended_content_items(
    master_profile_id: uuid.UUID,
    item_type: Optional[str] = Query(default=None, pattern="^(news|video|product|article)$"),
    limit: int = Query(default=8, le=50),
    db: Session = Depends(get_db),
):
    """Ranks active content items for ``master_profile_id`` by how many
    ``segment_tags`` overlap with the profile's ``segmentation_tags`` (ties
    broken by most-recently published), falling back to domain-matched
    items with no tag overlap when a profile has few/no tags."""
    repo = ContentRepository(db)
    try:
        items = repo.get_recommended_items(master_profile_id, item_type=item_type, limit=limit)
        return items
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/count")
@cache_response("content_items/count", ttl=60)
def count_content_items(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    item_type: Optional[str] = Query(default=None, pattern="^(news|video|product|article)$"),
    db: Session = Depends(get_db),
):
    repo = ContentRepository(db)
    return {"count": repo.count_items(tenant_id=tenant_id, domain=domain, item_type=item_type)}


@router.get("/{content_item_id}", response_model=ContentItemRead)
@cache_response("content_items/item", ttl=60)
def get_content_item(content_item_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = ContentRepository(db)
    obj = repo.get_item(content_item_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpContentItem '{content_item_id}' not found")
    return obj


@router.post("/", response_model=ContentItemRead, status_code=201)
def create_content_item(payload: ContentItemCreate, db: Session = Depends(get_db)):
    try:
        validate_domain_value(db, payload.domain, allow_all=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = ContentRepository(db)
    obj = repo.create_item(payload)
    invalidate_prefix("content_items")
    return obj


@router.patch("/{content_item_id}", response_model=ContentItemRead)
def update_content_item(content_item_id: uuid.UUID, payload: ContentItemUpdate, db: Session = Depends(get_db)):
    obj_in = payload.model_dump(exclude_unset=True)
    if "domain" in obj_in:
        try:
            validate_domain_value(db, obj_in.get("domain"), allow_all=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = ContentRepository(db)
    obj = repo.update_item(content_item_id, payload)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpContentItem '{content_item_id}' not found")
    invalidate_prefix("content_items")
    return obj


@router.delete("/{content_item_id}", status_code=204)
def delete_content_item(content_item_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = ContentRepository(db)
    if not repo.delete_item(content_item_id):
        raise HTTPException(status_code=404, detail=f"CdpContentItem '{content_item_id}' not found")
    invalidate_prefix("content_items")


all_content_routers = [router]
