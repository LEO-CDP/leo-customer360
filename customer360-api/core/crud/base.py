"""Generic CRUD helper, reused by every simple entity router.

Kept intentionally simple (no soft-delete, no complex filtering DSL) --
CIR-specific models (master profiles, raw profiles, reporting) that need
richer queries have their own dedicated module: core/crud/identity.py.
"""

from typing import Any, Generic, Optional, TypeVar

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from core.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class CRUDBase(Generic[ModelType]):
    """CRUD object with default methods to Create, Read, Update, Delete (CRUD)."""

    def __init__(self, model: type[ModelType]):
        self.model = model

    def get(self, db: Session, pk: Any) -> Optional[ModelType]:
        return db.get(self.model, pk)

    def _get_model_column(self, field_name: str):
        mapper = inspect(self.model)
        if field_name not in mapper.columns:
            raise ValueError(f"Invalid field '{field_name}' for {self.model.__name__}")
        return getattr(self.model, field_name)

    def _apply_filters(self, stmt: Any, filters: dict[str, Any]) -> Any:
        for field_name, value in filters.items():
            if value is None:
                continue
            stmt = stmt.where(self._get_model_column(field_name) == value)
        return stmt

    def _apply_sort(self, stmt: Any, sort_by: str) -> Any:
        normalized_sort = " ".join(sort_by.split())
        if not normalized_sort:
            return stmt

        sort_field, _, sort_direction = normalized_sort.partition(" ")
        direction = sort_direction.upper() if sort_direction else "ASC"
        if direction not in {"ASC", "DESC"}:
            raise ValueError(f"Invalid sort direction '{sort_direction}'")

        order_column = self._get_model_column(sort_field)
        return stmt.order_by(order_column.desc() if direction == "DESC" else order_column.asc())

    def _touch_updated_at(self, db_obj: ModelType) -> None:
        mapper = inspect(self.model)
        updated_at_column = mapper.columns.get("updated_at")
        if updated_at_column is None:
            return

        is_timezone_aware = bool(getattr(updated_at_column.type, "timezone", False))
        updated_at_value = func.now() if is_timezone_aware else func.timezone("UTC", func.now())
        setattr(db_obj, "updated_at", updated_at_value)

    def list(self, db: Session, *, skip: int = 0, limit: int = 100, **filters: Any) -> list[ModelType]:
        sort_by = filters.pop("sort_by", None)
        stmt = select(self.model)
        stmt = self._apply_filters(stmt, filters)
        if sort_by:
            stmt = self._apply_sort(stmt, sort_by)
        stmt = stmt.offset(skip).limit(limit)
        return list(db.execute(stmt).scalars().all())

    def count(self, db: Session, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        stmt = self._apply_filters(stmt, filters)
        return db.execute(stmt).scalar_one()

    def create(self, db: Session, obj_in: dict[str, Any]) -> ModelType:
        db_obj = self.model(**obj_in)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: ModelType, obj_in: dict[str, Any]) -> ModelType:
        for field, value in obj_in.items():
            setattr(db_obj, field, value)
        self._touch_updated_at(db_obj)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def delete(self, db: Session, db_obj: ModelType) -> None:
        db.delete(db_obj)
        db.commit()
