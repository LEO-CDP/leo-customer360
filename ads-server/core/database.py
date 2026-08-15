"""
Database infrastructure.

The database layer has no knowledge about FastAPI routes or business logic.

PostgreSQL schema:

    leo_ads
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import db_ad_engine


class Database:
    """
    PostgreSQL database facade.

    This class makes database access replaceable later.

    Possible future implementation:

        Database
          ├── PostgreSQL
          ├── Read replica
          └── transaction manager
    """

    def __init__(
        self,
        engine: Engine,
    ) -> None:
        self.engine = engine

        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Create a transactional SQLAlchemy session.
        """

        session = self.session_factory()

        try:
            yield session
            session.commit()

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def health_check(self) -> bool:
        """
        Lightweight database connectivity check.
        """

        from sqlalchemy import text

        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return True

    def dispose(self) -> None:
        """
        Dispose the connection pool.
        """

        self.engine.dispose()


database = Database(
    engine=db_ad_engine,
)