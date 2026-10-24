"""Database access for the ``leo_ads`` schema (the ad server's tables).

The ad server (customer360-promotions) keeps its catalogue and personalisation
tables in the ``leo_ads`` schema of the same PostgreSQL database. The admin
endpoints for ad personalisation read and write a few of those tables.

By default this reuses the DAO engine (same database and credentials). If the
API's database user cannot reach ``leo_ads`` in a deployed environment, set
``ADS_DATABASE_URL`` to a SQLAlchemy URL with a user that can; a separate,
small pool is then used (``ADS_DB_POOL_SIZE``, default 5).

``leo_ads`` has no row-level security, so there is no tenant GUC to set here:
isolation is the repository's job (it maps the caller's platform tenant to the
ad tenant and filters every query by it).
"""

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from leo_customer360_dao.database import engine as _dao_engine

_ADS_DATABASE_URL = os.getenv("ADS_DATABASE_URL", "").strip()

if _ADS_DATABASE_URL:
    engine = create_engine(
        _ADS_DATABASE_URL,
        pool_size=int(os.getenv("ADS_DB_POOL_SIZE", "5")),
        pool_pre_ping=True,
        future=True,
    )
else:
    engine = _dao_engine

AdsSessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
)


def get_ads_db() -> Generator[Session, None, None]:
    """Yield a request-scoped session on the ``leo_ads`` database."""
    db = AdsSessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = ["AdsSessionLocal", "engine", "get_ads_db"]
