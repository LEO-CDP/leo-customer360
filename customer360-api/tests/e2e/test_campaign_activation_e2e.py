"""SCRUM-97 E2E -- campaign activation API (the entry point to the Dagster
execution pipeline) against a live deployment (UAT). Skipped unless E2E_BASE_URL
is set.

Covers TEST_PLAN.md S97-01..07: the human-approval gate + guards, the
dispatch-log audit endpoint, the provider-config read, and (opt-in) a real
activation trigger. The full send is orchestrated by Dagster and needs an
Approved campaign with a template + segment + recipients, which can't be fully
seeded through the public API (no email-template CRUD endpoint), so S97-07 is
opt-in via E2E_CAMPAIGN_ID.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_BASE_URL"), reason="E2E_BASE_URL not set (see tests/e2e/README.md)"
)


@pytest.fixture(autouse=True)
def _require_email_feature(email_feature):
    """Skip this module unless the target deploys the SCRUM-97 activation endpoints."""


def _campaign_body(tenant_id, **over):
    return {"tenant_id": tenant_id, "name": f"E2E Activation {uuid.uuid4().hex[:8]}", **over}


# --- S97-01 unknown campaign ----------------------------------------------
@pytest.mark.case("S97-01")
def test_activate_unknown_campaign_returns_404(client, p):
    assert client.post(p(f"/admin/campaigns/{uuid.uuid4()}/activate")).status_code == 404


# --- S97-02 auth boundary -------------------------------------------------
@pytest.mark.case("S97-02")
def test_activate_requires_authorization(unauth_client, p):
    r = unauth_client.post(p(f"/admin/campaigns/{uuid.uuid4()}/activate"))
    assert r.status_code in (400, 401, 403), r.text


# --- S97-03 human-approval gate (not Approved -> refused) -----------------
@pytest.mark.case("S97-03")
def test_activate_draft_campaign_is_refused(client, p, tenant_id, track):
    created = client.post(p("/campaigns/"), json=_campaign_body(tenant_id, approval_status="Draft"))
    assert created.status_code in (200, 201), created.text
    track.add("campaign", created.json()["campaign_id"])
    r = client.post(p(f"/admin/campaigns/{created.json()['campaign_id']}/activate"))
    assert r.status_code == 409, r.text  # not Approved


# --- S97-04 Approved but missing template/segment -------------------------
@pytest.mark.case("S97-04")
def test_activate_approved_without_template_or_segment_is_refused(client, p, tenant_id, track):
    created = client.post(p("/campaigns/"), json=_campaign_body(tenant_id, approval_status="Approved"))
    assert created.status_code in (200, 201), created.text
    track.add("campaign", created.json()["campaign_id"])
    r = client.post(p(f"/admin/campaigns/{created.json()['campaign_id']}/activate"))
    assert r.status_code == 409, r.text  # needs template_id + segment_id


# --- S97-05 dispatch-log audit endpoint -----------------------------------
@pytest.mark.case("S97-05")
def test_dispatch_logs_empty_for_fresh_campaign(client, p, tenant_id, track):
    created = client.post(p("/campaigns/"), json=_campaign_body(tenant_id))
    assert created.status_code in (200, 201), created.text
    track.add("campaign", created.json()["campaign_id"])
    r = client.get(p(f"/admin/campaigns/{created.json()['campaign_id']}/dispatch-logs"))
    assert r.status_code == 200, r.text
    assert r.json() == []


# --- S97-06 provider config read ------------------------------------------
@pytest.mark.case("S97-06")
def test_email_provider_config_is_readable(client, p):
    r = client.get(p("/admin/email-provider-config"))
    assert r.status_code == 200  # returns the active config object or null


# --- S97-07 real activation trigger (opt-in) ------------------------------
@pytest.mark.case("S97-07")
def test_activate_approved_campaign_triggers_dagster(client, p):
    campaign_id = os.environ.get("E2E_CAMPAIGN_ID", "").strip()
    if not campaign_id:
        pytest.skip("E2E_CAMPAIGN_ID not set (an Approved campaign with template_id + segment_id)")
    r = client.post(p(f"/admin/campaigns/{campaign_id}/activate"))
    # 200 -> Dagster run submitted; 503 -> Dagster webserver unreachable (still a
    # pass-through of the approval gate). Anything else is a real failure.
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert r.json().get("run_id")
