"""Unit tests for the customer engagement flow engine (no database needed)."""

import unittest
import uuid

from pydantic import ValidationError

from leo_customer360_dao.models.identity import CdpMasterProfile
from leo_customer360_dao.workflow import FlowDefinition, FlowEngine, NodeDef
from leo_customer360_dao.workflow.nodes import render
from workflow_demo import ENGAGEMENT_SCORE_FOLLOW_UP, UatRunner, engagement_score_follow_up_cases


def _node(node_id, node_type, config=None, children=(), branch=None):
    return {
        "id": node_id,
        "type": node_type,
        "config": config or {},
        "children": list(children),
        **({"branch": branch} if branch else {}),
    }


def _profile(
    *,
    name="Ines",
    email="ines@example.com",
    phone_number=None,
    engagement_score=50,
    segmentation_tags=None,
    communication_preferences=None,
    push_token=True,
    **profile_fields,
):
    return CdpMasterProfile(
        master_profile_id=uuid.uuid4(),
        full_name=name,
        first_name=name.split(maxsplit=1)[0],
        email=email,
        phone_number=phone_number,
        engagement_score=engagement_score,
        segmentation_tags=segmentation_tags or [],
        communication_preferences=communication_preferences
        or {"email_opt_in": True, "sms_opt_in": True, "push_opt_in": True},
        push_tokens={"push": "test-token"} if push_token else {},
        **profile_fields,
    )


class DemoFlowUatTests(unittest.TestCase):
    def test_built_in_uat_suite_passes(self):
        outcomes = UatRunner(FlowEngine(ENGAGEMENT_SCORE_FOLLOW_UP)).run(engagement_score_follow_up_cases())
        self.assertEqual([o.case for o in outcomes if not o.passed], [])

    def test_built_in_flow_has_no_config_problems(self):
        self.assertEqual(FlowEngine(ENGAGEMENT_SCORE_FOLLOW_UP).problems(), [])


class ValidationTests(unittest.TestCase):
    def test_rejects_unsupported_schema_version(self):
        with self.assertRaises(ValidationError):
            FlowDefinition.model_validate({"schema_version": 3, "root": _node("n1", "trigger", {"event": "signup"})})

    def test_rejects_duplicate_node_ids(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node("n1", "trigger", {"event": "signup"}, [_node("n1", "exit", {"kind": "goal"})]),
            }
        )
        with self.assertRaisesRegex(ValueError, "Duplicate node id n1"):
            FlowEngine(flow)

    def test_rejects_unknown_node_type(self):
        flow = FlowDefinition.model_validate({"schema_version": 2, "root": _node("n1", "trigger", {"event": "signup"}, [_node("n2", "teleport")])})
        with self.assertRaisesRegex(ValueError, 'Unknown node type "teleport"'):
            FlowEngine(flow)

    def test_reports_flow_that_does_not_start_with_trigger(self):
        flow = FlowDefinition(root=NodeDef(id="n1", type="exit", config={"kind": "goal"}))
        self.assertIn("n1: the flow must start with a trigger", FlowEngine(flow).problems())

    def test_condition_needs_valid_operator_for_field(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [
                        _node(
                            "n2",
                            "condition",
                            {"field": "segmentation_tags", "op": "ge", "value": "vip"},
                        )
                    ],
                ),
            }
        )
        self.assertIn("n2: segmentation_tags needs op has/hasnt", FlowEngine(flow).problems())

    def test_condition_rejects_unknown_profile_field(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [
                        _node(
                            "n2",
                            "condition",
                            {"field": "unknown", "op": "eq", "value": 1},
                            [
                                _node("n3", "exit", {"kind": "goal"}, branch="yes"),
                                _node("n4", "exit", {"kind": "exit"}, branch="no"),
                            ],
                        )
                    ],
                ),
            }
        )
        self.assertIn("n2: unknown numeric field 'unknown'", FlowEngine(flow).problems())

    def test_wait_rejects_negative_duration_and_unknown_unit(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [_node("n2", "wait", {"amount": -1, "unit": "weeks"})],
                ),
            }
        )
        problems = FlowEngine(flow).problems()
        self.assertIn("n2: amount must be finite and non-negative", problems)
        self.assertIn("n2: unit must be minutes, hours or days", problems)

    def test_update_profile_rejects_unknown_action_without_removing_tag(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [
                        _node(
                            "n2",
                            "update_profile",
                            {"action": "typo", "tag": "vip"},
                        )
                    ],
                ),
            }
        )
        engine = FlowEngine(flow)
        self.assertIn("n2: action must be add_tag or remove_tag", engine.problems())
        result = engine.run(_profile(segmentation_tags=["vip"]))
        self.assertEqual(result.end, "error")
        self.assertIn("vip", result.tags)

    def test_branch_without_matching_child_fails_as_journey_error(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [
                        _node(
                            "n2",
                            "condition",
                            {"field": "engagement_score", "op": "ge", "value": 50},
                            [_node("n3", "exit", {"kind": "goal"}, branch="yes")],
                        )
                    ],
                ),
            }
        )
        result = FlowEngine(flow).run(_profile(engagement_score=10))
        self.assertEqual(result.end, "error")
        self.assertIn("no unique child for branch 'no'", str(result.error))

    def test_engine_rejects_non_positive_step_limit(self):
        with self.assertRaises(ValueError):
            FlowEngine(ENGAGEMENT_SCORE_FOLLOW_UP, max_steps=0)


