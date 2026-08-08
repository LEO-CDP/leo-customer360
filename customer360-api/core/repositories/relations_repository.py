"""Relations repository: relation types, profile-to-profile relations,
customer contacts (interactions), and transactions.

Encapsulates CRUD access for the entities that sit "around" a resolved
master profile (see core/models/relations.py). RelationType is a global
lookup dictionary (no tenant_id column); the other three are tenant-scoped.
"""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from core.crud.base import CRUDBase
from core.models.relations import CdpRelation, CustomerContact, RelationType, Transaction


class RelationsRepository:
    """Repository for relation-type, relation, contact, and transaction CRUD."""

    def __init__(self, session: Session):
        self.session = session
        self._relation_type_crud = CRUDBase(RelationType)
        self._relation_crud = CRUDBase(CdpRelation)
        self._customer_contact_crud = CRUDBase(CustomerContact)
        self._transaction_crud = CRUDBase(Transaction)

    # --- Relation Types (global lookup, no tenant_id) ---

    def list_relation_types(self, skip: int = 0, limit: int = 100) -> list[RelationType]:
        return self._relation_type_crud.list(self.session, skip=skip, limit=limit)

    def count_relation_types(self) -> int:
        return self._relation_type_crud.count(self.session)

    def get_relation_type(self, relation_type_id: int) -> Optional[RelationType]:
        return self._relation_type_crud.get(self.session, relation_type_id)

    def create_relation_type(self, payload: dict) -> RelationType:
        return self._relation_type_crud.create(self.session, payload)

    def update_relation_type(self, relation_type_id: int, updates: dict) -> RelationType:
        obj = self.get_relation_type(relation_type_id)
        if obj is None:
            raise ValueError(f"RelationType '{relation_type_id}' not found")
        return self._relation_type_crud.update(self.session, obj, updates)

    def delete_relation_type(self, relation_type_id: int) -> None:
        obj = self.get_relation_type(relation_type_id)
        if obj is None:
            raise ValueError(f"RelationType '{relation_type_id}' not found")
        self._relation_type_crud.delete(self.session, obj)

    # --- CdpRelation (profile-to-profile relationship graph) ---

    def list_relations(
        self, tenant_id: Optional[uuid.UUID] = None, skip: int = 0, limit: int = 100
    ) -> list[CdpRelation]:
        return self._relation_crud.list(self.session, skip=skip, limit=limit, tenant_id=tenant_id)

    def count_relations(self, tenant_id: Optional[uuid.UUID] = None) -> int:
        return self._relation_crud.count(self.session, tenant_id=tenant_id)

    def get_relation(self, relation_id: uuid.UUID) -> Optional[CdpRelation]:
        return self._relation_crud.get(self.session, relation_id)

    def create_relation(self, payload: dict) -> CdpRelation:
        return self._relation_crud.create(self.session, payload)

    def update_relation(self, relation_id: uuid.UUID, updates: dict) -> CdpRelation:
        obj = self.get_relation(relation_id)
        if obj is None:
            raise ValueError(f"CdpRelation '{relation_id}' not found")
        return self._relation_crud.update(self.session, obj, updates)

    def delete_relation(self, relation_id: uuid.UUID) -> None:
        obj = self.get_relation(relation_id)
        if obj is None:
            raise ValueError(f"CdpRelation '{relation_id}' not found")
        self._relation_crud.delete(self.session, obj)

    # --- CustomerContact (logged interactions/touchpoints) ---

    def list_customer_contacts(
        self, tenant_id: Optional[uuid.UUID] = None, skip: int = 0, limit: int = 100
    ) -> list[CustomerContact]:
        return self._customer_contact_crud.list(self.session, skip=skip, limit=limit, tenant_id=tenant_id)

    def count_customer_contacts(self, tenant_id: Optional[uuid.UUID] = None) -> int:
        return self._customer_contact_crud.count(self.session, tenant_id=tenant_id)

    def get_customer_contact(self, contact_id: uuid.UUID) -> Optional[CustomerContact]:
        return self._customer_contact_crud.get(self.session, contact_id)

    def create_customer_contact(self, payload: dict) -> CustomerContact:
        return self._customer_contact_crud.create(self.session, payload)

    def update_customer_contact(self, contact_id: uuid.UUID, updates: dict) -> CustomerContact:
        obj = self.get_customer_contact(contact_id)
        if obj is None:
            raise ValueError(f"CustomerContact '{contact_id}' not found")
        return self._customer_contact_crud.update(self.session, obj, updates)

    def delete_customer_contact(self, contact_id: uuid.UUID) -> None:
        obj = self.get_customer_contact(contact_id)
        if obj is None:
            raise ValueError(f"CustomerContact '{contact_id}' not found")
        self._customer_contact_crud.delete(self.session, obj)

    # --- Transaction ---

    def list_transactions(
        self, tenant_id: Optional[uuid.UUID] = None, skip: int = 0, limit: int = 100
    ) -> list[Transaction]:
        return self._transaction_crud.list(self.session, skip=skip, limit=limit, tenant_id=tenant_id)

    def count_transactions(self, tenant_id: Optional[uuid.UUID] = None) -> int:
        return self._transaction_crud.count(self.session, tenant_id=tenant_id)

    def get_transaction(self, transaction_id: uuid.UUID) -> Optional[Transaction]:
        return self._transaction_crud.get(self.session, transaction_id)

    def create_transaction(self, payload: dict) -> Transaction:
        return self._transaction_crud.create(self.session, payload)

    def update_transaction(self, transaction_id: uuid.UUID, updates: dict) -> Transaction:
        obj = self.get_transaction(transaction_id)
        if obj is None:
            raise ValueError(f"Transaction '{transaction_id}' not found")
        return self._transaction_crud.update(self.session, obj, updates)

    def delete_transaction(self, transaction_id: uuid.UUID) -> None:
        obj = self.get_transaction(transaction_id)
        if obj is None:
            raise ValueError(f"Transaction '{transaction_id}' not found")
        self._transaction_crud.delete(self.session, obj)
