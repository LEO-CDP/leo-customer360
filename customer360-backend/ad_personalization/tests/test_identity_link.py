"""Tests for the cross-device identity link job.

Derivation, vectors and the pure grouping logic always run. The database group is
opt-in (see tests/conftest.py); it builds a platform tenant, master profiles,
links and raw profiles inside a rolled-back transaction.
"""

import json
import uuid
from pathlib import Path

import pytest
from conftest import SCHEMA, requires_db

from ad_personalization import identity_link as il

VECTORS_PATH = (
    Path(__file__).resolve().parents[3]
    / "specs" / "003-ad-personalization-engine" / "contracts" / "visitor-key-test-vectors.json"
)
SECRET = "unit-test-secret"


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------


def _vectors():
    return json.loads(VECTORS_PATH.read_text())


def test_derivation_matches_the_shared_vectors():
    data = _vectors()
    assert data["vectors"], "no vectors"
    for vector in data["vectors"]:
        assert il.tenant_secret(vector["master_secret"], vector["tenant_id"]).hex() == vector["tenant_secret_hex"]
        key = il.visitor_key(vector["tenant_id"], vector["leovid"], vector["master_secret"])
        assert key.hex() == vector["expected_key_hex"]


def test_unusable_visitor_ids_are_rejected_like_the_other_services():
    data = _vectors()
    for bad in data["invalid_visitor_ids"] + ["x" * (data["max_visitor_id_length"] + 1)]:
        with pytest.raises(ValueError):
            il.visitor_key(1, bad, SECRET)


def test_no_secret_outside_development_means_no_keys(monkeypatch):
    for name in ("AD_VISITOR_KEY_MASTER_SECRET", "c360_PROMOTION_VISITOR_KEY_MASTER_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("c360_PROMOTION_ENVIRONMENT", "production")
    assert il.configured_master_secret() is None
    with pytest.raises(il.VisitorKeyUnavailable):
        il.visitor_key(1, "a")
    monkeypatch.setenv("c360_PROMOTION_ENVIRONMENT", "development")
    assert il.configured_master_secret() == il._DEVELOPMENT_SECRET


def test_env_secret_is_used_and_the_api_name_wins(monkeypatch):
    monkeypatch.setenv("c360_PROMOTION_VISITOR_KEY_MASTER_SECRET", "from-ad-server")
    assert il.configured_master_secret() == "from-ad-server"
    monkeypatch.setenv("AD_VISITOR_KEY_MASTER_SECRET", "from-api")
    assert il.configured_master_secret() == "from-api"


def test_all_cookie_ids_of_a_master_share_one_reference():
    links, ambiguous, invalid = il.resolve_cookie_keys(
        [("m1", "cookie-a"), ("m1", "cookie-b"), ("m2", "cookie-c")], 7, SECRET
    )
    assert ambiguous == invalid == 0
    refs = {il.visitor_key(7, c, SECRET): m for c, m in (("cookie-a", "m1"), ("cookie-b", "m1"), ("cookie-c", "m2"))}
    assert links == refs


def test_a_cookie_on_two_masters_is_ambiguous_and_linked_to_neither():
    links, ambiguous, _ = il.resolve_cookie_keys([("m1", "shared"), ("m2", "shared"), ("m1", "own")], 7, SECRET)
    assert ambiguous == 1
    assert list(links.values()) == ["m1"]
    assert il.visitor_key(7, "shared", SECRET) not in links


def test_unusable_cookie_ids_are_counted_not_fatal():
    links, _, invalid = il.resolve_cookie_keys([("m1", "  "), ("m1", "ok")], 7, SECRET)
    assert invalid == 1 and len(links) == 1


def test_same_cookie_gives_different_keys_per_tenant():
    assert il.visitor_key(1, "c", SECRET) != il.visitor_key(2, "c", SECRET)


# --------------------------------------------------------------------------
# Database (opt-in)
# --------------------------------------------------------------------------

UUID_RE = "^[0-9a-f-]{36}$"


class Platform:
    """A platform tenant with helpers to build master profiles and links."""

    def __init__(self, conn, ad_tenant_id):
        self.conn = conn
        self.ad_tenant_id = ad_tenant_id
        code = "it-" + uuid.uuid4().hex[:10]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customer360.sys_tenant (tenant_code, tenant_name, company_name, business_type) "
                "VALUES (%s, %s, %s, 'retail') RETURNING tenant_id::text",
                (code, code, code),
            )
            self.tenant_id = cur.fetchone()[0]
            cur.execute(
                f"UPDATE {SCHEMA}.tenant SET customer360_tenant_id = %s::uuid WHERE tenant_id = %s",
                (self.tenant_id, ad_tenant_id),
            )

    def master(self, cookies):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customer360.cdp_master_profiles (tenant_id, cookie_ids) "
                "VALUES (%s::uuid, %s) RETURNING master_profile_id::text",
                (self.tenant_id, list(cookies)),
            )
            return cur.fetchone()[0]

    def link(self, master_id, cookie, status="ACTIVE"):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customer360.cdp_raw_profiles_stage (tenant_id, source_system, cookie_id) "
                "VALUES (%s::uuid, 'test', %s) RETURNING raw_profile_id",
                (self.tenant_id, cookie),
            )
            raw_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO customer360.cdp_profile_links (tenant_id, raw_profile_id, master_profile_id, status) "
                "VALUES (%s::uuid, %s, %s::uuid, %s)",
                (self.tenant_id, raw_id, master_id, status),
            )

    def set_link_status(self, cookie, status):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE customer360.cdp_profile_links l SET status = %s FROM customer360.cdp_raw_profiles_stage r "
                "WHERE r.raw_profile_id = l.raw_profile_id AND r.cookie_id = %s AND l.tenant_id = %s::uuid",
                (status, cookie, self.tenant_id),
            )


