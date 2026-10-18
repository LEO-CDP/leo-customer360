"""Retrieval docs for Text-to-SQL: one line per table.

Each doc is the table name, the schema authors' own `COMMENT ON TABLE` text, and
the column names. This is the pure builder, shared by the agent server and by
customer360-api so both produce identical docs. The caller decides which tables
and columns to include; a table without a comment still gets its columns."""

from __future__ import annotations

from typing import Optional

MAX_COMMENT_CHARS = 300


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
