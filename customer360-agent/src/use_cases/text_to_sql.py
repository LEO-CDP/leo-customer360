"""Use case 2: schema-grounded Text-to-SQL.

Only the tables relevant to the question go into the prompt, so the prompt
stays small when the real schema has hundreds of tables. The model's SQL is
checked by `sql_guard` before the injected `run_sql` sees it. The rules come
from the published instruction (prompt store); the schema and question are
appended here."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from leo_customer360_agent.sql_guard import UnsafeSQLError, ensure_select_only
from use_cases.instructions import instructions

NOT_FOUND = "NOT_FOUND_ANSWERS"

_DATA = """Schema:
{schema}

Question:
{question}
"""


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class TextToSQLResult:
    sql: str
    rows: Optional[list[dict[str, Any]]]
    error: Optional[str]


# Words that carry no topic. Without this list "from" matches a `from_node` column
# and "what" or "how" match anything, so unrelated tables look relevant.
_STOP_WORDS = frozenset(
    "the and for are was were from with what which who whom whose how many much did does do have has had "
    "our your their that this these those per each all any can you show list give get find tell into about "
    "than then when where why there here over last more most some not out its his her they them".split()
)


def _tokens(text: str) -> set[str]:
    # Trailing "s" is dropped so "segments" matches "segment".
    words = re.split(r"[^a-z0-9]+", text.lower())
    return {w.rstrip("s") for w in words if len(w) > 2 and w not in _STOP_WORDS}


def select_relevant_tables(
    question: str, schema: dict[str, str], k: int = 3, require_match: bool = False
) -> list[str]:
    """Top-k table names by word overlap between the question and each table's
    doc. A match in the table name counts double, so a table named for the
    topic beats one that only has a related column.

    By default the list is padded to k with zero-overlap entries in dictionary
    order, which suits callers that want some context whatever the query. With
    `require_match=True` only entries that share a word with the question are
    returned, so an unmatched question yields []."""
    wanted = _tokens(question)
    scored = [
        (len(wanted & _tokens(doc)) + len(wanted & _tokens(name)), -i, name)
        for i, (name, doc) in enumerate(schema.items())
    ]
    scored.sort(reverse=True)
    return [name for score, _, name in scored[:k] if score > 0 or not require_match]


@dataclass(frozen=True)
class SQLProposal:
    """A checked SQL statement, ready for the caller to run. Nothing is executed here.

    status "ok": `sql` is a single read-only statement. "not_found": the schema
    cannot answer. "refused": the model wrote something that is not a single
    SELECT; `sql` keeps the raw text so the caller can log it."""

    status: str
    sql: Optional[str]
    error: Optional[str]


def contextualise(question: str, turns: Optional[list[dict[str, str]]]) -> str:
    """Prefix a follow-up question with the earlier question and SQL pairs."""
    if not turns:
        return question
    earlier = "\n".join(f"Q: {t['question']}\nSQL: {t['sql']}" for t in turns)
    return f"Earlier in this conversation:\n{earlier}\n\nFollow-up question: {question}"


def generate_sql(
    question: str,
    provider: Provider,
    schema: dict[str, str],
    k: int = 3,
    history: Optional[list[dict[str, str]]] = None,
) -> SQLProposal:
    text = contextualise(question, history)
    tables = select_relevant_tables(text, schema, k, require_match=True)
    if not tables:
        # Nothing in the schema shares a word with the question. Sending unrelated
        # tables would invite SQL against the wrong ones, so report "not found".
        return SQLProposal("not_found", None, None)
    prompt = f"{instructions('sql')}\n\n" + _DATA.format(schema="\n".join(schema[t] for t in tables), question=text)
    raw = provider.complete(prompt).strip()
    if raw == NOT_FOUND:
        return SQLProposal("not_found", None, None)

    try:
        return SQLProposal("ok", ensure_select_only(raw), None)
    except UnsafeSQLError as exc:
        return SQLProposal("refused", raw, f"Refused, only a single SELECT is allowed: {exc}")


def answer_question(
    question: str,
    provider: Provider,
    schema: dict[str, str],
    run_sql: Callable[[str], list[dict[str, Any]]],
    k: int = 3,
) -> TextToSQLResult:
    proposal = generate_sql(question, provider, schema, k)
    if proposal.status == "not_found":
        return TextToSQLResult(sql=NOT_FOUND, rows=None, error=None)
    if proposal.status == "refused":
        return TextToSQLResult(sql=proposal.sql, rows=None, error=proposal.error)

    try:
        rows = run_sql(proposal.sql)
    except Exception as exc:  # the database error text is the useful part for the caller
        return TextToSQLResult(sql=proposal.sql, rows=None, error=str(exc))
    return TextToSQLResult(sql=proposal.sql, rows=rows, error=None)