@pytest.fixture
def platform(db, tenant_factory):
    with db.cursor() as cur:
        cur.execute("SELECT to_regclass('customer360.cdp_profile_links'), to_regclass('customer360.cdp_master_profiles')")
        if None in cur.fetchone():
            pytest.skip("customer360.cdp_master_profiles / cdp_profile_links are not in this database")
        cur.execute(f"SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
                    "AND table_name = 'tenant' AND column_name = 'customer360_tenant_id'", (SCHEMA,))
        if cur.fetchone() is None:
            pytest.skip("personalization-schema.sql has not been applied to this database")

    def make():
        tenant = tenant_factory()
        tenant.settings(consent_mode="opt_out")
        return tenant, Platform(db, tenant.tenant_id)

    return make


def _run(db):
    return il.link_identities(db, schema=SCHEMA, master_secret=SECRET, log=lambda _m: None)


def _refs(db, tenant_id):
    with db.cursor() as cur:
        cur.execute(
            f"SELECT user_key_hash, profile_ref FROM {SCHEMA}.user_serving_key WHERE tenant_id = %s", (tenant_id,)
        )
        return {bytes(r[0]): r[1] for r in cur.fetchall()}


@requires_db
def test_all_cookie_ids_of_a_master_profile_share_one_profile_ref(db, platform):
    tenant, plat = platform()
    master = plat.master(["phone-cookie", "laptop-cookie"])
    plat.link(master, "phone-cookie")
    plat.link(master, "laptop-cookie")

    summary = _run(db)

    refs = _refs(db, tenant.tenant_id)
    assert refs == {
        il.visitor_key(tenant.tenant_id, "phone-cookie", SECRET): master,
        il.visitor_key(tenant.tenant_id, "laptop-cookie", SECRET): master,
    }
    assert summary[tenant.tenant_id]["linked"] == 2


@requires_db
def test_a_second_run_changes_nothing(db, platform):
    tenant, plat = platform()
    master = plat.master(["a-cookie"])
    plat.link(master, "a-cookie")
    _run(db)

    second = _run(db)

    assert second[tenant.tenant_id] == {"linked": 0, "unlinked": 0, "ambiguous": 0, "invalid": 0, "foreign": 0}


@requires_db
def test_unlinking_clears_the_reference_and_keeps_the_row(db, platform):
    tenant, plat = platform()
    master = plat.master(["a-cookie", "b-cookie"])
    plat.link(master, "a-cookie")
    plat.link(master, "b-cookie")
    _run(db)
    key_a = il.visitor_key(tenant.tenant_id, "a-cookie", SECRET)
    key_b = il.visitor_key(tenant.tenant_id, "b-cookie", SECRET)

    for status in ("UNLINKED", "HISTORICAL", "SUPERSEDED"):
        plat.set_link_status("a-cookie", status)
        summary = _run(db)
        refs = _refs(db, tenant.tenant_id)
        assert refs[key_a] is None, status
        assert refs[key_b] == master, status  # the other device is untouched
        assert summary[tenant.tenant_id]["unlinked"] in (0, 1)
        plat.set_link_status("a-cookie", "ACTIVE")
        _run(db)
        assert _refs(db, tenant.tenant_id)[key_a] == master


@requires_db
def test_relinking_to_another_master_reassigns_the_reference(db, platform):
    tenant, plat = platform()
    first = plat.master(["c-cookie"])
    plat.link(first, "c-cookie")
    _run(db)

    plat.set_link_status("c-cookie", "UNLINKED")
    second = plat.master(["c-cookie"])
    plat.link(second, "c-cookie")
    plat.set_link_status("c-cookie", "UNLINKED")  # the first master's link stays inactive
    with db.cursor() as cur:
        cur.execute(
            "UPDATE customer360.cdp_profile_links SET status = 'ACTIVE' WHERE master_profile_id = %s::uuid", (second,)
        )
        cur.execute("UPDATE customer360.cdp_master_profiles SET cookie_ids = '{}' WHERE master_profile_id = %s::uuid", (first,))
    _run(db)

    assert _refs(db, tenant.tenant_id)[il.visitor_key(tenant.tenant_id, "c-cookie", SECRET)] == second


