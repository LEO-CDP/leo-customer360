"""Routers for RelationType, CdpRelation, CustomerContact, Transaction --
supporting entities around a resolved master profile (interactions,
transactions, and the profile-to-profile relationship graph).
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from core.config import settings
from core.database import get_db
from core.repositories.relations_repository import RelationsRepository
from core.schemas.relations import (
    CdpRelationCreate,
    CdpRelationRead,
    CdpRelationUpdate,
    CustomerContactCreate,
    CustomerContactRead,
    CustomerContactUpdate,
    RelationTypeCreate,
    RelationTypeRead,
    RelationTypeUpdate,
    TransactionCreate,
    TransactionRead,
    TransactionUpdate,
)

# --- Relation Types (global lookup dictionary, no tenant_id column) ---
relation_types_router = APIRouter(prefix="/relation-types", tags=["Relations"])


@relation_types_router.get("/", response_model=list[RelationTypeRead])
@cache_response("cdp_relation_types/list", ttl=settings.cache_ttl_seconds)
def list_relation_types(
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return RelationsRepository(db).list_relation_types(skip=skip, limit=limit)


@relation_types_router.get("/count")
@cache_response("cdp_relation_types/count", ttl=settings.cache_ttl_seconds)
def count_relation_types(db: Session = Depends(get_db)):
    return {"count": RelationsRepository(db).count_relation_types()}


@relation_types_router.get("/{relation_type_id}", response_model=RelationTypeRead)
@cache_response("cdp_relation_types/item", ttl=settings.cache_ttl_seconds)
def get_relation_type(relation_type_id: int, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).get_relation_type(relation_type_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"RelationType '{relation_type_id}' not found")
    return obj


@relation_types_router.post("/", response_model=RelationTypeRead, status_code=201)
def create_relation_type(payload: RelationTypeCreate, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).create_relation_type(payload.model_dump())
    invalidate_prefix("cdp_relation_types")
    return obj


@relation_types_router.patch("/{relation_type_id}", response_model=RelationTypeRead)
def update_relation_type(relation_type_id: int, payload: RelationTypeUpdate, db: Session = Depends(get_db)):
    repo = RelationsRepository(db)
    try:
        obj = repo.update_relation_type(relation_type_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("cdp_relation_types")
    return obj


@relation_types_router.delete("/{relation_type_id}", status_code=204)
def delete_relation_type(relation_type_id: int, db: Session = Depends(get_db)):
    try:
        RelationsRepository(db).delete_relation_type(relation_type_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("cdp_relation_types")


# --- CdpRelation (profile-to-profile relationship graph) ---
cdp_relations_router = APIRouter(prefix="/relations", tags=["Relations"])


@cdp_relations_router.get("/", response_model=list[CdpRelationRead])
@cache_response("cdp_relations/list", ttl=settings.cache_ttl_seconds)
def list_relations(
    tenant_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return RelationsRepository(db).list_relations(tenant_id=tenant_id, skip=skip, limit=limit)


@cdp_relations_router.get("/count")
@cache_response("cdp_relations/count", ttl=settings.cache_ttl_seconds)
def count_relations(tenant_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db)):
    return {"count": RelationsRepository(db).count_relations(tenant_id=tenant_id)}


@cdp_relations_router.get("/{relation_id}", response_model=CdpRelationRead)
@cache_response("cdp_relations/item", ttl=settings.cache_ttl_seconds)
def get_relation(relation_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).get_relation(relation_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpRelation '{relation_id}' not found")
    return obj


@cdp_relations_router.post("/", response_model=CdpRelationRead, status_code=201)
def create_relation(payload: CdpRelationCreate, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).create_relation(payload.model_dump())
    invalidate_prefix("cdp_relations")
    return obj


@cdp_relations_router.patch("/{relation_id}", response_model=CdpRelationRead)
def update_relation(relation_id: uuid.UUID, payload: CdpRelationUpdate, db: Session = Depends(get_db)):
    repo = RelationsRepository(db)
    try:
        obj = repo.update_relation(relation_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("cdp_relations")
    return obj


@cdp_relations_router.delete("/{relation_id}", status_code=204)
def delete_relation(relation_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        RelationsRepository(db).delete_relation(relation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("cdp_relations")


# --- CustomerContact (logged interactions/touchpoints) ---
customer_contacts_router = APIRouter(prefix="/customer-contacts", tags=["Customer Interactions"])


@customer_contacts_router.get("/", response_model=list[CustomerContactRead])
@cache_response("crm_customer_contacts/list", ttl=settings.cache_ttl_seconds)
def list_customer_contacts(
    tenant_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return RelationsRepository(db).list_customer_contacts(tenant_id=tenant_id, skip=skip, limit=limit)


@customer_contacts_router.get("/count")
@cache_response("crm_customer_contacts/count", ttl=settings.cache_ttl_seconds)
def count_customer_contacts(tenant_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db)):
    return {"count": RelationsRepository(db).count_customer_contacts(tenant_id=tenant_id)}


@customer_contacts_router.get("/{contact_id}", response_model=CustomerContactRead)
@cache_response("crm_customer_contacts/item", ttl=settings.cache_ttl_seconds)
def get_customer_contact(contact_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).get_customer_contact(contact_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CustomerContact '{contact_id}' not found")
    return obj


@customer_contacts_router.post("/", response_model=CustomerContactRead, status_code=201)
def create_customer_contact(payload: CustomerContactCreate, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).create_customer_contact(payload.model_dump())
    invalidate_prefix("crm_customer_contacts")
    return obj


@customer_contacts_router.patch("/{contact_id}", response_model=CustomerContactRead)
def update_customer_contact(contact_id: uuid.UUID, payload: CustomerContactUpdate, db: Session = Depends(get_db)):
    repo = RelationsRepository(db)
    try:
        obj = repo.update_customer_contact(contact_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("crm_customer_contacts")
    return obj


@customer_contacts_router.delete("/{contact_id}", status_code=204)
def delete_customer_contact(contact_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        RelationsRepository(db).delete_customer_contact(contact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("crm_customer_contacts")


# --- Transaction ---
transactions_router = APIRouter(prefix="/transactions", tags=["Customer Interactions"])


@transactions_router.get("/", response_model=list[TransactionRead])
@cache_response("crm_transactions/list", ttl=settings.cache_ttl_seconds)
def list_transactions(
    tenant_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    db: Session = Depends(get_db),
):
    return RelationsRepository(db).list_transactions(tenant_id=tenant_id, skip=skip, limit=limit)


@transactions_router.get("/count")
@cache_response("crm_transactions/count", ttl=settings.cache_ttl_seconds)
def count_transactions(tenant_id: Optional[uuid.UUID] = None, db: Session = Depends(get_db)):
    return {"count": RelationsRepository(db).count_transactions(tenant_id=tenant_id)}


@transactions_router.get("/{transaction_id}", response_model=TransactionRead)
@cache_response("crm_transactions/item", ttl=settings.cache_ttl_seconds)
def get_transaction(transaction_id: uuid.UUID, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).get_transaction(transaction_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Transaction '{transaction_id}' not found")
    return obj


@transactions_router.post("/", response_model=TransactionRead, status_code=201)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    obj = RelationsRepository(db).create_transaction(payload.model_dump())
    invalidate_prefix("crm_transactions")
    return obj


@transactions_router.patch("/{transaction_id}", response_model=TransactionRead)
def update_transaction(transaction_id: uuid.UUID, payload: TransactionUpdate, db: Session = Depends(get_db)):
    repo = RelationsRepository(db)
    try:
        obj = repo.update_transaction(transaction_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("crm_transactions")
    return obj


@transactions_router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        RelationsRepository(db).delete_transaction(transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_prefix("crm_transactions")


all_relations_routers = [
    relation_types_router,
    cdp_relations_router,
    customer_contacts_router,
    transactions_router,
]