class RenderTests(unittest.TestCase):
    def test_replaces_known_placeholders_and_keeps_unknown(self):
        self.assertEqual(render("Hi {{ first_name }} {{nickname}}", {"first_name": "Ines"}), "Hi Ines {{nickname}}")

    def test_missing_template_renders_empty(self):
        self.assertEqual(render(None, {}), "")


class RunSemanticsTests(unittest.TestCase):
    def test_conditions_evaluate_customer_score_fields_from_master_profile(self):
        score_values = {
            "engagement_score": (80, "ge", 60),
            "churn_probability": (0.2, "lt", 0.4),
            "lead_conversion_probability": (0.8, "ge", 0.7),
            "historical_clv": (1500, "ge", 1000),
            "predictive_clv": (3000, "ge", 2500),
            "latest_nps_score": (9, "ge", 8),
            "average_csat": (4.5, "ge", 4),
            "overall_sentiment_score": (0.6, "ge", 0.2),
            "profile_completeness_score": (90, "ge", 80),
            "identity_confidence_score": (0.95, "ge", 0.9),
        }
        for field, (actual, operator, threshold) in score_values.items():
            with self.subTest(field=field):
                flow = FlowDefinition.model_validate(
                    {
                        "schema_version": 2,
                        "root": _node(
                            "n1",
                            "trigger",
                            {"event": "signup"},
                            [
                                _node(
                                    "n2",
                                    "condition",
                                    {"field": field, "op": operator, "value": threshold},
                                    [
                                        _node("n3", "exit", {"kind": "goal"}, branch="yes"),
                                        _node("n4", "exit", {"kind": "exit"}, branch="no"),
                                    ],
                                )
                            ],
                        ),
                    }
                )
                engine = FlowEngine(flow)
                self.assertEqual(engine.problems(), [])
                result = engine.run(_profile(**{field: actual}))
                self.assertEqual(result.end, "goal")
                self.assertEqual(result.steps[1].branch, "yes")

    def test_operational_profile_status_is_not_a_marketing_condition(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [
                        _node(
                            "n2",
                            "condition",
                            {"field": "status_code", "op": "eq", "value": 1},
                            [
                                _node("n3", "exit", {"kind": "goal"}, branch="yes"),
                                _node("n4", "exit", {"kind": "exit"}, branch="no"),
                            ],
                        )
                    ],
                ),
            }
        )
        self.assertIn(
            "n2: unknown numeric field 'status_code'",
            FlowEngine(flow).problems(),
        )

    def test_missing_channel_consent_skips_message(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [_node("n2", "email", {"subject": "Hi", "body": "Hello"})],
                ),
            }
        )
        profile = _profile()
        profile.communication_preferences = {}
        result = FlowEngine(flow).run(profile)
        self.assertEqual(result.deliveries, [("email", "skipped")])

    def test_consent_gate_skips_message_without_contact(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [_node("n2", "notification", {"body": "Hi"}, [_node("n3", "exit", {"kind": "goal"})])],
                ),
            }
        )
        result = FlowEngine(flow).run(_profile(name="NoPhone", phone_number=None))
        self.assertEqual(result.deliveries, [("notification", "skipped")])
        self.assertEqual(result.end, "goal")

    def test_step_limit_stops_runaway_flow(self):
        result = FlowEngine(ENGAGEMENT_SCORE_FOLLOW_UP, max_steps=2).run(_profile(engagement_score=70))
        self.assertEqual(result.end, "error")
        self.assertIn("more than 2 steps", str(result.error))

    def test_missing_numeric_value_takes_the_no_branch(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "signup"},
                    [
                        _node(
                            "n2",
                            "condition",
                            {"field": "engagement_score", "op": "ge", "value": "60"},
                            [
                                _node("n3", "exit", {"kind": "goal"}, branch="yes"),
                                _node("n4", "exit", {"kind": "exit"}, branch="no"),
                            ],
                        )
                    ],
                ),
            }
        )
        result = FlowEngine(flow).run(_profile(engagement_score=None))
        self.assertIsNone(result.error)
        self.assertEqual(result.steps[1].branch, "no")
        self.assertEqual(result.end, "exit")

    def test_update_profile_records_tags_without_mutating_the_profile(self):
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node(
                    "n1",
                    "trigger",
                    {"event": "purchase"},
                    [
                        _node("n2", "update_profile", {"action": "add_tag", "tag": "vip"}, [
                            _node("n3", "exit", {"kind": "goal"}),
                        ]),
                    ],
                ),
            }
        )
        profile = _profile(segmentation_tags=["new"])
        result = FlowEngine(flow).run(profile)
        self.assertEqual(result.tags, ["new", "vip"])
        self.assertEqual(profile.segmentation_tags, ["new"])


