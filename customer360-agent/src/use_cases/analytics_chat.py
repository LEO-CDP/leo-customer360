"""Use case 5: "ask your data" analytics chat.

A conversational layer over Text-to-SQL. Each follow-up is sent with the
previous question and SQL so "now break that down by city" resolves. Only
successful turns become context, so one refused or failed query cannot steer
the next. Results carry a chart-ready shape for the UI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from use_cases.performance_narrative import narrate
from use_cases.text_to_sql import NOT_FOUND, Provider, answer_question, contextualise

MAX_TURNS = 5
_CANNOT_ANSWER = "I cannot answer that from the data I can see. Try naming a table, metric or time range."
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class ChatTurn:
    question: str
    sql: Optional[str]
    rows: Optional[list[dict[str, Any]]]
    error: Optional[str]
    narrative: Optional[str]
    chart: Optional[dict[str, Any]]
    ungrounded: list[str] = field(default_factory=list)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def chart_spec(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """A minimal, renderer-neutral description of how to plot `rows`."""
    if not rows:
        return None
    columns = list(rows[0].keys())
    numeric = [c for c in columns if all(_is_number(r[c]) for r in rows)]
    labels = [c for c in columns if c not in numeric]
    if not numeric:
        return {"type": "table"}
    if len(rows) == 1 and not labels:
        return {"type": "metric", "y": numeric}
    if len(labels) == 1:
        label = labels[0]
        is_date = all(isinstance(r[label], str) and _DATE.match(r[label]) for r in rows)
        return {"type": "line" if is_date else "bar", "x": label, "y": numeric}
    return {"type": "table"}


class AnalyticsChat:
    def __init__(
        self,
        sql_provider: Provider,
        narrative_provider: Provider,
        schema: dict[str, str],
        run_sql: Callable[[str], list[dict[str, Any]]],
        k: int = 3,
    ) -> None:
        self._sql_provider = sql_provider
        self._narrative_provider = narrative_provider
        self._schema = schema
        self._run_sql = run_sql
        self._k = k
        self.history: list[ChatTurn] = []

    def _with_context(self, question: str) -> str:
        return contextualise(question, [{"question": t.question, "sql": t.sql} for t in self.history])

    def ask(self, question: str) -> ChatTurn:
        result = answer_question(
            self._with_context(question), self._sql_provider, self._schema, self._run_sql, self._k
        )
        if result.sql == NOT_FOUND:
            return ChatTurn(question, None, None, None, _CANNOT_ANSWER, None)
        if result.error is not None:
            return ChatTurn(question, result.sql, None, result.error, None, None)

        story = narrate(question, result.rows or [], self._narrative_provider)
        turn = ChatTurn(
            question=question,
            sql=result.sql,
            rows=result.rows,
            error=None,
            narrative=story.text,
            chart=chart_spec(result.rows or []),
            ungrounded=story.ungrounded,
        )
        self.history = (self.history + [turn])[-MAX_TURNS:]
        return turn
