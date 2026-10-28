"""Use case 8: schema-mapping assistant for client onboarding.

Proposes which CDP field each raw source column belongs to. The model's
answer is a proposal: a mapping is `mapped` only when the target exists in the
CDP schema, confidence clears the threshold, and no other column claims the
same target. Everything else is `needs_review` or `unmapped` for a person.

Sample values can be customer PII, so they stay out of the prompt unless the
caller opts in. The rules come from the published instruction (prompt store);
the CDP fields and source columns are appended here."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Optional, Protocol

from ai_providers.base import AIProviderError, parse_json_object
from use_cases.instructions import instructions
from use_cases.text_to_sql import select_relevant_tables

MIN_CONFIDENCE = 0.8
MAX_SAMPLES = 3

_DATA = """CDP fields:
{targets}

Source columns:
{sources}
"""


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class FieldMapping:
    source: str
    target: Optional[str]
    suggested: Optional[str]
    confidence: Optional[float]
    status: str  # "mapped" | "needs_review" | "unmapped"
    reason: Optional[str]


def _confidence(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _judge(source: str, entry: Optional[dict[str, Any]], target_schema: dict[str, str]) -> FieldMapping:
    if entry is None:
        return FieldMapping(source, None, None, None, "unmapped", "no mapping returned for this column")

    proposed = entry.get("target")
    confidence = _confidence(entry.get("confidence"))
    note = entry.get("reason")
    if not proposed:
        return FieldMapping(source, None, None, confidence, "unmapped", note or "no matching CDP field")
    if proposed not in target_schema:
        return FieldMapping(
            source, None, proposed, confidence, "needs_review", f"{proposed!r} is not a CDP field"
        )
    if confidence is None:
        return FieldMapping(source, proposed, proposed, None, "needs_review", "confidence missing or not a number")
    if confidence < MIN_CONFIDENCE:
        return FieldMapping(
            source, proposed, proposed, confidence, "needs_review",
            f"confidence {confidence:.2f} is below {MIN_CONFIDENCE}",
        )
    return FieldMapping(source, proposed, proposed, confidence, "mapped", note)


def _flag_shared_targets(mappings: list[FieldMapping]) -> list[FieldMapping]:
    claimed: dict[str, list[str]] = {}
    for m in mappings:
        if m.target:
            claimed.setdefault(m.target, []).append(m.source)
    shared = {t: s for t, s in claimed.items() if len(s) > 1}
    return [
        replace(m, status="needs_review", reason=f"target {m.target} is claimed by several columns: {', '.join(shared[m.target])}")
        if m.target in shared
        else m
        for m in mappings
    ]


def propose_mappings(
    source_columns: list[dict[str, Any]],
    target_schema: dict[str, str],
    provider: Provider,
    include_samples: bool = False,
    max_targets: int = 25,
) -> list[FieldMapping]:
    names = [c["name"] for c in source_columns]
    relevant = select_relevant_tables(" ".join(names), target_schema, k=max_targets)

    def describe(column: dict[str, Any]) -> str:
        if include_samples and column.get("samples"):
            return f"- {column['name']} (examples: {json.dumps(column['samples'][:MAX_SAMPLES])})"
        return f"- {column['name']}"

    prompt = f"{instructions('schema_mapping')}\n\n" + _DATA.format(
        targets="\n".join(target_schema[t] for t in relevant),
        sources="\n".join(describe(c) for c in source_columns),
    )
    data = parse_json_object(provider.complete(prompt))
    returned = data.get("mappings")
    if not isinstance(returned, list):
        raise AIProviderError("AI provider response did not contain a mappings list")

    by_source = {e["source"]: e for e in returned if isinstance(e, dict) and "source" in e}
    return _flag_shared_targets([_judge(n, by_source.get(n), target_schema) for n in names])
