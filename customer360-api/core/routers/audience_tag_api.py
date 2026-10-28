"""API router for ``customer360.cdp_audience_tags`` (IAB Audience Taxonomy 1.1).

Provides endpoints to:
- Upload and upsert ``audience_taxonomy1.1.tsv`` keyed by ``tag_name``
- Seed from the repository's default ``audience_taxonomy1.1.tsv``
- Upsert individual or batch audience taxonomy tags by ``tag_name``
- Browse, filter, and search IAB audience tags for Audience Builder / segment
  tagging, personalization, and programmatic advertising.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.database import get_db
from core.repositories.audience_tag_repository import AudienceTagRepository
from core.utils.audience_taxonomy_utils import (
    MAX_AUDIENCE_TAXONOMY_TSV_BYTES,
    AudienceTaxonomyTsvError,
)
from leo_customer360_dao.config import settings
from leo_customer360_dao.schemas.segmentation import (
    AudienceTagCreate,
    AudienceTagImportResult,
    AudienceTagRead,
    AudienceTagUpdate,
)

audience_tags_router = APIRouter(prefix="/audience-tags", tags=["IAB Audience Taxonomy"])


@audience_tags_router.get("", response_model=list[AudienceTagRead], include_in_schema=False)
@audience_tags_router.get("/", response_model=list[AudienceTagRead])
@cache_response("cdp_audience_tags/list", ttl=settings.cache_ttl_seconds)
def list_audience_tags(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=2000, ge=1, le=5000),
    tier_1: Optional[str] = Query(default=None),
    tier_2: Optional[str] = Query(default=None),
    tier_3: Optional[str] = Query(default=None),
    parent_id: Optional[str] = Query(default=None),
    has_parent: Optional[bool] = Query(default=None),
    taxonomy_version: Optional[str] = Query(default=None),
    status_code: Optional[int] = Query(default=1),
    q: Optional[str] = Query(default=None, max_length=200),
    seed_if_empty: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """List IAB Audience Taxonomy tags with optional tier, parent_id, has_parent, and text search filters."""
    repo = AudienceTagRepository(db)
    if seed_if_empty and skip == 0 and not any((tier_1, tier_2, tier_3, parent_id, q)):
        try:
            if repo.count_tags(status_code=None) == 0:
                repo.import_from_tsv_file(taxonomy_version=taxonomy_version or "1.1")
        except Exception:
            pass
    return repo.list_tags(
        skip=skip,
        limit=limit,
        tier_1=tier_1,
        tier_2=tier_2,
        tier_3=tier_3,
        parent_id=parent_id,
        has_parent=has_parent,
        taxonomy_version=taxonomy_version,
        status_code=status_code,
        q=q,
    )


@audience_tags_router.get("/count")
@cache_response("cdp_audience_tags/count", ttl=settings.cache_ttl_seconds)
def count_audience_tags(
    tier_1: Optional[str] = Query(default=None),
    tier_2: Optional[str] = Query(default=None),
    tier_3: Optional[str] = Query(default=None),
    parent_id: Optional[str] = Query(default=None),
    has_parent: Optional[bool] = Query(default=None),
    taxonomy_version: Optional[str] = Query(default=None),
    status_code: Optional[int] = Query(default=1),
    q: Optional[str] = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Count IAB Audience Taxonomy tags matching optional filters."""
    count = AudienceTagRepository(db).count_tags(
        tier_1=tier_1,
        tier_2=tier_2,
        tier_3=tier_3,
        parent_id=parent_id,
        has_parent=has_parent,
        taxonomy_version=taxonomy_version,
        status_code=status_code,
        q=q,
    )
    return {"count": count}


@audience_tags_router.post("", response_model=AudienceTagRead, status_code=status.HTTP_200_OK, include_in_schema=False)
@audience_tags_router.post("/", response_model=AudienceTagRead, status_code=status.HTTP_200_OK)
def upsert_audience_tag(
    payload: AudienceTagCreate,
    db: Session = Depends(get_db),
):
    """Create or update (upsert) a single IAB audience tag keyed by ``tag_name``."""
    tag = AudienceTagRepository(db).upsert_tag(payload, commit=True)
    invalidate_prefix("cdp_audience_tags")
    return tag


