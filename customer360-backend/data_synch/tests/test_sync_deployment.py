import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_upgrade_does_not_modify_independent_promotions_schema(tmp_path):
    log = tmp_path / "calls"
    ready = tmp_path / "pg_isready"
    ready.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ready.chmod(0o755)
    psql = tmp_path / "psql"
    psql.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    psql.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGUSER": "test",
    }
    subprocess.run(
        ["sh", str(ROOT / "postgres/upgrade-app-schema.sh")],
        env=env, check=True, capture_output=True, text=True,
    )
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert "legacy-upgrade.sql" in calls[0]
    assert "promotions" not in calls[0]
    assert all("ON_ERROR_STOP=1" in call for call in calls)


def test_compose_waits_for_upgrades_and_routes_jobs_to_webserver():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "promotions" not in services
    assert all("promotions" not in str(volume) for volume in services["schema-upgrade"]["volumes"])
    for service in ("dagster", "dagster-daemon"):
        assert services[service]["environment"]["DAGSTER_GRAPHQL_HOST"] == "dagster"
        assert services[service]["environment"]["DAGSTER_GRAPHQL_PORT"] == 3000
        assert services[service]["environment"]["LEO_ADS_DATABASE_URL"] == "${LEO_ADS_DATABASE_URL:-}"
    for service in ("dagster", "dagster-daemon", "api"):
        assert services[service]["depends_on"]["schema-upgrade"]["condition"] == "service_completed_successfully"
    assert services["dagster-daemon"]["depends_on"]["dagster"]["condition"] == "service_healthy"


def test_ci_runs_sync_tests_and_rebuilds_dependent_images():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "customer360-backend/data_synch/run_tests.sh" in ci
    assert "- 'customer360-database/**'" in ci
    assert ci.count("- 'customer360-dao/**'") >= 2


def test_vks_destination_url_is_propagated_as_an_optional_secret():
    deploy = (ROOT / "deployments/customer360-backend/deploy-backend-vks.sh").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/cd-deploy-steps.yml").read_text(encoding="utf-8")
    assert "_LEO_ADS_DATABASE_URL_FROM_ENV" in deploy
    assert "printf 'LEO_ADS_DATABASE_URL=%s\\n'" in deploy
    assert 'LEO_ADS_DATABASE_URL: ${{ secrets.LEO_ADS_DATABASE_URL }}' in ci
    assert 'LEO_ADS_CHECK_READINESS: ${{ vars.LEO_ADS_CHECK_READINESS }}' in ci
