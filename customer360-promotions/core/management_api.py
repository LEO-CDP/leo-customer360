"""Authenticated CRUD endpoints for promotions campaigns and creatives."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from core.management_auth import require_management_tenant
from model.campaign import Campaign
from model.creative import Creative
from model.management_schemas import (
    CampaignInput, CampaignRead, CreativeInput, CreativeRead,
    Customer360CampaignInput, Customer360CreativeInput,
)
from repository.management_repository import InvalidReference, ManagementConflict, ManagementRepository

router = APIRouter(prefix="/management", tags=["Management"])
TenantId = Annotated[int, Depends(require_management_tenant)]
ItemId = Annotated[int, Path(gt=0, le=9223372036854775807)]


def repository(request: Request) -> ManagementRepository:
    """Provide the management repository bound to the application database."""
    return ManagementRepository(request.app.state.promotions_engine)


def save_item(repo: ManagementRepository, model: type[Campaign] | type[Creative], tenant_id: int, payload: CampaignInput | CreativeInput, item_id: int | None = None) -> dict:
    """Save a record and translate expected persistence failures into API errors."""
    try:
        result = repo.save(model, tenant_id, payload, item_id)
    except InvalidReference as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ManagementConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return result


def get_item(repo: ManagementRepository, model: type[Campaign] | type[Creative], tenant_id: int, item_id: int) -> dict:
    """Read a tenant-owned record or return a non-disclosing 404."""
    result = repo.get(model, tenant_id, item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return result


def delete_item(repo: ManagementRepository, model: type[Campaign] | type[Creative], tenant_id: int, item_id: int) -> Response:
    """Delete an unreferenced tenant-owned record."""
    try:
        deleted = repo.delete(model, tenant_id, item_id)
    except ManagementConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/campaigns", response_model=list[CampaignRead])
def list_campaigns(request: Request, tenant_id: TenantId, offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(50, ge=1, le=100)) -> list[dict]:
    """List campaigns owned by the token's tenant."""
    return repository(request).list(Campaign, tenant_id, offset, limit)


@router.post("/campaigns", response_model=CampaignRead, status_code=201)
def create_campaign(payload: CampaignInput, request: Request, tenant_id: TenantId) -> dict:
    """Create a campaign for the token's tenant."""
    return save_item(repository(request), Campaign, tenant_id, payload)


@router.get("/campaigns/{campaign_id}", response_model=CampaignRead)
def get_campaign(campaign_id: ItemId, request: Request, tenant_id: TenantId) -> dict:
    """Get one tenant-owned campaign."""
    return get_item(repository(request), Campaign, tenant_id, campaign_id)


@router.put("/campaigns/{campaign_id}", response_model=CampaignRead)
def update_campaign(campaign_id: ItemId, payload: CampaignInput, request: Request, tenant_id: TenantId) -> dict:
    """Replace the editable fields of a tenant-owned campaign."""
    return save_item(repository(request), Campaign, tenant_id, payload, campaign_id)


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: ItemId, request: Request, tenant_id: TenantId) -> Response:
    """Delete an unreferenced tenant-owned campaign."""
    return delete_item(repository(request), Campaign, tenant_id, campaign_id)


@router.get("/creatives", response_model=list[CreativeRead])
def list_creatives(request: Request, tenant_id: TenantId, offset: int = Query(0, ge=0, le=9223372036854775807), limit: int = Query(50, ge=1, le=100)) -> list[dict]:
    """List creatives owned by the token's tenant."""
    return repository(request).list(Creative, tenant_id, offset, limit)


@router.post("/creatives", response_model=CreativeRead, status_code=201)
def create_creative(payload: CreativeInput, request: Request, tenant_id: TenantId) -> dict:
    """Create a creative for the token's tenant."""
    return save_item(repository(request), Creative, tenant_id, payload)


@router.get("/creatives/{creative_id}", response_model=CreativeRead)
def get_creative(creative_id: ItemId, request: Request, tenant_id: TenantId) -> dict:
    """Get one tenant-owned creative."""
    return get_item(repository(request), Creative, tenant_id, creative_id)


@router.put("/creatives/{creative_id}", response_model=CreativeRead)
def update_creative(creative_id: ItemId, payload: CreativeInput, request: Request, tenant_id: TenantId) -> dict:
    """Replace the editable fields of a tenant-owned creative."""
    return save_item(repository(request), Creative, tenant_id, payload, creative_id)


@router.delete("/creatives/{creative_id}", status_code=204)
def delete_creative(creative_id: ItemId, request: Request, tenant_id: TenantId) -> Response:
    """Delete an unreferenced tenant-owned creative."""
    return delete_item(repository(request), Creative, tenant_id, creative_id)


@router.put("/customer360/{source_tenant_id}/campaigns/{source_id}", response_model=CampaignRead)
def sync_campaign(source_tenant_id: uuid.UUID, source_id: uuid.UUID, payload: Customer360CampaignInput, request: Request, tenant_id: TenantId) -> dict:
    """Create or refresh a uniquely linked Customer 360 campaign."""
    try:
        return repository(request).sync_customer360(tenant_id, source_tenant_id, source_id, payload)
    except InvalidReference as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ManagementConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/customer360/{source_tenant_id}/content/{source_id}", response_model=CreativeRead)
def sync_creative(source_tenant_id: uuid.UUID, source_id: uuid.UUID, payload: Customer360CreativeInput, request: Request, tenant_id: TenantId) -> dict:
    """Create or refresh the unique creative for a Customer 360 content item."""
    try:
        return repository(request).sync_customer360(tenant_id, source_tenant_id, source_id, payload)
    except InvalidReference as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ManagementConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
