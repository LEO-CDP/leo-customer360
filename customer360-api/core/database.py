"""SQLAlchemy 2 engine + session factory with connection pooling.

Uses SQLAlchemy's default QueuePool for the psycopg2 driver, sized via
DB_POOL_SIZE / DB_MAX_OVERFLOW (.env), with pool_pre_ping to transparently
recover from dropped connections and pool_recycle to avoid stale connections.
"""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from core.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_pre_ping=settings.db_pool_pre_ping,
    echo=settings.db_echo_sql,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _set_transaction_context(session: Session, _transaction: object, connection: object) -> None:
    """Restore request identity GUCs whenever a SQLAlchemy transaction starts.

    The GUCs are transaction-local so pooled connections cannot leak tenant
    context. A commit ends that transaction, though, and ORM refreshes or
    later queries can immediately start another one. Reapplying the values at
    transaction start keeps RLS active across those boundaries.
    """
    tenant_id = session.info.get("tenant_id")
    if tenant_id:
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})

    user_id = session.info.get("user_id")
    if user_id:
        connection.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": user_id})


event.listen(Session, "after_begin", _set_transaction_context)


def get_db(request: Request) -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped SQLAlchemy session.

    Also sets the ``app.tenant_id`` / ``app.user_id`` Postgres session
    variables (transaction-local, via ``set_config(..., true)``) from the
    caller's identity resolved by ``core.auth.auth_middleware`` and stashed on
    ``request.state``. The ``tenant_policy`` Row-Level Security policies
    defined on every crm_*/cdp_* table (see the "ROW LEVEL SECURITY" section
    of core-customer360/database-schema.sql) key off ``app.tenant_id`` --
    without this, RLS fails closed (no rows visible) for every request, since
    ``current_setting('app.tenant_id', true)`` returns NULL until it's set.
    """
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

