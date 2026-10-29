from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "customer360-database"


def test_fresh_schema_marks_historical_upgrade_and_upgrade_marks_only_after_steps():
    schema = (DATABASE / "database-schema.sql").read_text(encoding="utf-8")
    upgrade = (DATABASE / "legacy-upgrade.sql").read_text(encoding="utf-8")

    assert "IF to_regclass('customer360.sys_tenant') IS NULL THEN" in schema
    assert "VALUES ('legacy_through_018')" in schema
    assert upgrade.index("pg_advisory_lock(") < upgrade.index("\\if :legacy_upgrade_needed")
    assert upgrade.index("-- Historical step: 018_journey_report.sql") < upgrade.index(
        "INSERT INTO customer360.sys_schema_upgrade_history (upgrade_key)"
    )
    assert "\\else\n\\echo Legacy database upgrade already applied.\n\\endif" in upgrade
    assert "pg_advisory_unlock(" in upgrade


def test_setup_runners_use_the_consolidated_upgrade():
    for path in (
        ROOT / "dev-c360.sh",
        DATABASE / "setup-and-upgrade-db.sh",
        ROOT / "deployments/postgres/run-sql.sh",
    ):
        text = path.read_text(encoding="utf-8")
        assert "legacy-upgrade.sql" in text
        assert "migrations/*.sql" not in text


def test_retired_migrations_directory_contains_no_sql():
    assert not list((DATABASE / "migrations").glob("*.sql"))
