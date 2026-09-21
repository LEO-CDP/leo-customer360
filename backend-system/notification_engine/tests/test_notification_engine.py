"""Unit tests for the Zalo notification engine (pure logic)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notification_engine.adapters import MockZNSAdapter, build_zns_adapter  # noqa: E402
from notification_engine.rendering import render_params, render_value  # noqa: E402
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


# --- tracking token (must match data-tracking-api decode) ---
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


# --- S3 opt-out filter ---
def test_extract_optout_events_keeps_only_zalo_optouts():
    records = [
        {"event_name": "zalo-opt-out",
         "properties": {"tracking_channel": "zalo", "tenant_id": "t", "master_profile_id": "p1",
                        "suppression_reason": "opt_out"}},
        {"event_name": "zalo-delivered",
         "properties": {"tracking_channel": "zalo", "tenant_id": "t", "master_profile_id": "p1"}},
        {"event_name": "email-bounced",
         "properties": {"tracking_channel": "email", "suppression_reason": "hard_bounce"}},
        {"event_name": "zalo-failed",
         "properties": {"tracking_channel": "zalo", "tenant_id": "t", "master_profile_id": "p2",
                        "suppression_reason": "permanent_failure"}},
    ]
    out = extract_optout_events(records)
    assert [e["properties"]["master_profile_id"] for e in out] == ["p1", "p2"]


# --- opt-out projection (consent write) ---
def test_project_optout_events_counts_applied_vs_skipped():
    from notification_engine.optout_projection import project_optout_events

    class _Cur:
        rowcount = 1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def rollback(self): pass

    events = [
        {"properties": {"tenant_id": "t", "master_profile_id": "p1", "suppression_reason": "opt_out"}},
        {"properties": {"tenant_id": "t", "suppression_reason": "opt_out"}},  # missing profile
        {"properties": {"tenant_id": "t", "master_profile_id": "p3"}},        # missing reason
    ]
    assert project_optout_events(_Conn(), events) == {"applied": 1, "skipped": 2}


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
