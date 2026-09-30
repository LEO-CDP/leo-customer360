"""Campaign A/B experiment endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.database import get_db
from core.repositories.campaign_draft_repository import CampaignDraftNotFoundError
from core.repositories.campaign_experiment_repository import (
    CampaignExperimentConflictError,
    CampaignExperimentError,
    CampaignExperimentNotFoundError,
    CampaignExperimentRepository,
)
from leo_customer360_dao.schemas.crm import (
    CampaignExperimentCreate,
    CampaignExperimentPerformanceRead,
    CampaignExperimentRead,
    CampaignExperimentUpdate,
)

router = APIRouter(prefix="/campaigns", tags=["Campaign Experiments"])
experiment_router = APIRouter(prefix="/campaign-experiments", tags=["Campaign Experiments"])


def _current_user_id(request: Request) -> Optional[uuid.UUID]:
    user_id = getattr(request.state, "user_id", None)
    return uuid.UUID(str(user_id)) if user_id else None


def _tenant_id(request: Request) -> uuid.UUID:
    return uuid.UUID(require_tenant(request))


@router.get("/{campaign_id}/experiments", response_model=list[CampaignExperimentRead])
def list_campaign_experiments(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    try:
        return CampaignExperimentRepository(db).list_for_campaign(_tenant_id(request), campaign_id)
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{campaign_id}/experiments", response_model=CampaignExperimentRead, status_code=201)
def create_campaign_experiment(
    campaign_id: uuid.UUID,
    payload: CampaignExperimentCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return CampaignExperimentRepository(db).create(
            _tenant_id(request), campaign_id, _current_user_id(request), payload.model_dump()
        )
    except CampaignDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignExperimentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@experiment_router.get("/{experiment_id}", response_model=CampaignExperimentRead)
def get_campaign_experiment(experiment_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    try:
        repository = CampaignExperimentRepository(db)
        experiment = repository.get_experiment(_tenant_id(request), experiment_id)
        return repository.read_experiment(experiment)
    except CampaignExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@experiment_router.patch("/{experiment_id}", response_model=CampaignExperimentRead)
def update_campaign_experiment(
    experiment_id: uuid.UUID,
    payload: CampaignExperimentUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return CampaignExperimentRepository(db).update(
            _tenant_id(request), experiment_id, _current_user_id(request), payload.model_dump(exclude_unset=True)
        )
    except CampaignExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CampaignExperimentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CampaignExperimentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@experiment_router.get("/{experiment_id}/performance", response_model=list[CampaignExperimentPerformanceRead])
def get_campaign_experiment_performance(
    experiment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return CampaignExperimentRepository(db).performance(_tenant_id(request), experiment_id)
    except CampaignExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


all_campaign_experiment_routers = [router, experiment_router]
