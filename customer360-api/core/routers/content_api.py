"""API for personalized content items (news/videos/products/articles) shown
in the Customer 360 profile dashboard, plus a ``/recommended`` endpoint that
serves persisted ranking-workflow results for one profile, optionally filtered
to a segment.
"""

import uuid
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.cache import cache_response, invalidate_prefix
from core.database import get_db
from core.repositories.content_repository import ContentRepository
from core.repositories.product_item_repository import ProductItemRepository
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client
from core.utils.domains import validate_domain_value
from core.utils.content_utils import (
    MAX_CONTENT_TSV_BYTES,
    ContentImportStorageError,
    ContentTsvError,
    delete_staged_content_import,
    parse_content_tsv,
    stage_content_import,
)
from leo_customer360_dao.schemas.content import (
    ContentItemCreate,
    ContentItemRead,
    ContentItemUpdate,
    RecommendedContentItem,
)
from leo_customer360_dao.schemas.product_item import ProductItemCreate, ProductItemRead
from core.utils.product_utils import (
    MAX_PRODUCT_TSV_BYTES,
    ProductImportStorageError,
    ProductTsvError,
    delete_staged_import,
    parse_product_tsv,
    stage_product_import,
)

router = APIRouter(prefix="/content-items", tags=["Personalized Content"])
logger = logging.getLogger(__name__)
TenantId = Annotated[str, Depends(require_tenant)]


def _tenant_uuid_or_forbidden(tenant_id: str, requested_tenant_id: uuid.UUID | None) -> uuid.UUID:
    context_tenant_id = uuid.UUID(tenant_id)
    if requested_tenant_id is not None and requested_tenant_id != context_tenant_id:
        raise HTTPException(status_code=403, detail="Requested tenant does not match tenant context")
    return context_tenant_id


@router.post("/import/content", status_code=status.HTTP_202_ACCEPTED)
async def upload_content_tsv(
    tenant_id: TenantId,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    """Validate and asynchronously import content rows from a TSV upload."""
    if not file.filename or not file.filename.lower().endswith(".tsv"):
        await file.close()
        raise HTTPException(status_code=415, detail="Upload a .tsv file")

    content = await file.read(MAX_CONTENT_TSV_BYTES + 1)
    await file.close()
    if len(content) > MAX_CONTENT_TSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"TSV uploads are limited to {MAX_CONTENT_TSV_BYTES} bytes",
        )
    try:
        records = parse_content_tsv(content)
        for domain in {record.domain for record in records}:
            validate_domain_value(db, domain)
    except ContentTsvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tenant_uuid = uuid.UUID(tenant_id)
    try:
        staged = stage_content_import(tenant_uuid, records)
    except ContentImportStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        run_id = dagster_client.content_item_import.import_content(
            tenant_id=staged.tenant_id,
            bucket=staged.bucket,
            object_key=staged.object_key,
        )
    except DagsterJobTriggerError as exc:
        try:
            delete_staged_content_import(staged.bucket, staged.object_key)
        except Exception:
            logger.exception(
                "Failed to remove unsubmitted content import object (tenant_id=%s)",
                staged.tenant_id,
            )
        raise HTTPException(
            status_code=503,
            detail=f"Could not submit content import to Dagster: {exc}",
        ) from exc

    return {
        "run_id": run_id,
        "status": "submitted",
        "content_items_submitted": staged.row_count,
    }


@router.post("/import/products", status_code=status.HTTP_202_ACCEPTED)
async def upload_product_tsv(
    tenant_id: TenantId,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    """Validate, stage, and asynchronously import tenant product TSV rows."""
    if not file.filename or not file.filename.lower().endswith(".tsv"):
        await file.close()
        raise HTTPException(status_code=415, detail="Upload a .tsv file")

    content = await file.read(MAX_PRODUCT_TSV_BYTES + 1)
    await file.close()
    if len(content) > MAX_PRODUCT_TSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"TSV uploads are limited to {MAX_PRODUCT_TSV_BYTES} bytes",
        )
    try:
        records = parse_product_tsv(content)
        for domain in {record.domain for record in records}:
            validate_domain_value(db, domain)
    except ProductTsvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tenant_uuid = uuid.UUID(tenant_id)
    try:
        staged = stage_product_import(tenant_uuid, records)
    except ProductImportStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        run_id = dagster_client.product_content_import.import_products(
            tenant_id=staged.tenant_id,
            bucket=staged.bucket,
            object_key=staged.object_key,
        )
    except DagsterJobTriggerError as exc:
        try:
            delete_staged_import(staged.bucket, staged.object_key)
        except Exception:
            logger.exception(
                "Failed to remove unsubmitted product import object (tenant_id=%s)",
                staged.tenant_id,
            )
        raise HTTPException(
            status_code=503,
            detail=f"Could not submit product import to Dagster: {exc}",
        ) from exc

    return {
        "run_id": run_id,
        "status": "submitted",
        "products_submitted": staged.row_count,
    }


