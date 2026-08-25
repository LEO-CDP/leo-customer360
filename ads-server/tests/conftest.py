"""
Pytest configuration and shared fixtures for ads-server tests.

Model tests (test_models.py) never touch a database. Repository and API
tests need a real PostgreSQL instance (JSONB / identity columns are not
emulated by SQLite); when one isn't reachable, they fall back to a minimal
in-memory SQLite engine that only supports the plain model tests.
"""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from tests._pg import check_postgres_available


def _pg_url() -> str:
    import os

    return (
        f"postgresql+psycopg2://{os.getenv('LEO_AD_DB_USER', 'postgres')}:"
        f"{os.getenv('LEO_AD_DB_PASSWORD', 'postgres')}@"
        f"{os.getenv('LEO_AD_DB_HOST', 'localhost')}:"
        f"{os.getenv('LEO_AD_DB_PORT', '5432')}/"
        f"{os.getenv('LEO_AD_DB_NAME', 'customer360')}"
    )


@pytest.fixture(scope="session")
def _pg_engine():
    """Session-scoped engine to the real database, or None if unreachable."""
    if not check_postgres_available():
        return None
    return create_engine(_pg_url(), echo=False)


@pytest.fixture(scope="function")
def test_engine(_pg_engine):
    """
    Provide a database handle for repository/API tests.

    - PostgreSQL available: yields a Connection wrapped in an outer
      transaction that is rolled back on teardown. Sessions created from
      this connection automatically nest into SAVEPOINTs, so nothing a
      test inserts is ever persisted to the real database.
    - PostgreSQL unavailable: an in-memory SQLite engine (model tests only;
      repository/API tests are skipped in this case, see pytestmark below).
    """
    if _pg_engine is not None:
        connection = _pg_engine.connect()
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()
            connection.close()
        return

    from model.ad import Ad
    from model.placement import Placement
    from model.tenant import Tenant

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = OFF"))
        conn.commit()

    try:
        Tenant.__table__.create(engine, checkfirst=True)
        Placement.__table__.create(engine, checkfirst=True)
        Ad.__table__.create(engine, checkfirst=True)
    except Exception:
        pass  # SQLite is too limited; dependent tests are skipped anyway

    yield engine


@pytest.fixture
def seed(test_engine):
    """
    Insert the minimal valid dependency chain (tenant + creative) that Ad
    rows require via foreign key, and return the generated ids.

    Only usable against PostgreSQL (see test_engine); repository/API tests
    that need it are skipped when PostgreSQL is unavailable.
    """
    from sqlalchemy.orm import Session

    from model.creative import Creative
    from model.tenant import Tenant

    session = Session(bind=test_engine, expire_on_commit=False)

    tenant = Tenant(tenant_key=f"test-{uuid4().hex}", name="Test Tenant")
    session.add(tenant)
    session.flush()

    creative = Creative(
        tenant_id=tenant.tenant_id,
        creative_key=f"test-creative-{uuid4().hex}",
        ad_type="display",
        format_code="300x250",
    )
    session.add(creative)
    session.commit()

    ids = {"tenant_id": tenant.tenant_id, "creative_id": creative.creative_id}
    session.close()
    return ids



