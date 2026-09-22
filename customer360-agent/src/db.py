"""Optional Postgres engine for the prompt store.

The agent is otherwise DB-free; a database is used ONLY when the prompt store is
backed by Postgres. `get_engine()` returns a pooled SQLAlchemy engine when
`settings.database_url` is set, else None (so the store falls back to the in-code
defaults). SQLAlchemy is imported lazily so the package imports without it."""

from __future__ import annotations

from config import settings

_engine = None
_resolved = False


def get_engine():
    """The shared sync engine, or None when no DB is configured (local / offline).

    Cached after first call. Set AGENT_DATABASE_URL / DATABASE_URL to a SQLAlchemy
    URL, e.g. ``postgresql+psycopg2://user:pass@host:5432/db``."""
    global _engine, _resolved
    if _resolved:
        return _engine
    url = (settings.database_url or "").strip()
    if url:
        from sqlalchemy import create_engine

        # The prompt-store tables live in the customer360 schema (see
        # customer360-database/database-schema.sql); set search_path so the store's
        # unqualified SQL resolves there.
        schema = (settings.db_schema or "customer360").strip()
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            future=True,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
    _resolved = True
    return _engine


def reset_engine_cache() -> None:
    """Drop the cached engine (tests only)."""
    global _engine, _resolved
    if _engine is not None:
        _engine.dispose()
    _engine, _resolved = None, False
