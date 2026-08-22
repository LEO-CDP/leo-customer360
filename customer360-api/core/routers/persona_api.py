
"""Routers for customer personas, features, scores, and history.

Customer personas are "identity understanding" computed by the backend-system/
identity_resolution PersonaResolutionEngine, representing resolved understanding
of who a customer is based on their aggregated raw and domain profiles.

Persona features provide explainability (input signals to persona computation).
Persona score details provide score breakdown.
Persona history is an append-only audit trail of material persona changes.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.auth import require_admin
from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.crud.base import CRUDBase
from core.database import get_db
from core.models.identity import (
    CdpCustomerPersona,
    CdpPersonaArchetype,
    CdpPersonaFeature,
    CdpPersonaHistory,
    CdpPersonaScoreDetail,
)
from core.repositories.persona_repository import PersonaRepository
from core.schemas.identity import (
    CustomerPersonaCreate,
    CustomerPersonaRead,
    CustomerPersonaUpdate,
    MasterProfileListResponse,
    MasterProfileRead,
    PersonaAnalyticsSummary,
    PersonaArchetypeCreate,
    PersonaArchetypeRead,
    PersonaArchetypeUpdate,
    PersonaFeatureCreate,
    PersonaFeatureRead,
    PersonaHistoryCreate,
    PersonaHistoryRead,
    PersonaScoreDetailCreate,
    PersonaScoreDetailRead,
)
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client
from core.utils.domains import validate_domain_value

logger = logging.getLogger(__name__)

# --- Exposed for test mocking (backward compatibility) ---
_archetype_crud = CRUDBase(CdpPersonaArchetype)
_persona_crud = CRUDBase(CdpCustomerPersona)
_persona_feature_crud = CRUDBase(CdpPersonaFeature)
_persona_score_detail_crud = CRUDBase(CdpPersonaScoreDetail)
_persona_history_crud = CRUDBase(CdpPersonaHistory)

# --- Persona Archetypes ---
# Shared, reusable persona definitions. This is what the Persona Management
# admin UI must list (each row carrying its own matched_profile_count) --
# NOT raw per-profile cdp_customer_personas match rows. Mounted BEFORE
# customer_personas_router's "/{persona_id}" catch-all so "/persona/archetypes"
# never gets swallowed by it.
persona_archetypes_router = APIRouter(prefix="/persona/archetypes", tags=["Identity Resolution - Customer Personas"])


@persona_archetypes_router.get("", response_model=list[PersonaArchetypeRead])
@cache_response("persona_archetypes/list", ttl=settings.cache_ttl_seconds)
def list_persona_archetypes(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = PersonaRepository(db)
    return repo.list_archetypes(tenant_id=tenant_id, domain=domain, is_active=is_active, skip=skip, limit=limit)


@persona_archetypes_router.get("/{persona_archetype_id}", response_model=PersonaArchetypeRead)
@cache_response("persona_archetypes/item", ttl=settings.cache_ttl_seconds)
def get_persona_archetype(persona_archetype_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_archetype(persona_archetype_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaArchetype '{persona_archetype_id}' not found")
    return obj


@persona_archetypes_router.get("/{persona_archetype_id}/master-profiles", response_model=MasterProfileListResponse)
@cache_response("persona_archetypes/master_profiles", ttl=settings.cache_ttl_seconds)
def list_master_profiles_for_persona_archetype(
    persona_archetype_id: uuid.UUID,
    tenant_id: Optional[uuid.UUID] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.api_default_page_size, ge=1, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    repo = PersonaRepository(db)
    if repo.get_archetype(persona_archetype_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaArchetype '{persona_archetype_id}' not found")
    return repo.list_master_profiles_by_archetype(
        persona_archetype_id=persona_archetype_id, tenant_id=tenant_id, page=page, page_size=page_size
    )


def _trigger_persona_centroid_recompute(obj, trigger_reason: str) -> Optional[str]:
    """Best-effort submit of ``identity_resolution_job`` to refresh this
    archetype's ``matched_profile_count`` after an admin create/edit. Never
    raises -- a Dagster webserver outage must not block archetype metadata
    CRUD, which is a synchronous DB write independent of the async recompute.

    The helper and response field retain their ``centroid_*`` names for API
    compatibility with the earlier recompute trigger response."""
    try:
        return dagster_client.identity_resolution.recompute_personas(
            trigger_reason=trigger_reason,
            tenant_id=str(obj.tenant_id),
            persona_archetype_id=str(obj.persona_archetype_id),
        )
    except DagsterJobTriggerError:
        logger.warning(
            "Could not submit identity_resolution_job to refresh matched_profile_count for "
            "persona_archetype_id=%s (trigger_reason=%s) -- the count will stay stale until the next "
            "scheduled recompute.",
            obj.persona_archetype_id,
            trigger_reason,
        )
        return None


@persona_archetypes_router.post("", response_model=PersonaArchetypeRead, status_code=201, dependencies=[Depends(require_admin)])
def create_persona_archetype(payload: PersonaArchetypeCreate, db: Session = Depends(get_db)):
    try:
        validate_domain_value(db, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = PersonaRepository(db)
    obj = repo.create_archetype(payload.model_dump())
    invalidate_prefix("persona_archetypes")
    run_id = _trigger_persona_centroid_recompute(obj, trigger_reason="persona_archetype_created")
    setattr(obj, "centroid_recompute_run_id", run_id)
    return obj


@persona_archetypes_router.patch("/{persona_archetype_id}", response_model=PersonaArchetypeRead, dependencies=[Depends(require_admin)])
def update_persona_archetype(persona_archetype_id: uuid.UUID, payload: PersonaArchetypeUpdate, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_archetype(persona_archetype_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaArchetype '{persona_archetype_id}' not found")
    obj = repo.update_archetype(persona_archetype_id, payload.model_dump(exclude_unset=True))
    invalidate_prefix("persona_archetypes")
    run_id = _trigger_persona_centroid_recompute(obj, trigger_reason="persona_archetype_updated")
    setattr(obj, "centroid_recompute_run_id", run_id)
    return obj


@persona_archetypes_router.delete("/{persona_archetype_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_persona_archetype(persona_archetype_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_archetype(persona_archetype_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaArchetype '{persona_archetype_id}' not found")
    repo.delete_archetype(persona_archetype_id)
    invalidate_prefix("persona_archetypes")

# --- Customer Personas ---
# Primary CRUD + analytics for resolved personas. Mounted at "/persona" so
# every route below (and every other router in this module) resolves under
# .../api/v1/persona/*, independent of how app.py wires the top-level prefix.
customer_personas_router = APIRouter(prefix="/persona", tags=["Identity Resolution - Customer Personas"])


@customer_personas_router.get("/list", response_model=list[CustomerPersonaRead])
@cache_response("customer_personas/list", ttl=settings.cache_ttl_seconds)
def list_customer_personas(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    master_profile_id: Optional[uuid.UUID] = None,
    persona_archetype_id: Optional[uuid.UUID] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = PersonaRepository(db)
    return repo.list_personas(
        tenant_id=tenant_id,
        domain=domain,
        master_profile_id=master_profile_id,
        persona_archetype_id=persona_archetype_id,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )


@customer_personas_router.get("/analytics/summary", response_model=PersonaAnalyticsSummary)
@cache_response("customer_personas/analytics_summary", ttl=settings.cache_ttl_seconds)
def get_customer_persona_analytics_summary(
    tenant_id: Optional[uuid.UUID] = None,
    domain: Optional[str] = Query(default=None),
    is_active: Optional[bool] = None,
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    try:
        validate_domain_value(db, domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = PersonaRepository(db)
    return repo.get_analytics_summary(
        tenant_id=tenant_id,
        domain=domain,
        is_active=is_active,
        days=days,
    )


@customer_personas_router.get("/category/{persona_category}/master-profiles", response_model=MasterProfileListResponse)
@cache_response("customer_personas/master_profiles_by_category", ttl=settings.cache_ttl_seconds)
def list_master_profiles_by_persona_category(
    persona_category: str,
    tenant_id: Optional[uuid.UUID] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.api_default_page_size, ge=1, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    repo = PersonaRepository(db)
    return repo.list_master_profiles_by_persona_category(
        persona_category=persona_category,
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
    )


@customer_personas_router.get("/{persona_id}", response_model=CustomerPersonaRead)
@cache_response("customer_personas/item", ttl=settings.cache_ttl_seconds)
def get_customer_persona(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_persona(persona_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    return obj


@customer_personas_router.get("/{persona_id}/features", response_model=list[PersonaFeatureRead])
@cache_response("customer_personas/features", ttl=settings.cache_ttl_seconds)
def get_customer_persona_features(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    if repo.get_persona(persona_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    return repo.list_persona_features(persona_id=persona_id, skip=0, limit=settings.api_max_page_size)


@customer_personas_router.get("/{persona_id}/score-details", response_model=list[PersonaScoreDetailRead])
@cache_response("customer_personas/score_details", ttl=settings.cache_ttl_seconds)
def get_customer_persona_score_details(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    if repo.get_persona(persona_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    return repo.list_persona_score_details(persona_id=persona_id, skip=0, limit=settings.api_max_page_size)


@customer_personas_router.get("/{persona_id}/master-profile", response_model=MasterProfileRead)
@cache_response("customer_personas/master_profile", ttl=settings.cache_ttl_seconds)
def get_customer_persona_master_profile(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    if repo.get_persona(persona_id) is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")

    master_profile = repo.get_master_profile_by_persona_id(persona_id)
    if master_profile is None:
        raise HTTPException(status_code=404, detail=f"No master profile linked to persona '{persona_id}'")
    return master_profile


@customer_personas_router.post("/", response_model=CustomerPersonaRead, status_code=201)
def create_customer_persona(payload: CustomerPersonaCreate, db: Session = Depends(get_db)):
    try:
        validate_domain_value(db, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = PersonaRepository(db)
    obj = repo.create_persona(payload.model_dump())
    invalidate_prefix("customer_personas")
    return obj


@customer_personas_router.patch("/{persona_id}", response_model=CustomerPersonaRead)
def update_customer_persona(persona_id: uuid.UUID, payload: CustomerPersonaUpdate, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_persona(persona_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    obj = repo.update_persona(persona_id, payload.model_dump(exclude_unset=True))
    invalidate_prefix("customer_personas")
    return obj


@customer_personas_router.delete("/{persona_id}", status_code=204)
def delete_customer_persona(persona_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_persona(persona_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpCustomerPersona '{persona_id}' not found")
    repo.delete_persona(persona_id)
    invalidate_prefix("customer_personas")


# --- Persona Features (explainability input signals; append-only) ------
# Nested under "/persona/features" (rather than a flat "persona-features")
# so the resource hierarchy reads as: persona -> features.
persona_features_router = APIRouter(prefix="/persona/features", tags=["Identity Resolution - Customer Personas"])


@persona_features_router.get("/", response_model=list[PersonaFeatureRead])
@cache_response("persona_features/list", ttl=settings.cache_ttl_seconds)
def list_persona_features(
    persona_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    repo = PersonaRepository(db)
    return repo.list_persona_features(persona_id=persona_id, skip=skip, limit=limit)


@persona_features_router.get("/{feature_id}", response_model=PersonaFeatureRead)
@cache_response("persona_features/item", ttl=settings.cache_ttl_seconds)
def get_persona_feature(feature_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_persona_feature(feature_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaFeature '{feature_id}' not found")
    return obj


@persona_features_router.post("/", response_model=PersonaFeatureRead, status_code=201)
def create_persona_feature(payload: PersonaFeatureCreate, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.create_persona_feature(payload.model_dump())
    invalidate_prefix("persona_features")
    return obj


# --- Persona Score Details (explainability score breakdown; append-only) ---
# Nested under "/persona/score-details": persona -> score-details.
persona_score_details_router = APIRouter(
    prefix="/persona/score-details", tags=["Identity Resolution - Customer Personas"]
)


@persona_score_details_router.get("/", response_model=list[PersonaScoreDetailRead])
@cache_response("persona_score_details/list", ttl=settings.cache_ttl_seconds)
def list_persona_score_details(
    persona_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    repo = PersonaRepository(db)
    return repo.list_persona_score_details(persona_id=persona_id, skip=skip, limit=limit)


@persona_score_details_router.get("/{score_id}", response_model=PersonaScoreDetailRead)
@cache_response("persona_score_details/item", ttl=settings.cache_ttl_seconds)
def get_persona_score_detail(score_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_persona_score_detail(score_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaScoreDetail '{score_id}' not found")
    return obj


@persona_score_details_router.post("/", response_model=PersonaScoreDetailRead, status_code=201)
def create_persona_score_detail(payload: PersonaScoreDetailCreate, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.create_persona_score_detail(payload.model_dump())
    invalidate_prefix("persona_score_details")
    return obj


# --- Persona History (audit trail of material persona changes; append-only) ---
# Nested under "/persona/history": persona -> history.
persona_history_router = APIRouter(prefix="/persona/history", tags=["Identity Resolution - Customer Personas"])


@persona_history_router.get("/", response_model=list[PersonaHistoryRead])
@cache_response("persona_history/list", ttl=settings.cache_ttl_seconds)
def list_persona_history(
    persona_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    repo = PersonaRepository(db)
    return repo.list_persona_history(persona_id=persona_id, skip=skip, limit=limit)


@persona_history_router.get("/{history_id}", response_model=PersonaHistoryRead)
@cache_response("persona_history/item", ttl=settings.cache_ttl_seconds)
def get_persona_history_entry(history_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.get_persona_history(history_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpPersonaHistory '{history_id}' not found")
    return obj


@persona_history_router.post("/", response_model=PersonaHistoryRead, status_code=201)
def create_persona_history_entry(payload: PersonaHistoryCreate, db: Session = Depends(get_db)):
    repo = PersonaRepository(db)
    obj = repo.create_persona_history(payload.model_dump())
    invalidate_prefix("persona_history")
    return obj


# Export all routers. Each router already carries its own "/persona..."
# prefix above, so app.py only needs to add the shared "/api/v1" root -
# every URL below therefore begins with /api/v1/persona.
all_persona_routers = [
    persona_archetypes_router,
    customer_personas_router,
    persona_features_router,
    persona_score_details_router,
    persona_history_router,
]