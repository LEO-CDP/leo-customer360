"""SELECT-only guard for model-written SQL.

The model's output is untrusted. This accepts exactly one read-only statement
and rejects everything else; the caller should still run it on a read-only
database role."""

from __future__ import annotations

import re

_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "truncate", "create",
    "grant", "revoke", "merge", "copy", "call", "execute", "vacuum",
)
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN) + r")\b", re.IGNORECASE)


class UnsafeSQLError(ValueError):
    """The model produced SQL that is not a single read-only statement."""


def _strip_fence(sql: str) -> str:
    sql = sql.strip()
    if sql.startswith("```"):
        parts = sql.split("\n", 1)
        sql = parts[1] if len(parts) > 1 else ""
        if sql.rstrip().endswith("```"):
            sql = sql.rstrip()[:-3]
    return sql.strip()


def _mask_literals_and_comments(sql: str) -> str:
    """Blank out string literals and comments so keyword and `;` checks only
    see executable SQL."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def ensure_select_only(raw_sql: str) -> str:
    """Return the cleaned statement, or raise UnsafeSQLError."""
    sql = _strip_fence(raw_sql).rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("empty SQL")

    masked = _mask_literals_and_comments(sql)
    if ";" in masked:
        raise UnsafeSQLError("only a single statement is allowed")
    if not re.match(r"\s*(select|with)\b", masked, re.IGNORECASE):
        raise UnsafeSQLError("only SELECT statements are allowed")
    if _FORBIDDEN_RE.search(masked):
        raise UnsafeSQLError("statement contains a write or DDL keyword")
    return sql
