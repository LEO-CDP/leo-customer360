"""Use case 9: Persona Decision Loop (customer context and next best action).

Two parts:

- `build_evidence` reads one customer's persona, features, recent transactions
  and persona history through an injected SQL runner. It selects behavioural
  columns only, never names, emails, phones or addresses.
- `decide` asks the model for Perception (what the evidence shows),
  Interpretation (the customer's current context) and Action (the next best
  action). The action must come from the closed `ALLOWED_ACTIONS` list, every
  fact it cites must exist in the evidence, and anything else is returned with
  `needs_review=True` and no action.

The rules come from the published instruction (prompt store). The allowed
actions, evidence and citable references are appended here. The instruction
tells the model to fall back to the first allowed action, so the first entry of
any action list is its default."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Optional, Protocol

from ai_providers.base import AIProviderError, parse_json_object
from use_cases.instructions import instructions
from use_cases.text_clean import strip_em_dashes

# The next_best_action vocabulary already used by the platform's persona scoring.
# The first entry is the default the model is told to fall back to.
ALLOWED_ACTIONS = (
    "Continue standard engagement cadence.",
    "Offer a loyalty upsell or premium tier upgrade.",
    "Send an acquisition offer to convert to a qualified lead.",
    "Send a re-engagement / win-back offer.",
    "Nurture with onboarding content to drive first purchase.",
    "Trigger a retention/win-back campaign to reduce churn risk.",
)

_DATA = """Allowed actions:
{actions}

Evidence (JSON):
{evidence}

Citable references:
{paths}
"""


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class PersonaDecision:
    perception: list[str] = field(default_factory=list)
    interpretation: Optional[str] = None
    action: Optional[str] = None
    channel: Optional[str] = None
    rationale: Optional[str] = None
    cited: list[str] = field(default_factory=list)
    needs_review: bool = False
    reason: Optional[str] = None


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _clean(text: str) -> str:
    return strip_em_dashes(text)


def _plain_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _plain(v) for k, v in row.items()}


def evidence_paths(evidence: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for section, content in evidence.items():
        if isinstance(content, dict):
            paths.update(f"{section}.{key}" for key in content)
        elif isinstance(content, list):
            paths.update(f"{section}.{i}" for i in range(len(content)))
    return paths


def build_evidence(run_sql: Callable[[str], list[dict[str, Any]]], master_profile_id: str) -> dict[str, Any]:
    pid = str(uuid.UUID(master_profile_id))  # raises ValueError for anything that is not a UUID
    latest = (
        "SELECT persona_id FROM customer360.cdp_customer_personas "
        f"WHERE master_profile_id = '{pid}' ORDER BY computed_at DESC LIMIT 1"
    )

    persona_rows = run_sql(
        "SELECT p.lifecycle_stage, p.customer_value_tier, p.risk_level, p.persona_score, p.engagement_score, "
        "p.next_best_action AS current_next_best_action, a.persona_name, a.persona_summary "
        "FROM customer360.cdp_customer_personas p "
        "LEFT JOIN customer360.cdp_persona_archetypes a ON a.persona_archetype_id = p.persona_archetype_id "
        f"WHERE p.master_profile_id = '{pid}' ORDER BY p.computed_at DESC LIMIT 1"
    )
    if not persona_rows:
        return {}

    feature_rows = run_sql(
        "SELECT f.feature_code, f.numeric_value, f.text_value, f.boolean_value "
        f"FROM customer360.cdp_persona_features f WHERE f.persona_id IN ({latest})"
    )
    features: dict[str, Any] = {}
    for row in feature_rows:
        for column in ("numeric_value", "text_value", "boolean_value"):
            if row.get(column) is not None:
                features[row["feature_code"]] = _plain(row[column])
                break

    transactions = run_sql(
        "SELECT entity_name, amount, currency, channel, transaction_type, campaign_name, transaction_time "
        f"FROM customer360.crm_transactions WHERE master_profile_id = '{pid}' "
        "ORDER BY transaction_time DESC LIMIT 10"
    )
    history = run_sql(
        "SELECT h.old_persona_name, h.new_persona_name, h.change_reason, h.changed_at "
        "FROM customer360.cdp_persona_history h "
        "JOIN customer360.cdp_customer_personas p ON p.persona_id = h.persona_id "
        f"WHERE p.master_profile_id = '{pid}' ORDER BY h.changed_at DESC LIMIT 5"
    )
    return {
        "persona": _plain_row(persona_rows[0]),
        "features": features,
        "transactions": [_plain_row(r) for r in transactions],
        "history": [_plain_row(r) for r in history],
    }


def _review(reason: str, **kept: Any) -> PersonaDecision:
    return PersonaDecision(needs_review=True, reason=reason, **kept)


def decide(
    evidence: dict[str, Any],
    provider: Provider,
    allowed_actions: tuple[str, ...] = ALLOWED_ACTIONS,
    suppressed: Optional[set[str]] = None,
) -> PersonaDecision:
    if not (evidence.get("persona") or evidence.get("features") or evidence.get("transactions")):
        return _review("insufficient evidence for this customer")

    paths = evidence_paths(evidence)
    prompt = f"{instructions('persona')}\n\n" + _DATA.format(
        actions="\n".join(f"- {a}" for a in allowed_actions),
        evidence=json.dumps(evidence, default=str),
        paths=", ".join(sorted(paths)),
    )
    try:
        data = parse_json_object(provider.complete(prompt))
    except AIProviderError as exc:
        return _review(str(exc))

    perception = data.get("perception")
    interpretation = data.get("interpretation")
    action = data.get("action")
    if (
        not isinstance(perception, list) or not perception or not all(isinstance(p, str) for p in perception)
        or not isinstance(interpretation, str) or not interpretation.strip()
        or not isinstance(action, dict) or not isinstance(action.get("code"), str)
    ):
        return _review("model reply is missing perception, interpretation or action")

    kept = {
        "perception": [_clean(p) for p in perception],
        "interpretation": _clean(interpretation),
    }
    code = action["code"]
    if code not in allowed_actions:
        return _review(f"action {code!r} is not one of the allowed actions", **kept)
    if suppressed and code in suppressed:
        return _review(f"action {code!r} is suppressed for this customer", **kept)

    cited = [c for c in data.get("cited", []) if isinstance(c, str)] if isinstance(data.get("cited"), list) else []
    if not cited:
        return _review("decision must cite at least one fact from the evidence", **kept)
    unknown = [c for c in cited if c not in paths]
    if unknown:
        return _review(f"cited evidence does not exist: {', '.join(unknown)}", **kept)

    return PersonaDecision(
        **kept,
        action=code,
        channel=action.get("channel") if isinstance(action.get("channel"), str) else None,
        rationale=_clean(action["rationale"]) if isinstance(action.get("rationale"), str) else None,
        cited=cited,
    )