@audience_tags_router.post("/upsert", response_model=AudienceTagImportResult)
def bulk_upsert_audience_tags(
    payload: list[AudienceTagCreate],
    taxonomy_version: str = Query(default="1.1", min_length=1),
    db: Session = Depends(get_db),
) -> AudienceTagImportResult:
    """Bulk upsert a list of IAB audience taxonomy tags keyed by ``tag_name``."""
    if not payload:
        raise HTTPException(status_code=422, detail="At least one audience tag record is required")
    repo = AudienceTagRepository(db)
    upserted_count = repo.bulk_upsert_tags(payload, commit=True)
    invalidate_prefix("cdp_audience_tags")
    return AudienceTagImportResult(
        taxonomy_version=taxonomy_version,
        total_rows_parsed=len(payload),
        upserted_count=upserted_count,
        sample_tag_names=[item.tag_name for item in payload[:10] if item.tag_name],
    )


@audience_tags_router.post("/import/tsv", response_model=AudienceTagImportResult)
async def upload_audience_taxonomy_tsv(
    file: UploadFile = File(...),
    taxonomy_version: str = Query(default="1.1", min_length=1),
    db: Session = Depends(get_db),
) -> AudienceTagImportResult:
    """Upload an IAB Audience Taxonomy ``.tsv`` file and upsert all rows by ``tag_name``."""
    if not file.filename or not file.filename.lower().endswith(".tsv"):
        await file.close()
        raise HTTPException(status_code=415, detail="Upload a .tsv file")

    content = await file.read(MAX_AUDIENCE_TAXONOMY_TSV_BYTES + 1)
    await file.close()
    if len(content) > MAX_AUDIENCE_TAXONOMY_TSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"TSV uploads are limited to {MAX_AUDIENCE_TAXONOMY_TSV_BYTES} bytes",
        )

    try:
        result = AudienceTagRepository(db).import_from_tsv_bytes(
            content,
            taxonomy_version=taxonomy_version,
        )
    except AudienceTaxonomyTsvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    invalidate_prefix("cdp_audience_tags")
    return result


@audience_tags_router.post("/seed-default", response_model=AudienceTagImportResult)
def seed_default_audience_taxonomy(
    taxonomy_version: str = Query(default="1.1", min_length=1),
    db: Session = Depends(get_db),
) -> AudienceTagImportResult:
    """Upsert the bundled ``core/all_base_data/audience_taxonomy1.1.tsv`` into ``cdp_audience_tags``."""
    try:
        result = AudienceTagRepository(db).import_from_tsv_file(taxonomy_version=taxonomy_version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AudienceTaxonomyTsvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    invalidate_prefix("cdp_audience_tags")
    return result


@audience_tags_router.get("/{tag_name:path}", response_model=AudienceTagRead)
@cache_response("cdp_audience_tags/item", ttl=settings.cache_ttl_seconds)
def get_audience_tag(
    tag_name: str,
    db: Session = Depends(get_db),
):
    """Fetch a single IAB Audience Taxonomy tag by its ``tag_name`` primary key."""
    obj = AudienceTagRepository(db).get_tag(tag_name)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpAudienceTag '{tag_name}' not found")
    return obj


@audience_tags_router.patch("/{tag_name:path}", response_model=AudienceTagRead)
def update_audience_tag(
    tag_name: str,
    payload: AudienceTagUpdate,
    db: Session = Depends(get_db),
):
    """Update fields on an existing IAB Audience Taxonomy tag."""
    obj = AudienceTagRepository(db).update_tag(tag_name, payload)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpAudienceTag '{tag_name}' not found")
    invalidate_prefix("cdp_audience_tags")
    return obj


@audience_tags_router.delete("/{tag_name:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_audience_tag(
    tag_name: str,
    db: Session = Depends(get_db),
) -> None:
    """Delete an IAB Audience Taxonomy tag by ``tag_name``."""
    deleted = AudienceTagRepository(db).delete_tag(tag_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"CdpAudienceTag '{tag_name}' not found")
    invalidate_prefix("cdp_audience_tags")


all_audience_tag_routers = [audience_tags_router]
