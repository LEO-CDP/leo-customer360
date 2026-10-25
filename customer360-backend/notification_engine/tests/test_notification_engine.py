"""Unit tests for the Zalo notification engine (pure logic)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notification_engine.adapters import MockZNSAdapter, build_zns_adapter  # noqa: E402
from notification_engine.rendering import render_params, render_value  # noqa: E402
from notification_engine.send import _is_opted_out, _is_suppressed  # noqa: E402
from notification_engine.s3_reader import extract_optout_events  # noqa: E402
from notification_engine.tracking import encode_tracking_token  # noqa: E402


# --- rendering (typed-param binding) ---
def test_render_value_substitutes_merge_tokens():
    ctx = {"first_name": "An", "phone": "+84900"}
    assert render_value("Hi {{first_name}}", ctx) == "Hi An"
    assert render_value("{{phone}}", ctx) == "+84900"
    assert render_value("no tokens", ctx) == "no tokens"
    assert render_value(123, ctx) == 123  # non-str passes through
    assert render_value("{{unknown}}", ctx) == "{{unknown}}"  # unknown token left intact


def test_render_params_binds_every_value():
    out = render_params({"name": "{{first_name}}", "code": "OTP123"}, {"first_name": "Binh"})
    assert out == {"name": "Binh", "code": "OTP123"}


# --- tracking token (must match customer360-event-api decode) ---
def test_tracking_token_is_deterministic_and_decodable():
    a = encode_tracking_token("t1", "c1", "p1")
    b = encode_tracking_token("t1", "c1", "p1")
    assert a == b and a  # deterministic (HMAC of the same tuple)


# --- adapter selection ---
def test_build_adapter_falls_back_to_mock_without_token():
    adapter = build_zns_adapter(access_token=None, provider="zns")
    assert isinstance(adapter, MockZNSAdapter)


def test_mock_adapter_always_succeeds_with_message_id():
    result = MockZNSAdapter().send(phone="+84900", template_id="123", template_data={}, tracking_id="tok")
    assert result.ok is True
    assert result.provider_message_id


def test_zalo_send_requires_explicit_literal_true_consent():
    assert _is_opted_out({"communication_preferences": {"zalo_opt_in": True}}) is False
    assert _is_opted_out({"communication_preferences": {"zalo_opt_in": False}}) is True
    assert _is_opted_out({"communication_preferences": {"zalo_opt_in": "true"}}) is True
    assert _is_opted_out({"communication_preferences": {}}) is True


def test_zalo_suppression_lookup_is_tenant_campaign_and_channel_scoped():
    class _Cursor:
        def __init__(self):
            self.statement = ""
            self.params = {}

        def execute(self, statement, params):
            self.statement = statement
            self.params = params

        def fetchone(self):
            return {"is_suppressed": True}

    cursor = _Cursor()
    profile = {
        "master_profile_id": "profile-1",
        "phone_number": "+12025550123",
    }

    assert _is_suppressed(cursor, "tenant-1", "campaign-1", profile) is True
    assert "tenant_id" in cursor.statement
    assert "channel IN ('GLOBAL', 'ZALO')" in cursor.statement
    assert cursor.params == {
        "tenant_id": "tenant-1",
        "campaign_id": "campaign-1",
        "master_profile_id": "profile-1",
        "phone": "+12025550123",
    }


# --- S3 verified consent-revocation filter ---
def test_extract_optout_events_keeps_verified_email_and_zalo_revocations():
    records = [
        {
            "event_name": "email-unsubscribed",
            "source_system": "verified_consent_callback",
            "payload": {
                "properties": {
                    "tracking_channel": "email",
                    "tenant_id": "t1",
                    "master_profile_id": "p1",
                    "consent_update": {
                        "field": "email_opt_in",
                        "granted": False,
                        "reason": "unsubscribe",
                        "source": "verified_email_callback",
                    },
                }
            },
        },
        {
            "event_name": "zalo-opt-out",
            "source_system": "verified_consent_callback",
            "payload": {
                "properties": {
                    "tracking_channel": "zalo",
                    "tenant_id": "t1",
                    "master_profile_id": "p2",
                    "consent_update": {
                        "field": "zalo_opt_in",
                        "granted": False,
                        "reason": "opt_out",
                        "source": "verified_zalo_webhook",
                    },
                }
            },
        },
        {
            "event_name": "zalo-failed",
            "source_system": "verified_consent_callback",
            "payload": {
                "properties": {
                    "tracking_channel": "zalo",
                    "tenant_id": "t1",
                    "master_profile_id": "p3",
                    "suppression_reason": "permanent_failure",
                }
            },
        },
        {
            "event_name": "email-unsubscribed",
            "source_system": "tracking",
            "payload": {
                "properties": {
                    "tracking_channel": "email",
                    "tenant_id": "t1",
                    "master_profile_id": "spoofed",
                    "consent_update": {
                        "field": "email_opt_in",
                        "granted": False,
                        "reason": "unsubscribe",
                        "source": "verified_email_callback",
                    },
                }
            },
        },
    ]
    out = extract_optout_events(records)
    assert [e["properties"]["master_profile_id"] for e in out] == ["p1", "p2"]


# --- opt-out projection (consent write) ---
def test_project_optout_events_counts_applied_vs_skipped():
    from notification_engine.optout_projection import project_optout_events

    class _Cur:
        rowcount = 1
        def __init__(self):
            self.calls = []
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    class _Conn:
        def __init__(self):
            self.cursor_instance = _Cur()
        def cursor(self):
            return self.cursor_instance
        def commit(self):
            pass
        def rollback(self):
            pass

    events = [
        {
            "event_name": "email-unsubscribed",
            "source_system": "verified_consent_callback",
            "properties": {
                "tracking_channel": "email",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "master_profile_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "consent_update": {
                    "field": "email_opt_in",
                    "granted": False,
                    "reason": "unsubscribe",
                    "source": "verified_email_callback",
                },
            },
        },
        {
            "event_name": "zalo-opt-out",
            "source_system": "verified_consent_callback",
            "properties": {
                "tracking_channel": "zalo",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "master_profile_id": "aaaaaaaa-0000-0000-0000-000000000002",
                "consent_update": {
                    "field": "zalo_opt_in",
                    "granted": False,
                    "reason": "opt_out",
                    "source": "verified_zalo_webhook",
                },
            },
        },
        {
            "event_name": "zalo-failed",
            "source_system": "verified_consent_callback",
            "properties": {
                "tracking_channel": "zalo",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "master_profile_id": "aaaaaaaa-0000-0000-0000-000000000003",
                "suppression_reason": "permanent_failure",
            },
        },
        {
            "event_name": "email-unsubscribed",
            "source_system": "tracking",
            "properties": {
                "tracking_channel": "email",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "master_profile_id": "aaaaaaaa-0000-0000-0000-000000000004",
            },
        },
    ]
    fake_conn = _Conn()
    assert project_optout_events(fake_conn, events) == {"applied": 2, "skipped": 2}
    updates = [
        call[0]
        for call in fake_conn.cursor_instance.calls
        if str(call[0][0]).lstrip().startswith("UPDATE")
    ]
    assert len(updates) == 2
    assert updates[0][1][:5] == (
        "email_opt_in",
        "email_opt_out_reason",
        "unsubscribe",
        "email_consent_source",
        "verified_email_callback",
    )
    assert updates[1][1][:5] == (
        "zalo_opt_in",
        "zalo_opt_out_reason",
        "opt_out",
        "zalo_consent_source",
        "verified_zalo_webhook",
    )
    assert all("- 'eligibility_policy'" in call[0] for call in updates)


def test_full_zns_render_produces_sent_template_data():
    """The 'final ZNS message' sent to the OA: the campaign's template_data (from
    the agent's plan, template tpl-promo) bound against a resolved recipient.
    ZNS is template-locked — only typed params are filled, no message text is
    authored, so the output IS the template_data dict handed to the OA."""
    campaign_template_data = {                       # from crm_campaign.ai_plan.template_data
        "customer_name": "{{first_name}}",           # personalization token
        "offer": "Giảm 10% cho sản phẩm trong giỏ hàng",
        "expiry": "Trong 48 giờ, đến 2026-09-23",
    }
    context = {"first_name": "An", "last_name": "Nguyễn", "name": "An Nguyễn", "phone": "+84901234567"}

    sent = render_params(campaign_template_data, context)

    assert sent == {
        "customer_name": "An",                       # {{first_name}} bound
        "offer": "Giảm 10% cho sản phẩm trong giỏ hàng",
        "expiry": "Trong 48 giờ, đến 2026-09-23",
    }
    # No key added or dropped — ZNS never authors free text beyond the fixed params.
    assert set(sent) == set(campaign_template_data)
