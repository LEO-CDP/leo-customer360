"""Read-only deployment checks for the LEO Ads audience sync."""

import os
from contextlib import closing

import psycopg2

from audience_sync import DB_SCHEMA, _connect


class ReadinessError(RuntimeError):
    """A required database object or permission is missing."""


def _fetch_one(cursor):
    row = cursor.fetchone()
    if row is None:
        raise ReadinessError("Database readiness query returned no result")
    return row


def _check_relations(connection, requirements):
    with connection.cursor() as cursor:
        for relation, privileges in requirements.items():
            cursor.execute("SELECT to_regclass(%s)", (relation,))
            if _fetch_one(cursor)[0] is None:
                raise ReadinessError(f"Missing relation: {relation}")
            for privilege in privileges.split(","):
                cursor.execute(
                    "SELECT has_table_privilege(current_user, %s, %s)",
                    (relation, privilege),
                )
                if not _fetch_one(cursor)[0]:
                    raise ReadinessError(f"Missing {privilege} privilege on {relation}")


def _check_postgresql_version(connection, label):
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version_num")
        if int(_fetch_one(cursor)[0]) < 160000:
            raise ReadinessError(f"{label} requires PostgreSQL 16 or newer")


def check_readiness():
    """Check both databases without writing data; return whether a tenant mapping exists."""
    source_requirements = {
        f"{DB_SCHEMA}.crm_campaign": "SELECT",
        f"{DB_SCHEMA}.cdp_segments": "SELECT",
        f"{DB_SCHEMA}.cdp_master_profiles": "SELECT",
        f"{DB_SCHEMA}.crm_suppression_list": "SELECT",
        f"{DB_SCHEMA}.crm_ad_sync_runs": "SELECT,INSERT,UPDATE",
    }
    destination_requirements = {
        "leo_ads.tenant": "SELECT",
        "leo_ads.customer360_tenant_map": "SELECT",
        "leo_ads.audience": "SELECT,INSERT,UPDATE",
        "leo_ads.user_serving_key": "SELECT,INSERT,UPDATE,DELETE",
        "leo_ads.audience_member": "SELECT,INSERT,DELETE",
    }
    with closing(_connect()) as source:
        _check_postgresql_version(source, "Customer 360 database")
        _check_relations(source, source_requirements)
        with source.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = to_regclass(%s)
                      AND conname = 'chk_crm_ad_sync_runs_platform'
                      AND convalidated
                      AND pg_get_constraintdef(oid) LIKE '%%leo_ads%%'
                )
                """,
                (f"{DB_SCHEMA}.crm_ad_sync_runs",),
            )
            if not _fetch_one(cursor)[0]:
                raise ReadinessError("Customer 360 audience-sync platform upgrade is missing")

    url = os.environ.get("LEO_ADS_DATABASE_URL")
    destination = psycopg2.connect(url) if url else _connect()
    with closing(destination) as target:
        _check_postgresql_version(target, "LEO Ads database")
        _check_relations(target, destination_requirements)
        with target.cursor() as cursor:
            for relation in ("leo_ads.tenant", "leo_ads.customer360_tenant_map"):
                cursor.execute(
                    "SELECT has_any_column_privilege(current_user, %s, 'UPDATE')",
                    (relation,),
                )
                if not _fetch_one(cursor)[0]:
                    raise ReadinessError(f"Missing column UPDATE privilege for locking {relation}")
            cursor.execute("SELECT has_database_privilege(current_user, current_database(), 'TEMP')")
            if not _fetch_one(cursor)[0]:
                raise ReadinessError("Destination role lacks TEMP privilege")
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'leo_ads.audience'::regclass
                      AND attname = 'customer360_tenant_id' AND NOT attisdropped
                ), EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'leo_ads.audience'::regclass
                      AND conname = 'fk_audience_customer360_tenant' AND convalidated
                      AND confrelid = 'leo_ads.customer360_tenant_map'::regclass
                      AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (customer360_tenant_id, tenant_id)%%'
                ), EXISTS (
                    SELECT 1 FROM leo_ads.customer360_tenant_map m
                    JOIN leo_ads.tenant t ON t.tenant_id = m.tenant_id
                    WHERE t.status = 'active'
                )
                """
            )
            column_present, constraint_present, has_mapping = _fetch_one(cursor)
            if not column_present or not constraint_present:
                raise ReadinessError("LEO Ads audience tenant-integrity upgrade is missing")
            cursor.execute(
                """
                SELECT confrelid = 'leo_ads.audience'::regclass,
                       confrelid = 'leo_ads.user_serving_key'::regclass,
                       pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'leo_ads.audience_member'::regclass
                  AND contype = 'f' AND convalidated
                """
            )
            member_constraints = cursor.fetchall()
            if not all(
                any(row[parent_index] and expected in row[2] for row in member_constraints)
                for parent_index, expected in (
                    (0, "FOREIGN KEY (tenant_id, audience_id)"),
                    (1, "FOREIGN KEY (tenant_id, user_key_hash)"),
                )
            ):
                raise ReadinessError("LEO Ads audience membership foreign keys are missing")
            return has_mapping


def main():
    """Report deployment readiness without displaying connection strings."""
    try:
        has_mapping = check_readiness()
    except ReadinessError as exc:
        print(f"LEO Ads sync is not ready: {exc}")
        return 1
    except psycopg2.Error as exc:
        print(f"LEO Ads sync database check failed ({type(exc).__name__}); check connectivity and permissions.")
        return 1
    if not has_mapping:
        print("LEO Ads sync is not ready: no active tenant mapping exists.")
        return 1
    print("LEO Ads sync database objects, privileges and an active mapping are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
