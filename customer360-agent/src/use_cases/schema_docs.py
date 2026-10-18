"""Retrieval docs for Text-to-SQL, built from the live schema.

Each doc is the table name, the schema authors' own `COMMENT ON TABLE` text,
and the column names. Reading the comments from the database keeps the
descriptions in step with the schema; a table without a comment still gets its
columns, and the schema test suite insists new tables carry a comment."""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

MAX_COMMENT_CHARS = 300
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_QUERY = """SELECT c.relname AS table_name, a.attname AS column_name, obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
ORDER BY c.relname, a.attnum"""


def _tidy(comment: Optional[str], max_chars: int) -> Optional[str]:
    text = " ".join((comment or "").split())
    if not text:
        return None
    if len(text) > max_chars:
        cut = text[: max_chars - 3]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;:") + "..."
    return text if text[-1] in ".!?" else text + "."


def build_schema_docs(
    columns: dict[str, list[str]],
    comments: dict[str, Optional[str]],
    max_comment_chars: int = MAX_COMMENT_CHARS,
) -> dict[str, str]:
    docs: dict[str, str] = {}
    for table, cols in columns.items():
        description = _tidy(comments.get(table), max_comment_chars)
        head = f"{table}: {description}" if description else f"{table}."
        docs[table] = f"{head} Columns: {', '.join(cols)}"
    return docs


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