class ProductionValidationTests(unittest.TestCase):
    def _flow(self, root_event="signup", extra=None):
        children = [extra] if extra else [_node("n2", "exit", {"kind": "goal"})]
        return FlowDefinition.model_validate(
            {"schema_version": 2, "root": _node("n1", "trigger", {"event": root_event}, children)}
        )

    def test_unknown_trigger_event_is_a_problem(self):
        problems = FlowEngine(self._flow(root_event="engagement_follow_up")).problems()
        self.assertIn("n1: event must be one of", " ".join(problems))

    def test_unknown_wait_event_is_a_problem(self):
        wait = _node(
            "n2",
            "wait_event",
            {"event": "page_viewed", "amount": 1, "unit": "days"},
            [_node("n3", "exit", {"kind": "goal"}, branch="happened"), _node("n4", "exit", {"kind": "exit"}, branch="timeout")],
        )
        problems = FlowEngine(self._flow(extra=wait)).problems()
        self.assertIn("n2: event must be one of", " ".join(problems))

    def test_unknown_placeholder_in_a_message_is_a_problem(self):
        email = _node("n2", "email", {"subject": "Hi {{cart_value}}", "body": "Hello"})
        problems = FlowEngine(self._flow(extra=email)).problems()
        self.assertIn("n2: unknown placeholder {{cart_value}} in subject", problems)

    def test_known_placeholder_passes(self):
        email = _node("n2", "email", {"subject": "Hi {{first_name}}", "body": "Hello {{first_name}}"})
        self.assertEqual(FlowEngine(self._flow(extra=email)).problems(), [])

    def test_audience_sync_node_requires_a_supported_platform_and_name(self):
        sync = _node(
            "n2",
            "run_data_synch_job",
            {"platform": "meta_ads", "audience_name": "Spring audience"},
        )
        self.assertEqual(FlowEngine(self._flow(extra=sync)).problems(), [])
        invalid = _node("n2", "run_data_synch_job", {"platform": "unknown", "audience_name": ""})
        self.assertEqual(len(FlowEngine(self._flow(extra=invalid)).problems()), 2)

    def test_notification_node_accepts_supported_providers(self):
        for provider in ("sms", "zalo_oa", "whatsapp", "mobile_native"):
            with self.subTest(provider=provider):
                notification = _node(
                    "n2",
                    "notification",
                    {"provider": provider, "body": "Hello {{first_name}}"},
                )
                self.assertEqual(FlowEngine(self._flow(extra=notification)).problems(), [])

    def test_notification_node_rejects_unknown_provider(self):
        notification = _node(
            "n2",
            "notification",
            {"provider": "carrier_unknown", "body": "Hello"},
        )
        self.assertIn("provider must be one of", " ".join(FlowEngine(self._flow(extra=notification)).problems()))

    def test_mobile_native_notification_uses_push_consent_and_tokens(self):
        notification = _node(
            "n2",
            "notification",
            {"provider": "mobile_native", "body": "Hello"},
            [_node("n3", "exit", {"kind": "goal"})],
        )
        flow = FlowDefinition.model_validate(
            {
                "schema_version": 2,
                "root": _node("n1", "trigger", {"event": "signup"}, [notification]),
            }
        )
        profile = _profile(
            phone_number=None,
            communication_preferences={"push_opt_in": True},
            push_token=True,
        )
        result = FlowEngine(flow).run(profile)
        self.assertEqual(result.deliveries, [("notification", "sent")])

    def test_failed_delivery_stops_profile_before_downstream_actions(self):
        from unittest.mock import Mock
        from leo_customer360_dao.workflow.models import DeliveryReceipt
        from leo_customer360_dao.workflow.services import Services

        gateway = Mock()
        gateway.send.return_value = DeliveryReceipt(status="failed", detail="Provider unavailable")
        writer = Mock()
        email = _node("n2", "email", {"subject": "Hi", "body": "Hello"}, [
            _node("n3", "update_profile", {"action": "add_tag", "tag": "delivered"}),
        ])
        services = Services(gateways={"email": gateway}, profile_writer=writer)

        result = FlowEngine(self._flow(extra=email), services=services).run(_profile())

        self.assertEqual(result.end, "error")
        self.assertEqual(result.deliveries, [("email", "failed")])
        self.assertIn("Provider unavailable", result.error)
        writer.apply.assert_not_called()

    def test_blank_messages_and_non_string_providers_are_validation_problems(self):
        for node in (
            _node("n2", "email", {"subject": " ", "body": ""}),
            _node("n2", "notification", {"provider": [], "body": "Hello"}),
            _node("n2", "run_data_synch_job", {"platform": [], "audience_name": "UAT"}),
        ):
            with self.subTest(node=node):
                self.assertTrue(FlowEngine(self._flow(extra=node)).problems())


if __name__ == "__main__":
    unittest.main()
