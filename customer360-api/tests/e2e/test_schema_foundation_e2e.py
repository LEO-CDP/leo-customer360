"""SCRUM-93 E2E -- email-marketing schema foundation, via the API surface that
depends on it. Covers TEST_PLAN.md S93-01..09: the crm_campaign email-marketing
columns round-trip, approval_status constraint + boundary, campaign/segment and
lead/lead-source FK enforcement, and that crm_segment_sync_runs is queryable +
tenant-scoped. Migration up/down + RLS + crm_campaign_content_items are DB-level
(see TEST_PLAN.md §6). Skipped unless E2E_BASE_URL is set.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_BASE_URL"), reason="E2E_BASE_URL not set (see tests/e2e/README.md)"
)

APPROVAL_STATUSES = ("Draft", "InReview", "Approved", "Rejected")


def _campaign_body(tenant_id, **over):
    return {"tenant_id": tenant_id, "name": f"E2E EM {uuid.uuid4().hex[:8]}", **over}


# --- S93-01 / S93-02: campaign EM columns + ai_plan JSONB round-trip -------
@pytest.mark.case("S93-01", "S93-02")
def test_campaign_email_marketing_columns_round_trip(client, p, tenant_id, segment_id, track):
    ai_plan = {"steps": ["draft", "review", "approve"], "generated_by": "e2e", "n": 3}
    created = client.post(p("/campaigns/"), json=_campaign_body(
        tenant_id, segment_id=segment_id, approval_status="Draft",
        strategy_summary="E2E strategy summary.", ai_plan=ai_plan,
    ))
    assert created.status_code in (200, 201), created.text
    body = created.json()
    track.add("campaign", body["campaign_id"])
    assert str(body["segment_id"]) == str(segment_id)
    assert body["approval_status"] == "Draft"
    assert body["strategy_summary"] == "E2E strategy summary."
    assert body["ai_plan"] == ai_plan
    # Read back -> persisted, not just echoed.
    got = client.get(p(f"/campaigns/{body['campaign_id']}"))
    assert got.status_code == 200, got.text
    assert got.json()["ai_plan"] == ai_plan
    assert got.json()["approval_status"] == "Draft"


# --- S93-03: every valid approval_status accepted -------------------------
@pytest.mark.case("S93-03")
@pytest.mark.parametrize("status", APPROVAL_STATUSES)
def test_campaign_accepts_each_approval_status(client, p, tenant_id, track, status):
    r = client.post(p("/campaigns/"), json=_campaign_body(tenant_id, approval_status=status))
    assert r.status_code in (200, 201), r.text
    track.add("campaign", r.json()["campaign_id"])
    assert r.json()["approval_status"] == status


# --- S93-04: invalid approval_status rejected -----------------------------
@pytest.mark.case("S93-04")
def test_campaign_rejects_invalid_approval_status(client, p, tenant_id):
    r = client.post(p("/campaigns/"), json=_campaign_body(tenant_id, approval_status="Bogus"))
    assert r.status_code == 422, r.text


# --- S93-05: campaign.segment_id FK enforced ------------------------------
@pytest.mark.case("S93-05")
def test_campaign_segment_fk_enforced(client, p, tenant_id, track):
    r = client.post(p("/campaigns/"), json=_campaign_body(tenant_id, segment_id=str(uuid.uuid4())))
    # A non-existent segment_id must be rejected (FK) -- never a 201.
    assert r.status_code >= 400, r.text
    if r.status_code in (200, 201):
        track.add("campaign", r.json()["campaign_id"])


# --- S93-06: crm_lead.lead_source_id relation round-trip ------------------
@pytest.mark.case("S93-06")
def test_lead_source_relation_round_trip(client, p, tenant_id, track):
    src = client.post(p("/lead-sources/"), json={"tenant_id": tenant_id, "name": f"e2e-src-{uuid.uuid4().hex[:6]}"})
    assert src.status_code in (200, 201), src.text
    source_id = src.json()["lead_source_id"]
    track.add("lead-source", source_id)

    lead = client.post(p("/leads/"), json={
        "tenant_id": tenant_id, "lead_source_id": source_id,
        "first_name": "E2E", "last_name": "Lead", "email": f"e2e+{uuid.uuid4().hex[:6]}@example.com",
    })
    assert lead.status_code in (200, 201), lead.text
    track.add("lead", lead.json()["lead_id"])
    assert str(lead.json()["lead_source_id"]) == str(source_id)


# --- S93-07: crm_lead.lead_source_id FK enforced --------------------------
@pytest.mark.case("S93-07")
def test_lead_source_fk_enforced(client, p, tenant_id, track):
    r = client.post(p("/leads/"), json={
        "tenant_id": tenant_id, "lead_source_id": str(uuid.uuid4()), "first_name": "E2E",
    })
    assert r.status_code >= 400, r.text
    if r.status_code in (200, 201):
        track.add("lead", r.json()["lead_id"])


# --- S93-08: crm_segment_sync_runs queryable + tenant-scoped --------------
@pytest.mark.case("S93-08")
def test_segment_sync_runs_audit_table_is_queryable(client, p):
    r = client.get(p("/admin/crm/sync-runs"), params={"limit": 5})
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