@router.get("/", response_model=list[ContentItemRead])
def list_content_items(
    tenant_context: TenantId,
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    item_type: Optional[str] = Query(default=None, pattern="^(news|video|product|article)$"),
    status_code: Optional[int] = Query(default=None, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    content_only: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    scoped_tenant_id = _tenant_uuid_or_forbidden(tenant_context, tenant_id)
    repo = ContentRepository(db)
    return repo.list_items(
        skip=skip,
        limit=limit,
        tenant_id=scoped_tenant_id,
        domain=domain,
        item_type=item_type,
        status_code=status_code,
        q=q,
        content_only=content_only,
    )


@router.get("/products", response_model=list[ProductItemRead])
def list_product_items(
    tenant_context: TenantId,
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List product metadata joined to its tenant-owned content item."""
    scoped_tenant_id = _tenant_uuid_or_forbidden(tenant_context, tenant_id)
    return ProductItemRepository(db).list_products(
        tenant_id=scoped_tenant_id,
        domain=domain,
        q=q,
        skip=skip,
        limit=limit,
    )


@router.post("/products", response_model=ProductItemRead, status_code=status.HTTP_201_CREATED)
def create_product_item(
    payload: ProductItemCreate,
    tenant_context: TenantId,
    db: Session = Depends(get_db),
) -> dict:
    """Create a product and its linked personalized content item atomically."""
    try:
        validate_domain_value(db, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = ProductItemRepository(db).create_product(
            tenant_id=uuid.UUID(tenant_context),
            payload=payload,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A product with this source and product identity already exists",
        ) from exc
    invalidate_prefix("content_items")
    return result


@router.get("/recommended", response_model=list[RecommendedContentItem])
def get_recommended_content_items(
    tenant_context: TenantId,
    master_profile_id: uuid.UUID,
    segment_id: uuid.UUID | None = None,
    item_type: Optional[str] = Query(default=None, pattern="^(news|video|product|article)$"),
    limit: int = Query(default=8, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Return persisted rankings, merged across segments by default."""
    repo = ContentRepository(db)
    try:
        items = repo.get_recommended_items(
            tenant_id=uuid.UUID(tenant_context),
            master_profile_id=master_profile_id,
            segment_id=segment_id,
            item_type=item_type,
            limit=limit,
        )
        return items
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/count")
@cache_response("content_items/count", ttl=60)
def count_content_items(
    tenant_context: TenantId,
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    item_type: Optional[str] = Query(default=None, pattern="^(news|video|product|article)$"),
    db: Session = Depends(get_db),
):
    scoped_tenant_id = _tenant_uuid_or_forbidden(tenant_context, tenant_id)
    repo = ContentRepository(db)
    return {
        "count": repo.count_items(
            tenant_id=scoped_tenant_id,
            domain=domain,
            item_type=item_type,
        )
    }


@router.get("/{content_item_id}", response_model=ContentItemRead)
@cache_response("content_items/item", ttl=60)
def get_content_item(
    content_item_id: uuid.UUID,
    tenant_context: TenantId,
    db: Session = Depends(get_db),
):
    repo = ContentRepository(db)
    obj = repo.get_item(content_item_id, uuid.UUID(tenant_context))
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpContentItem '{content_item_id}' not found")
    return obj


@router.post("/", response_model=ContentItemRead, status_code=201)
def create_content_item(
    payload: ContentItemCreate,
    tenant_context: TenantId,
    db: Session = Depends(get_db),
):
    scoped_tenant_id = uuid.UUID(tenant_context)
    if payload.tenant_id != scoped_tenant_id:
        raise HTTPException(status_code=403, detail="Payload tenant does not match tenant context")
    try:
        validate_domain_value(db, payload.domain, allow_all=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = ContentRepository(db)
    obj = repo.create_item(payload)
    invalidate_prefix("content_items")
    return obj


@router.patch("/{content_item_id}", response_model=ContentItemRead)
def update_content_item(
    content_item_id: uuid.UUID,
    payload: ContentItemUpdate,
    tenant_context: TenantId,
    db: Session = Depends(get_db),
):
    obj_in = payload.model_dump(exclude_unset=True)
    if "domain" in obj_in:
        try:
            validate_domain_value(db, obj_in.get("domain"), allow_all=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = ContentRepository(db)
    obj = repo.update_item(content_item_id, payload, uuid.UUID(tenant_context))
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpContentItem '{content_item_id}' not found")
    invalidate_prefix("content_items")
    return obj


@router.delete("/{content_item_id}", status_code=204)
def delete_content_item(
    content_item_id: uuid.UUID,
    tenant_context: TenantId,
    db: Session = Depends(get_db),
):
    repo = ContentRepository(db)
    if not repo.delete_item(content_item_id, uuid.UUID(tenant_context)):
        raise HTTPException(status_code=404, detail=f"CdpContentItem '{content_item_id}' not found")
    invalidate_prefix("content_items")


all_content_routers = [router]
