"""Use case 7: campaign performance narrative.

Turns the rows of a SQL result into a short plain-English summary for
non-technical readers. The model may only restate what the rows contain, so
any number in its text that cannot be traced to the data is reported in
`Narrative.ungrounded` for the caller to act on. The rules come from the
published instruction (prompt store); the question and rows are appended here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from use_cases.instructions import instructions
from use_cases.text_clean import strip_em_dashes

MAX_ROWS = 50
_NO_DATA = "There is no data to summarise for this question."
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

_DATA = """Question:
{question}

Data (JSON rows):
{rows}
"""


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class Narrative:
    text: str
    ungrounded: list[str] = field(default_factory=list)


def _candidate_values(rows: list[dict[str, Any]]) -> list[float]:
    """Every figure a faithful summary could quote: numeric cells, rates read
    as percentages, and digits that appear inside text cells."""
    values: list[float] = []
    for row in rows:
        for cell in row.values():
            if isinstance(cell, bool):
                continue
            if isinstance(cell, (int, float)):
                values.append(float(cell))
                if isinstance(cell, float) and 0 <= cell <= 1:
                    values.append(cell * 100)
            elif isinstance(cell, str):
                values.extend(float(m.replace(",", "")) for m in _NUMBER.findall(cell))
    return values


def ungrounded_numbers(text: str, rows: list[dict[str, Any]]) -> list[str]:
    """Numbers in `text` that match no figure in `rows` (within rounding)."""
    known = _candidate_values(rows)
    missing: list[str] = []
    for match in _NUMBER.findall(text):
        token = match.rstrip(",.").replace(",", "")
        if not token:
            continue
        number = float(token)
        if not any(abs(number - k) <= 0.5 for k in known):
            missing.append(token)
    return missing


def narrate(question: str, rows: list[dict[str, Any]], provider: Provider) -> Narrative:
    if not rows:
        return Narrative(text=_NO_DATA)

    shown = rows[:MAX_ROWS]
    prompt = f"{instructions('narrative')}\n\n" + _DATA.format(question=question, rows=json.dumps(shown, default=str))
    text = strip_em_dashes(provider.complete(prompt))
    return Narrative(text=text, ungrounded=ungrounded_numbers(text, shown))
