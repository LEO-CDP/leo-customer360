"""Postgres-backed prompt store.

Bodies live in the DB (schema + seed owned by database-init/*.sql). The store
serves a process-local snapshot loaded by refresh(); get() is snapshot-only (no
I/O). publish()/rollback()/history() are the runtime-edit surface.

No in-code body fallback: if a key is unpublished or the DB is unreachable, get()
raises PromptNotFound (the caller surfaces it). Apply the DB schema + seed before
pointing the agent at a database.
"""

from __future__ import annotations

import logging

from prompts.port import NONE, PromptNotFound, PromptTemplate, declared_vars

log = logging.getLogger("prompts")

_SELECT = """SELECT t.key, t.engine, t.current_version, v.body, v.required_vars
             FROM prompt_template t
             JOIN prompt_version v ON v.key = t.key AND v.version = t.current_version"""


def validate(key: str, body: str, engine: str, required_vars: tuple[str, ...]) -> None:
    """Publish-time rail: reject a template that would render wrong. Checks the
    engine is known, the body is non-empty, and every ``$name`` is declared (an
    undeclared placeholder renders as the literal ``$foo``, read as an instruction)."""
    if engine != NONE:
        raise ValueError(f"prompt {key!r}: unknown engine {engine!r} (expected {NONE!r})")
    if not body.strip():
        raise ValueError(f"prompt {key!r}: empty body")
    undeclared = [v for v in declared_vars(body) if v not in required_vars]
    if undeclared:
        raise ValueError(
            f"prompt {key!r}: undeclared placeholders {undeclared} "
            "(declare them in required_vars or remove the $)"
        )


class PgPromptStore:
    """Postgres-backed, snapshot-served."""

    def __init__(self):
        self._snapshot: dict[str, PromptTemplate] = {}

    # --- read path (sync, snapshot-only) -----------------------------------------------------
    def get(self, key: str) -> PromptTemplate:
        tpl = self._snapshot.get(key)
        if tpl is None:
            raise PromptNotFound(key)
        return tpl

    def pinned(self) -> dict[str, int]:
        return {k: t.version for k, t in self._snapshot.items()}

    # --- load / write path (touches the DB) --------------------------------------------------
    def _engine(self):
        from db import get_engine

        return get_engine()

    def refresh(self) -> None:
        """Load the published snapshot. Best-effort: on any failure (including the
        tables not existing yet) keep serving the last good snapshot rather than
        breaking generation."""
        engine = self._engine()
        if engine is None:
            return
        from sqlalchemy import text

        try:
            with engine.begin() as conn:
                rows = conn.execute(text(_SELECT)).all()
        except Exception as exc:  # a prompt store must never break the pipeline
            log.warning("prompts: refresh failed (%s); serving last snapshot", exc)
            return
        snap: dict[str, PromptTemplate] = {}
        for key, eng, ver, body, req in rows:
            required = tuple(v for v in (req or "").split(",") if v)
            try:
                validate(key, body, eng, required)
            except ValueError as exc:
                log.warning("prompts: %s — dropping this key from the snapshot", exc)
                continue
            snap[key] = PromptTemplate(key=key, body=body, version=int(ver), engine=eng,
                                       required_vars=required)
        self._snapshot = snap
        log.info("prompts: snapshot loaded (%d published key(s))", len(snap))

    def publish(self, key: str, body: str, *, engine: str = NONE, note: str = "",
                created_by: str = "admin",
                required_vars: tuple[str, ...] | None = None) -> int:
        """Append a new version and move the pointer. Returns the new version number."""
        required = required_vars if required_vars is not None else declared_vars(body)
        validate(key, body, engine, required)
        eng = self._engine()
        if eng is None:
            raise RuntimeError("no database configured — cannot publish prompts")
        from sqlalchemy import text

        with eng.begin() as conn:
            nxt = conn.execute(
                text("""INSERT INTO prompt_version (key, version, body, required_vars, created_by, note)
                        SELECT :k, COALESCE(MAX(version), 0) + 1, :b, :r, :c, :n
                        FROM prompt_version WHERE key = :k
                        RETURNING version"""),
                {"k": key, "b": body, "r": ",".join(required), "c": created_by, "n": note},
            ).scalar_one()
            conn.execute(
                text("""INSERT INTO prompt_template (key, engine, current_version)
                        VALUES (:k, :e, :v)
                        ON CONFLICT (key) DO UPDATE SET engine = :e, current_version = :v"""),
                {"k": key, "e": engine, "v": nxt},
            )
        self.refresh()
        return int(nxt)

    def rollback(self, key: str, version: int) -> int:
        """Point ``key`` back at an existing version. No row is deleted."""
        eng = self._engine()
        if eng is None:
            raise RuntimeError("no database configured — cannot roll back prompts")
        from sqlalchemy import text

        with eng.begin() as conn:
            found = conn.execute(
                text("SELECT 1 FROM prompt_version WHERE key = :k AND version = :v"),
                {"k": key, "v": version},
            ).first()
            if not found:
                raise PromptNotFound(f"{key} v{version}")
            conn.execute(text("UPDATE prompt_template SET current_version = :v WHERE key = :k"),
                         {"k": key, "v": version})
        self.refresh()
        return version

    def history(self, key: str, limit: int = 20) -> list[dict]:
        """Version log for one key, newest first."""
        eng = self._engine()
        if eng is None:
            return []
        from sqlalchemy import text

        with eng.begin() as conn:
            rows = conn.execute(
                text("""SELECT version, created_at, created_by, note, length(body)
                        FROM prompt_version WHERE key = :k ORDER BY version DESC LIMIT :n"""),
                {"k": key, "n": limit},
            ).all()
        return [{"version": v, "created_at": str(ts), "created_by": by, "note": note, "chars": n}
                for v, ts, by, note, n in rows]
