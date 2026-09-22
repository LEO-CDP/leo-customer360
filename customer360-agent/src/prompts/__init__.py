"""Prompt store -- prompts as addressable, versioned data in Postgres. Bodies +
seed live in customer360-database; this package reads/edits them.

    from prompts import get_store
    get_store().get("campaign.plan.instructions").render({...})

The port is in ``port``, the Postgres implementation in ``stores``.
"""

from __future__ import annotations

from prompts.port import (
    NONE,
    PromptNotFound,
    PromptStore,
    PromptTemplate,
    declared_vars,
)
from prompts.stores import PgPromptStore, validate

__all__ = [
    "NONE",
    "PgPromptStore",
    "PromptNotFound",
    "PromptStore",
    "PromptTemplate",
    "declared_vars",
    "get_store",
    "reset_store_cache",
    "validate",
]

_store: PromptStore | None = None


def get_store() -> PromptStore:
    """The process-wide prompt store (a single snapshot, so version pinning is
    stable within a run). Call refresh() once at startup to load the snapshot."""
    global _store
    if _store is None:
        _store = PgPromptStore()
    return _store


def reset_store_cache() -> None:
    """Drop the cached store (tests only)."""
    global _store
    _store = None
