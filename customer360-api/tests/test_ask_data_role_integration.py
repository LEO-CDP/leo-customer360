"""Privileges of the restricted `customer360_ask_data` role, tested on the seeded
`customer360_agents_test` database. This role is the real security boundary for
"ask your data": Postgres, not the application, must refuse personal columns,
unlisted tables, writes and other tenants' rows. Skipped unless
ASK_DATA_TEST_DATABASE_URL is set."""

import os
from contextlib import contextmanager

import pytest

sa = pytest.importorskip("sqlalchemy")

URL = os.environ.get("ASK_DATA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="ASK_DATA_TEST_DATABASE_URL not set")

ROLE = "customer360_ask_data"
DEMO_TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"

# Columns that identify a person or hold contact details. None may ever be readable by the role.
PERSONAL_COLUMNS = {
    "email", "phone_number", "full_name", "first_name", "last_name", "address", "date_of_birth",
    "secondary_emails", "secondary_phones", "national_id", "external_ids", "device_ids",
    "advertising_ids", "cookie_ids", "push_tokens", "staff_name", "ip_address", "user_id",
}


@pytest.fixture(scope="module")
def engine():
    assert URL.rstrip("/").endswith("customer360_agents_test"), "refusing to run outside the test database"
    eng = sa.create_engine(URL, future=True)
    yield eng
    eng.dispose()


@contextmanager
def as_role(engine, tenant=DEMO_TENANT, read_only=True):
    """A rolled-back transaction running as the restricted role, with the tenant set."""
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            if read_only:
                conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            conn.execute(sa.text(f"SET LOCAL ROLE {ROLE}"))
            conn.execute(sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
            yield conn
        finally:
            trans.rollback()


def _scalar(conn, sql):
    return conn.execute(sa.text(sql)).scalar()


def test_the_role_exists_and_cannot_log_in_bypass_rls_or_own_anything(engine):
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = :r"),
            {"r": ROLE},
        ).one()

    assert tuple(row) == (False, False, False, False, False)


def test_it_reads_the_tenants_own_rows(engine):
    with as_role(engine) as conn:
        assert _scalar(conn, "SELECT COUNT(*) FROM customer360.cdp_master_profiles") > 0
        assert _scalar(conn, "SELECT COUNT(*) FROM customer360.crm_transactions") > 0


def test_another_tenant_sees_no_rows(engine):
    with as_role(engine, tenant=OTHER_TENANT) as conn:
        assert _scalar(conn, "SELECT COUNT(*) FROM customer360.cdp_master_profiles") == 0
        assert _scalar(conn, "SELECT COUNT(*) FROM customer360.crm_transactions") == 0


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT email FROM customer360.cdp_master_profiles LIMIT 1",
        "SELECT phone_number FROM customer360.cdp_master_profiles LIMIT 1",
        "SELECT full_name FROM customer360.cdp_master_profiles LIMIT 1",
        "SELECT staff_name FROM customer360.crm_transactions LIMIT 1",
        "SELECT * FROM customer360.cdp_master_profiles LIMIT 1",
        "SELECT user_id FROM customer360.crm_campaign LIMIT 1",
    ],
    ids=["email", "phone", "full_name", "staff_name", "select_star", "staff_user_id"],
)
def test_personal_columns_are_refused_by_postgres(engine, sql):
    with as_role(engine) as conn, pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
        conn.execute(sa.text(sql))


@pytest.mark.parametrize(
    "table",
    [
        "sys_user", "sys_userinfo", "sys_role", "crm_connector_config", "crm_contact", "crm_lead",
        "cdp_raw_profiles_stage", "cdp_identity_index", "cdp_profile_links", "sys_audit_log",
        "cdp_persona_features", "cdp_persona_history", "cdp_persona_score_details",
    ],
)
def test_tables_outside_the_allow_list_are_refused(engine, table):
    with as_role(engine) as conn, pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
        conn.execute(sa.text(f"SELECT 1 FROM customer360.{table} LIMIT 1"))


WRITES = [
    "DELETE FROM customer360.cdp_segments",
    "UPDATE customer360.cdp_segments SET segment_name = 'x'",
    "INSERT INTO customer360.cdp_segments (segment_id) VALUES (gen_random_uuid())",
]


@pytest.mark.parametrize("sql", WRITES)
def test_a_read_only_transaction_refuses_writes(engine, sql):
    with as_role(engine) as conn, pytest.raises(sa.exc.InternalError, match="read-only transaction"):
        conn.execute(sa.text(sql))


@pytest.mark.parametrize("sql", WRITES)
def test_the_role_has_no_write_privilege_even_in_a_read_write_transaction(engine, sql):
    """Defence in depth: if a caller forgot to make the transaction read-only, Postgres still refuses."""
    with as_role(engine, read_only=False) as conn, pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
        conn.execute(sa.text(sql))


def test_every_readable_table_has_forced_row_level_security_and_a_tenant_column(engine):
    """The invariant behind tenant isolation: a table is only readable if Postgres filters it by tenant."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                          EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                                  AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS has_tenant
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'customer360' AND c.relkind IN ('r', 'p')
                     AND has_any_column_privilege(:r, c.oid, 'SELECT')"""
            ),
            {"r": ROLE},
        ).all()

    assert len(rows) >= 10, "the role should be able to read a useful set of tables"
    unprotected = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity and r.has_tenant)]
    assert unprotected == [], f"readable tables without forced RLS and tenant_id: {unprotected}"


def test_no_personal_column_is_readable_on_any_table(engine):
    with engine.connect() as conn:
        readable = conn.execute(
            sa.text(
                """SELECT c.relname || '.' || a.attname
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                   WHERE n.nspname = 'customer360' AND c.relkind IN ('r', 'p')
                     AND has_column_privilege(:r, c.oid, a.attnum, 'SELECT')"""
            ),
            {"r": ROLE},
        ).scalars().all()

    leaked = sorted(name for name in readable if name.split(".")[1] in PERSONAL_COLUMNS)
    assert leaked == [], f"personal columns readable by {ROLE}: {leaked}"
    assert len(readable) > 50, "the role should see a useful set of columns"
