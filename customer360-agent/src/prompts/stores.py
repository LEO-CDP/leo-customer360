"""Postgres-backed prompt store.

Bodies live in the DB (schema + seed owned by customer360-database/*.sql). The store
serves a process-local snapshot loaded by refresh(); get() is snapshot-only (no
I/O). publish()/rollback()/history() are the runtime-edit surface.

No in-code body fallback: if a key is unpublished or the DB is unreachable, get()
raises PromptNotFound (the caller surfaces it). Apply the DB schema + seed before
pointing the agent at a database.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from prompts.port import NONE, PromptNotFound, PromptTemplate, declared_vars

log = logging.getLogger("prompts")

_SELECT = """SELECT prompt_key, prompt_engine, instruction_version,
                    system_instructions, required_variables, prompt_versions
             FROM cdp_ai_agents
             WHERE prompt_key IS NOT NULL
               AND system_instructions IS NOT NULL"""


def _required_vars(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def _history(value) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("prompt history must contain valid JSON") from exc
    if not isinstance(value, list):
        raise ValueError("prompt history must be a JSON array")

    history: list[dict] = []
    seen_versions: set[int] = set()
    previous_version = 0
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("prompt history entries must be JSON objects")
        version = item.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ValueError("prompt history versions must be positive integers")
        if version in seen_versions or version <= previous_version:
            raise ValueError("prompt history versions must be unique and increasing")
        body = item.get("body")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("prompt history bodies must be non-empty strings")
        required_value = item.get("required_vars", [])
        if required_value is None:
            required_value = []
        if not isinstance(required_value, (list, tuple, str)):
            raise ValueError("prompt history required_vars must be an array or CSV string")
        normalized = dict(item)
        normalized["version"] = version
        normalized["body"] = body
        normalized["required_vars"] = list(_required_vars(required_value))
        history.append(normalized)
        seen_versions.add(version)
        previous_version = version
    return history


def _validate_prompt_state(
    key: str | None,
    version: int,
    body: str | None,
    required_vars: tuple[str, ...],
    revisions: list[dict],
) -> None:
    """Ensure the materialized current prompt agrees with its revision log."""
    if key is None:
        if revisions:
            raise ValueError("agent without a prompt_key cannot have prompt history")
        return
    if not body or not revisions:
        raise ValueError(f"prompt {key!r}: current body and history are required")
    current = next((item for item in revisions if item["version"] == version), None)
    if current is None:
        raise ValueError(f"prompt {key!r}: current version {version} is missing from history")
    if current["body"] != body:
        raise ValueError(f"prompt {key!r}: current body does not match version {version}")
    if tuple(current["required_vars"]) != tuple(required_vars):
        raise ValueError(f"prompt {key!r}: current required_vars do not match version {version}")


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
        for key, eng, ver, body, req, raw_history in rows:
            required = _required_vars(req)
            try:
                revisions = _history(raw_history)
                validate(key, body, eng, required)
                _validate_prompt_state(key, int(ver), body, required, revisions)
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
            agent_code = "prompt_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
            first_entry = {
                "version": 1,
                "body": body,
                "required_vars": list(required),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": created_by,
                "note": note,
            }
            created_agent_code = conn.execute(
                text("""INSERT INTO cdp_ai_agents (
                            agent_code, display_name, description, model_type, status,
                            prompt_key, prompt_engine, system_instructions,
                            required_variables, instruction_version,
                            instruction_updated_by, instruction_note, prompt_versions
                        ) VALUES (
                            :agent_code, :display_name, :description, 'generative_llm', 'ACTIVE',
                            :key, :engine, :body, :required_variables, 1,
                            :created_by, :note, CAST(:prompt_versions AS jsonb)
                        ) ON CONFLICT (prompt_key) DO NOTHING
                        RETURNING agent_code"""),
                {
                    "agent_code": agent_code,
                    "display_name": key,
                    "description": "Prompt-backed AI agent",
                    "key": key,
                    "engine": engine,
                    "body": body,
                    "required_variables": list(required),
                    "created_by": created_by,
                    "note": note,
                    "prompt_versions": json.dumps([first_entry]),
                },
            ).scalar_one_or_none()
            if created_agent_code is not None:
                first_publish = True
            else:
                first_publish = False

            if not first_publish:
                row = conn.execute(
                  text("""SELECT agent_code, instruction_version, system_instructions,
                          required_variables, prompt_versions
                       FROM cdp_ai_agents
                       WHERE prompt_key = :key
                       FOR UPDATE"""),
                {"key": key},
                    ).mappings().one_or_none()
                if row is None:
                    raise PromptNotFound(key)

                revisions = _history(row["prompt_versions"])
                current_version = int(row["instruction_version"] or 0)
                _validate_prompt_state(
                    key,
                    current_version,
                    row["system_instructions"],
                    _required_vars(row["required_variables"]),
                    revisions,
                )
                previous_versions = [int(item["version"]) for item in revisions]
                nxt = max([current_version, *previous_versions], default=0) + 1
                entry = {
                    "version": nxt,
                    "body": body,
                    "required_vars": list(required),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "created_by": created_by,
                    "note": note,
                }
                conn.execute(
                text("""UPDATE cdp_ai_agents
                       SET prompt_engine = :engine,
                           system_instructions = :body,
                           required_variables = :required_variables,
                           instruction_version = :version,
                           instruction_updated_by = :created_by,
                           instruction_note = :note,
                           prompt_versions = COALESCE(prompt_versions, '[]'::jsonb) || CAST(:entry AS jsonb),
                           updated_at = now()
                       WHERE agent_code = :agent_code"""),
                {
                    "engine": engine,
                    "body": body,
                    "required_variables": list(required),
                    "version": nxt,
                    "created_by": created_by,
                    "note": note,
                    "entry": json.dumps(entry),
                    "agent_code": row["agent_code"],
                },
                )
            else:
                nxt = 1
        self.refresh()
        return int(nxt)

    def rollback(self, key: str, version: int) -> int:
        """Point ``key`` back at an existing version. No row is deleted."""
        eng = self._engine()
        if eng is None:
            raise RuntimeError("no database configured — cannot roll back prompts")
        from sqlalchemy import text

        with eng.begin() as conn:
            row = conn.execute(
                  text("""SELECT agent_code, prompt_engine, prompt_versions
                       FROM cdp_ai_agents
                       WHERE prompt_key = :key
                       FOR UPDATE"""),
                {"key": key},
            ).mappings().one_or_none()
            revisions = _history(row["prompt_versions"]) if row else []
            selected = next((item for item in revisions if int(item["version"]) == version), None)
            if row is None or selected is None:
                raise PromptNotFound(f"{key} v{version}")
            required = _required_vars(selected.get("required_vars"))
            validate(key, selected.get("body", ""), row["prompt_engine"], required)
            conn.execute(
                text("""UPDATE cdp_ai_agents
                       SET system_instructions = :body,
                           required_variables = :required_variables,
                           instruction_version = :version,
                           instruction_updated_by = 'rollback',
                           instruction_note = :note,
                           updated_at = now()
                       WHERE agent_code = :agent_code"""),
                {
                    "body": selected.get("body", ""),
                    "required_variables": list(required),
                    "version": version,
                    "note": f"rollback to version {version}",
                    "agent_code": row["agent_code"],
                },
            )
        self.refresh()
        return version

    def history(self, key: str, limit: int = 20) -> list[dict]:
        """Version log for one key, newest first."""
        if limit < 0:
            raise ValueError("history limit must be non-negative")
        eng = self._engine()
        if eng is None:
            return []
        from sqlalchemy import text

        with eng.begin() as conn:
            row = conn.execute(
                text("SELECT prompt_versions FROM cdp_ai_agents WHERE prompt_key = :key"),
                {"key": key},
            ).mappings().one_or_none()
        if row is None:
            return []
        try:
            revisions = sorted(_history(row["prompt_versions"]), key=lambda item: int(item["version"]), reverse=True)
        except ValueError as exc:
            log.warning("prompts: history for %s is invalid (%s)", key, exc)
            return []
        return [
            {
                "version": int(item["version"]),
                "created_at": str(item.get("created_at", "")),
                "created_by": item.get("created_by", "system"),
                "note": item.get("note", ""),
                "chars": len(item.get("body", "")),
            }
            for item in revisions[:limit]
        ]
