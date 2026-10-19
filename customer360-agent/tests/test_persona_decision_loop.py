"""Use case 9: Persona Decision Loop (Perception, Interpretation, Action).
The action comes from a closed list, every cited fact must exist in the
evidence, and anything doubtful goes to a person."""

import json

import pytest

from use_cases.persona_decision_loop import (
    ALLOWED_ACTIONS,
    build_evidence,
    decide,
    evidence_paths,
)

# Synthetic evidence, test-only.
EVIDENCE = {
    "persona": {"lifecycle_stage": "churn_risk", "risk_level": "high", "customer_value_tier": "gold"},
    "features": {"churn_probability": 0.72, "last_activity_recency_days": 41},
    "transactions": [{"entity_name": "Course A", "amount": 120.0, "channel": "web"}],
    "history": [{"old_persona_name": "Loyal", "new_persona_name": "At risk", "change_reason": "inactivity"}],
}
RETENTION = "Trigger a retention/win-back campaign to reduce churn risk."


class _Provider:
    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def _reply(**overrides):
    base = {
        "perception": ["Churn probability is 0.72", "No activity for 41 days"],
        "interpretation": "The customer is drifting away after a period of regular purchases.",
        "action": {"code": RETENTION, "channel": "email", "rationale": "High churn probability."},
        "cited": ["features.churn_probability", "features.last_activity_recency_days", "history.0"],
    }
    base.update(overrides)
    return base


def test_valid_reply_becomes_a_decision():
    decision = decide(EVIDENCE, _Provider(_reply()))

    assert decision.needs_review is False
    assert decision.action == RETENTION
    assert decision.channel == "email"
    assert decision.perception == ["Churn probability is 0.72", "No activity for 41 days"]
    assert "drifting away" in decision.interpretation
    assert decision.cited == ["features.churn_probability", "features.last_activity_recency_days", "history.0"]


def test_prompt_carries_the_evidence_and_the_allowed_actions():
    provider = _Provider(_reply())

    decide(EVIDENCE, provider)

    assert "0.72" in provider.prompts[0]
    assert RETENTION in provider.prompts[0]


def test_action_outside_the_closed_list_is_not_accepted():
    reply = _reply(action={"code": "Offer a 90% discount", "channel": "sms", "rationale": "x"})

    decision = decide(EVIDENCE, _Provider(reply))

    assert decision.needs_review is True
    assert decision.action is None
    assert "allowed" in decision.reason


def test_citing_evidence_that_does_not_exist_needs_review():
    decision = decide(EVIDENCE, _Provider(_reply(cited=["features.churn_probability", "features.mood_score"])))

    assert decision.needs_review is True
    assert "features.mood_score" in decision.reason


def test_a_decision_must_cite_at_least_one_fact():
    decision = decide(EVIDENCE, _Provider(_reply(cited=[])))

    assert decision.needs_review is True
    assert "cite" in decision.reason


def test_empty_evidence_skips_the_model():
    provider = _Provider(_reply())

    decision = decide({}, provider)

    assert provider.prompts == []
    assert decision.needs_review is True
    assert "insufficient evidence" in decision.reason


def test_suppressed_action_needs_review():
    decision = decide(EVIDENCE, _Provider(_reply()), suppressed={RETENTION})

    assert decision.needs_review is True
    assert decision.action is None
    assert "suppressed" in decision.reason


def test_malformed_model_output_is_flagged_not_raised():
    decision = decide(EVIDENCE, _Provider("not json"))

    assert decision.needs_review is True
    assert "valid JSON" in decision.reason


def test_missing_interpretation_needs_review():
    reply = _reply()
    del reply["interpretation"]

    assert decide(EVIDENCE, _Provider(reply)).needs_review is True


def test_em_dashes_are_replaced_in_the_written_fields():
    reply = _reply(
        interpretation="Drifting — after regular purchases.",
        perception=["Quiet — 41 days"],
        action={"code": RETENTION, "channel": "email", "rationale": "Act — now."},
    )

    decision = decide(EVIDENCE, _Provider(reply))

    for text in (decision.interpretation, decision.perception[0], decision.rationale):
        assert "—" not in text


def test_evidence_paths_lists_every_citable_reference():
    paths = evidence_paths(EVIDENCE)

    assert {"persona.risk_level", "features.churn_probability", "transactions.0", "history.0"} <= paths
    assert "features.mood_score" not in paths


def test_default_actions_match_the_vocabulary_already_used_by_the_platform():
    assert RETENTION in ALLOWED_ACTIONS
    assert "Continue standard engagement cadence." in ALLOWED_ACTIONS


def test_the_standard_cadence_is_the_first_action_because_the_instruction_falls_back_to_the_first():
    assert ALLOWED_ACTIONS[0] == "Continue standard engagement cadence."


# ---- evidence builder (fake SQL runner) ----

PROFILE_ID = "4a445289-da03-4d61-85ad-49b2d05d871f"


def _fake_runner(issued):
    def run(sql):
        issued.append(sql)
        if "FROM customer360.cdp_persona_features" in sql:
            return [
                {"feature_code": "churn_probability", "numeric_value": 0.72, "text_value": None, "boolean_value": None},
                {"feature_code": "membership_tier", "numeric_value": None, "text_value": "gold", "boolean_value": None},
                {"feature_code": "is_hashed", "numeric_value": None, "text_value": None, "boolean_value": False},
            ]
        if "FROM customer360.crm_transactions" in sql:
            return [{"entity_name": "Course A", "amount": 120, "channel": "web", "transaction_time": "2026-10-01"}]
        if "FROM customer360.cdp_persona_history" in sql:
            return [{"old_persona_name": "Loyal", "new_persona_name": "At risk", "change_reason": "inactivity"}]
        return [{"lifecycle_stage": "churn_risk", "risk_level": "high", "persona_name": "At risk"}]

    return run


def test_builder_assembles_persona_features_transactions_and_history():
    issued = []

    evidence = build_evidence(_fake_runner(issued), PROFILE_ID)

    assert evidence["persona"]["risk_level"] == "high"
    assert evidence["features"] == {"churn_probability": 0.72, "membership_tier": "gold", "is_hashed": False}
    assert evidence["transactions"][0]["entity_name"] == "Course A"
    assert evidence["history"][0]["change_reason"] == "inactivity"


def test_builder_never_selects_direct_identifiers():
    issued = []

    build_evidence(_fake_runner(issued), PROFILE_ID)

    joined = " ".join(issued).lower()
    for column in ("email", "phone", "full_name", "first_name", "last_name", "national_id", "address"):
        assert column not in joined


def test_builder_bounds_transactions_and_history():
    issued = []

    build_evidence(_fake_runner(issued), PROFILE_ID)

    assert any("crm_transactions" in s and "LIMIT 10" in s for s in issued)
    assert any("cdp_persona_history" in s and "LIMIT 5" in s for s in issued)


def test_builder_returns_empty_evidence_when_there_is_no_persona():
    def run(sql):
        return []

    assert build_evidence(run, PROFILE_ID) == {}


def test_builder_rejects_a_non_uuid_profile_id_before_any_query():
    issued = []

    with pytest.raises(ValueError):
        build_evidence(_fake_runner(issued), "x'; DROP TABLE y; --")

    assert issued == []
