"""FastAPI adapter around the framework-neutral DAO session factory."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from leo_customer360_dao.database import (
    SessionLocal,
    _set_transaction_context,
    engine,
)


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a request-scoped DAO session with the resolved request identity."""
    db = SessionLocal()
    try:
        tenant_id = getattr(request.state, "tenant_id", None)
        user_id = getattr(request.state, "user_id", None)
        tenant_value = str(tenant_id).strip() if tenant_id is not None else ""
        if hasattr(db, "info"):
            db.info["tenant_id"] = tenant_value or None
            db.info["user_id"] = str(user_id) if user_id is not None else None
        if tenant_value:
            db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_value})
        if user_id is not None:
            db.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(user_id)})
        yield db
    finally:
        db.close()


__all__ = ["SessionLocal", "_set_transaction_context", "engine", "get_db"]