"""SCRUM-94 E2E -- Route A/B/C routing that WRITES to crm_* target tables.

OPT-IN: these mutate the shared UAT tenant's CRM tables, so they run only with
E2E_ALLOW_DATA_WRITES=1. Each targets a single existing profile of a given
lifecycle stage via ``sql_rules = master_profile_id = '<uuid>'`` (matches exactly
one), verifies the produced row at its deterministic uuid5 PK, and cleans up by
deleting ONLY the rows it created (pre-existence-checked) -- the ephemeral segment
is auto-deleted by the ``make_segment`` fixture, cascading its sync-run rows.

Covers TEST_PLAN.md R-A1..R-D1. Skipped when E2E_BASE_URL is unset or writes not
allowed, and per-test when no profile of the needed stage exists on the target.
"""

import os

import pytest

# Reuse the engine's own deterministic-key + routing helpers so the test computes
# the exact PKs the sync writes (and thus can delete precisely).
from core.crud.crm_sync import (
    DEFAULT_LEAD_SOURCE_NAME,
    _clean,
    _deterministic_id,
    _has_identity,
)

pytestmark = [
    pytest.mark.skipif(not os.environ.get("E2E_BASE_URL"), reason="E2E_BASE_URL not set"),
    pytest.mark.skipif(
        os.environ.get("E2E_ALLOW_DATA_WRITES", "").strip().lower() not in ("1", "true", "yes"),
        reason="routing tests write to crm_* tables; set E2E_ALLOW_DATA_WRITES=1 to enable",
    ),
]


def _find_profile(client, p, stage):
    r = client.get(p("/master-profiles"), params={"lifecycle_stage": stage, "page_size": 1})
    if r.status_code != 200:
        return None
    items = r.json().get("items") or []
    return items[0] if items else None


def _exists(client, p, kind_path, rid) -> bool:
    return client.get(p(f"{kind_path}/{rid}")).status_code == 200


def _sync(client, p, sid) -> dict:
    r = client.post(p(f"/admin/crm/sync-segment/{sid}"))
    assert r.status_code == 200, r.text
    return r.json()


def _segment_for(make_segment, mpid):
    # neutralize=True: teardown strips the ephemeral segment_tag the sync's
    # synchronous recompute applied to this real profile before deleting.
    return make_segment(sql_rules=f"master_profile_id = '{mpid}'", neutralize=True)[0]


# --- R-A1 / R-A2: customer -> crm_customer_contacts (no fabricated tx) -----
@pytest.mark.case("R-A1", "R-A2")
def test_route_a_customer(client, p, tenant_id, make_segment, track):
    prof = _find_profile(client, p, "customer")
    if not prof:
        pytest.skip("no active 'customer' profile on target")
    mpid = str(prof["master_profile_id"])
    sid = _segment_for(make_segment, mpid)
    res = _sync(client, p, sid)
    counts, detail = res["route_counts"], res["detail"]
    if counts["matched"] == 0:
        pytest.skip("target profile not active/matchable")
    assert counts["matched"] == 1
    assert counts["customer"] == 1
    # R-A2: amounts are never fabricated -- a profile with no transaction facts
    # writes zero transactions.
    if detail.get("transactions_written", 0) != 0:
        pytest.skip("profile carries transaction facts; precise tx cleanup out of scope")
    cc_id = str(_deterministic_id(tenant_id, "customer_contact", sid, mpid))
    if detail.get("customer_contacts_written", 0) >= 1:
        assert _exists(client, p, "/customer-contacts", cc_id)
        track.add("customer-contact", cc_id)  # keyed by ephemeral segment -> safe to delete


# --- R-B1 / R-B2 / R-B3: lead -> crm_lead + crm_lead_source ---------------
@pytest.mark.case("R-B1", "R-B2", "R-B3")
def test_route_b_lead(client, p, tenant_id, make_segment, track):
    prof = _find_profile(client, p, "lead")
    if not prof:
        pytest.skip("no active 'lead' profile on target")
    mpid = str(prof["master_profile_id"])
    lead_id = str(_deterministic_id(tenant_id, "lead", mpid))
    source_name = (_clean(prof.get("acquisition_source")) or DEFAULT_LEAD_SOURCE_NAME).strip().lower()
    src_id = str(_deterministic_id(tenant_id, "lead_source", source_name))
    lead_pre, src_pre = _exists(client, p, "/leads", lead_id), _exists(client, p, "/lead-sources", src_id)

    sid = _segment_for(make_segment, mpid)
    counts = _sync(client, p, sid)["route_counts"]
    if counts["matched"] == 0:
        pytest.skip("target profile not active/matchable")
    assert counts["matched"] == 1

    if _has_identity(prof):
        assert counts["lead"] == 1
        got = client.get(p(f"/leads/{lead_id}"))
        assert got.status_code == 200, got.text
        assert str(got.json()["lead_source_id"]) == src_id  # relation populated
        if not src_pre:
            track.add("lead-source", src_id)  # deleted after the lead (LIFO)
        if not lead_pre:
            track.add("lead", lead_id)
    else:
        # R-B3: no usable identity field -> skipped, nothing written.
        assert counts["skipped"] == 1
        assert not _exists(client, p, "/leads", lead_id)


# --- R-C1: other stage -> crm_contact -------------------------------------
@pytest.mark.case("R-C1")
def test_route_c_contact(client, p, tenant_id, make_segment, track):
    prof = _find_profile(client, p, "prospect")
    if not prof:
        pytest.skip("no active 'prospect' profile on target")
    mpid = str(prof["master_profile_id"])
    contact_id = str(_deterministic_id(tenant_id, "contact", mpid))
    pre = _exists(client, p, "/contacts", contact_id)

    sid = _segment_for(make_segment, mpid)
    counts = _sync(client, p, sid)["route_counts"]
    if counts["matched"] == 0:
        pytest.skip("target profile not active/matchable")
    assert counts["matched"] == 1

    if _has_identity(prof):
        assert counts["contact"] == 1
        assert _exists(client, p, "/contacts", contact_id)
        if not pre:
            track.add("contact", contact_id)
    else:
        assert counts["skipped"] == 1


# --- R-D1: idempotent writes (replay -> same deterministic id, no dup) -----
@pytest.mark.case("R-D1")
def test_route_writes_are_idempotent(client, p, tenant_id, make_segment, track):
    prof = _find_profile(client, p, "prospect")
    if not prof or not _has_identity(prof):
        pytest.skip("no identifiable 'prospect' profile on target")
    mpid = str(prof["master_profile_id"])
    contact_id = str(_deterministic_id(tenant_id, "contact", mpid))
    pre = _exists(client, p, "/contacts", contact_id)

    sid = _segment_for(make_segment, mpid)
    c1 = _sync(client, p, sid)["route_counts"]
    c2 = _sync(client, p, sid)["route_counts"]
    if c1["matched"] == 0:
        pytest.skip("target profile not active/matchable")
    assert c1["contact"] == c2["contact"] == 1  # deterministic across replays
    assert _exists(client, p, "/contacts", contact_id)  # still exactly one row
    if not pre:
        track.add("contact", contact_id)
