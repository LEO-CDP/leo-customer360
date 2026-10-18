"""Retrieval docs for Text-to-SQL, loaded from the live schema.

The doc format comes from `leo_customer360_agent.schema_docs`, shared with
customer360-api so both build identical docs. This module adds the SQL loader
that reads table comments and column names through an injected runner. The
stateless routes do not use it (the caller sends the docs); the agent's own
database tests do."""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from leo_customer360_agent.schema_docs import MAX_COMMENT_CHARS, build_schema_docs

__all__ = ["MAX_COMMENT_CHARS", "build_schema_docs", "load_schema_docs"]

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_QUERY = """SELECT c.relname AS table_name, a.attname AS column_name, obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
ORDER BY c.relname, a.attnum"""


def load_schema_docs(
    run_sql: Callable[[str], list[dict[str, Any]]],
    schema: str = "customer360",
    max_comment_chars: int = MAX_COMMENT_CHARS,
) -> dict[str, str]:
    if not _IDENTIFIER.match(schema):
        raise ValueError(f"invalid schema name: {schema!r}")

    columns: dict[str, list[str]] = {}
    comments: dict[str, Optional[str]] = {}
    for row in run_sql(_QUERY.format(schema=schema)):
        columns.setdefault(row["table_name"], []).append(row["column_name"])
        comments.setdefault(row["table_name"], row.get("comment"))
    return build_schema_docs(columns, comments, max_comment_chars)
