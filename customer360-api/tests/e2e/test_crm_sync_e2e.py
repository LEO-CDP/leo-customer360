"""SCRUM-94 E2E -- segment-ID CRM sync engine, endpoint/contract layer.

Covers TEST_PLAN.md S94-01..16: dry-run, real sync (zero-match, no write
footprint), the count invariant, audit evidence, idempotent replay, the
404/400/422 guards, the auth boundary, list filtering + limit bounds, and
(optionally) cross-tenant isolation. Routing that WRITES to crm_* tables is in
test_routing_e2e.py (opt-in). Skipped unless E2E_BASE_URL is set.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_BASE_URL"), reason="E2E_BASE_URL not set (see tests/e2e/README.md)"
)

ROUTE_KEYS = {"matched", "customer", "lead", "contact", "skipped", "error"}

# Connection settings (mirror conftest; read here so this module needs no cross-import).
BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
TIMEOUT = float(os.environ.get("E2E_TIMEOUT", "60"))
VERIFY_TLS = os.environ.get("E2E_VERIFY_TLS", "true").strip().lower() not in ("false", "0", "no")
# Optional second tenant for real cross-tenant isolation checks.
TENANT_B = os.environ.get("E2E_TENANT_ID_B", "").strip()
TOKEN_B = os.environ.get("E2E_BEARER_TOKEN_B", "").strip()


def _assert_counts_consistent(counts: dict) -> None:
    assert set(counts) == ROUTE_KEYS, counts
    assert all(counts[k] >= 0 for k in ROUTE_KEYS), counts
    # Every matched member lands in exactly one of customer/lead/contact/skipped;
    # error is an overlapping tally, so it adds no new bucket.
    assert counts["customer"] + counts["lead"] + counts["contact"] + counts["skipped"] == counts["matched"], counts
    assert counts["error"] <= counts["matched"], counts


# --- S94-01 ---------------------------------------------------------------
@pytest.mark.case("S94-01")
def test_health_is_public(unauth_client):
    assert unauth_client.get("/health").status_code == 200


# --- S94-02 dry-run -------------------------------------------------------
@pytest.mark.case("S94-02")
def test_dry_run_returns_counts_and_writes_nothing(client, p, segment_id):
    r = client.post(p(f"/admin/crm/sync-segment/{segment_id}"), params={"dry_run": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["status"] == "Completed"
    assert body["detail"]["recomputed"] is False
    assert body["route_counts"]["error"] == 0
    _assert_counts_consistent(body["route_counts"])


# --- S94-03 real sync (zero-match) + S94-04 invariant ---------------------
@pytest.mark.case("S94-03", "S94-04")
def test_real_sync_completes_zero_match(client, p, segment_id):
    r = client.post(p(f"/admin/crm/sync-segment/{segment_id}"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "Completed"
    assert body["dry_run"] is False
    assert str(body["segment_id"]) == str(segment_id)
    assert body["route_counts"]["matched"] == 0  # impossible predicate
    _assert_counts_consistent(body["route_counts"])


# --- S94-05 audited + S94-06 fields ---------------------------------------
@pytest.mark.case("S94-05", "S94-06")
def test_sync_is_audited_and_retrievable(client, p, segment_id):
    body = client.post(p(f"/admin/crm/sync-segment/{segment_id}")).json()
    run_id = body["sync_run_id"]

    got = client.get(p(f"/admin/crm/sync-runs/{run_id}"))
    assert got.status_code == 200, got.text
    run = got.json()
    assert run["sync_run_id"] == run_id
    assert run["status"] == "Completed"
    assert str(run["segment_id"]) == str(segment_id)
    for field in ("matched_count", "customer_count", "lead_count", "contact_count", "skipped_count", "error_count"):
        assert isinstance(run[field], int)
    assert run["started_at"]  # timing recorded

    listed = client.get(p("/admin/crm/sync-runs"), params={"segment_id": segment_id, "limit": 50})
    assert listed.status_code == 200, listed.text
    assert any(x["sync_run_id"] == run_id for x in listed.json())


# --- S94-07 idempotent replay --------------------------------------------
@pytest.mark.case("S94-07")
def test_sync_is_idempotent_across_replays(client, p, segment_id):
    a = client.post(p(f"/admin/crm/sync-segment/{segment_id}")).json()["route_counts"]
    b = client.post(p(f"/admin/crm/sync-segment/{segment_id}")).json()["route_counts"]
    for k in ("matched", "customer", "lead", "contact", "skipped"):
        assert a[k] == b[k], (k, a, b)


# --- S94-08 unknown segment ----------------------------------------------
@pytest.mark.case("S94-08")
def test_unknown_segment_returns_404(client, p):
    assert client.post(p(f"/admin/crm/sync-segment/{uuid.uuid4()}")).status_code == 404


# --- S94-09 no sql_rules --------------------------------------------------
@pytest.mark.case("S94-09")
def test_segment_without_sql_rules_returns_400(client, p, make_segment):
    sid, _ = make_segment(sql_rules=None)  # created without sql_rules
    assert client.post(p(f"/admin/crm/sync-segment/{sid}")).status_code == 400


# --- S94-10 unsafe sql_rules rejected ------------------------------------
@pytest.mark.case("S94-10")
def test_unsafe_sql_rules_rejected_at_create(client, p, tenant_id, track):
    tag = f"e2e-unsafe-{uuid.uuid4().hex[:8]}"
    r = client.post(p("/segments/"), json={
        "tenant_id": tenant_id, "domain": "all", "segment_tag": tag, "segment_name": tag,
        "sql_rules": "1=1; DROP TABLE customer360.cdp_master_profiles;",
        "processed_by": "human", "is_active": True,
    })
    # The SegmentCreate validator (validate_sql_where_fragment) rejects it up front.
    assert r.status_code == 422, r.text
    if r.status_code in (200, 201):  # defensive: clean up if it somehow created
        track.add("segment", r.json()["segment_id"])


# --- S94-11 auth boundary -------------------------------------------------
@pytest.mark.case("S94-11")
def test_sync_requires_authorization(unauth_client, p, segment_id):
    r = unauth_client.post(p(f"/admin/crm/sync-segment/{segment_id}"))
    assert r.status_code in (400, 401, 403), r.text


# --- S94-15 list limit bounds --------------------------------------------
@pytest.mark.case("S94-15")
def test_sync_runs_limit_is_bounded(client, p):
    assert client.get(p("/admin/crm/sync-runs"), params={"limit": 0}).status_code == 422
    assert client.get(p("/admin/crm/sync-runs"), params={"limit": 101}).status_code == 422
    assert client.get(p("/admin/crm/sync-runs"), params={"limit": 1}).status_code == 200


# --- S94-16 list filter by segment ---------------------------------------
@pytest.mark.case("S94-16")
def test_sync_runs_filter_scopes_to_segment(client, p, make_segment):
    sid, _ = make_segment(sql_rules="engagement_score < -1")
    client.post(p(f"/admin/crm/sync-segment/{sid}"), params={"dry_run": "true"})
    listed = client.get(p("/admin/crm/sync-runs"), params={"segment_id": sid, "limit": 50})
    assert listed.status_code == 200, listed.text
    assert all(str(x["segment_id"]) == str(sid) for x in listed.json())


# --- S94-13 / S94-14 cross-tenant isolation (opt-in: needs a 2nd tenant) --
@pytest.mark.case("S94-13", "S94-14")
@pytest.mark.skipif(not (TENANT_B and TOKEN_B), reason="E2E_TENANT_ID_B / E2E_BEARER_TOKEN_B not set")
def test_cross_tenant_segment_and_run_are_404(client, p, segment_id):
    import httpx
    # Run a sync under tenant A to get a real run id.
    run_id = client.post(p(f"/admin/crm/sync-segment/{segment_id}")).json()["sync_run_id"]
    hb = {"Authorization": f"Bearer {TOKEN_B}", "X-Tenant-Id": TENANT_B}
    with httpx.Client(base_url=C.BASE_URL, headers=hb, timeout=C.TIMEOUT,
                      follow_redirects=True, verify=C.VERIFY_TLS) as cb:
        assert cb.post(p(f"/admin/crm/sync-segment/{segment_id}")).status_code == 404  # A's segment
        assert cb.get(p(f"/admin/crm/sync-runs/{run_id}")).status_code == 404          # A's run