@requires_db
def test_a_master_with_no_active_link_links_nothing(db, platform):
    tenant, plat = platform()
    master = plat.master(["d-cookie"])
    plat.link(master, "d-cookie", status="UNLINKED")

    _run(db)

    assert _refs(db, tenant.tenant_id) == {}


@requires_db
def test_a_cookie_on_two_master_profiles_is_not_linked(db, platform):
    tenant, plat = platform()
    one, two = plat.master(["shared"]), plat.master(["shared"])
    plat.link(one, "shared")
    plat.link(two, "shared")

    summary = _run(db)

    assert _refs(db, tenant.tenant_id) == {}
    assert summary[tenant.tenant_id]["ambiguous"] == 1


@requires_db
def test_a_visitor_with_an_open_erasure_request_is_never_linked(db, platform):
    tenant, plat = platform()
    master = plat.master(["erase-me", "keep-me"])
    plat.link(master, "erase-me")
    plat.link(master, "keep-me")
    erased = il.visitor_key(tenant.tenant_id, "erase-me", SECRET)
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.erasure_request (tenant_id, user_key_hash, status) VALUES (%s, %s, 'pending')",
            (tenant.tenant_id, erased),
        )

    _run(db)

    refs = _refs(db, tenant.tenant_id)
    assert erased not in refs
    assert refs[il.visitor_key(tenant.tenant_id, "keep-me", SECRET)] == master


@requires_db
def test_other_data_on_the_row_survives_and_non_master_references_are_not_ours(db, platform):
    tenant, plat = platform()
    key = il.visitor_key(tenant.tenant_id, "e-cookie", SECRET)
    foreign_key = b"\xee" * 16
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.user_serving_key (user_key_hash, tenant_id, profile_ref, attributes) "
            "VALUES (%s, %s, NULL, %s), (%s, %s, 'demo-user-001', '{}')",
            (key, tenant.tenant_id, json.dumps({"device": "mobile"}), foreign_key, tenant.tenant_id),
        )
    master = plat.master(["e-cookie"])
    plat.link(master, "e-cookie")

    _run(db)
    plat.set_link_status("e-cookie", "UNLINKED")
    _run(db)

    with db.cursor() as cur:
        cur.execute(f"SELECT attributes FROM {SCHEMA}.user_serving_key WHERE user_key_hash = %s", (key,))
        assert cur.fetchone()[0] == {"device": "mobile"}
    assert _refs(db, tenant.tenant_id)[foreign_key] == "demo-user-001"


@requires_db
def test_tenants_never_mix(db, platform):
    first_tenant, first = platform()
    second_tenant, second = platform()
    one = first.master(["same-cookie"])
    first.link(one, "same-cookie")
    two = second.master(["same-cookie"])
    second.link(two, "same-cookie")

    _run(db)

    assert list(_refs(db, first_tenant.tenant_id).values()) == [one]
    assert list(_refs(db, second_tenant.tenant_id).values()) == [two]
    assert set(_refs(db, first_tenant.tenant_id)) != set(_refs(db, second_tenant.tenant_id))


@requires_db
def test_a_key_owned_by_another_ad_tenant_is_left_alone(db, platform):
    first_tenant, first = platform()
    other_tenant, _other = platform()
    key = il.visitor_key(first_tenant.tenant_id, "clash", SECRET)
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO {SCHEMA}.user_serving_key (user_key_hash, tenant_id, profile_ref) VALUES (%s, %s, 'theirs')",
            (key, other_tenant.tenant_id),
        )
    master = first.master(["clash"])
    first.link(master, "clash")

    summary = _run(db)

    assert summary[first_tenant.tenant_id]["foreign"] == 1
    assert _refs(db, other_tenant.tenant_id)[key] == "theirs"


@requires_db
def test_tenants_with_personalisation_off_are_skipped(db, platform):
    tenant, plat = platform()
    tenant.settings(enabled=False)
    master = plat.master(["f-cookie"])
    plat.link(master, "f-cookie")

    summary = _run(db)

    assert tenant.tenant_id not in summary
    assert _refs(db, tenant.tenant_id) == {}


@requires_db
def test_without_a_secret_the_job_does_nothing(db, platform, monkeypatch):
    for name in ("AD_VISITOR_KEY_MASTER_SECRET", "c360_PROMOTION_VISITOR_KEY_MASTER_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("c360_PROMOTION_ENVIRONMENT", "production")
    tenant, plat = platform()
    master = plat.master(["g-cookie"])
    plat.link(master, "g-cookie")

    assert il.link_identities(db, schema=SCHEMA, log=lambda _m: None) == {}
    assert _refs(db, tenant.tenant_id) == {}
