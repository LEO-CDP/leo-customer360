from unittest.mock import MagicMock

import psycopg2
import pytest

import check_leo_ads_readiness as readiness


def _connection(responses):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(value,) for value in responses]
    return connection, cursor


def test_readiness_checks_each_permission_individually():
    connection, cursor = _connection(["leo_ads.audience", True, False])
    with pytest.raises(readiness.ReadinessError, match="Missing UPDATE privilege"):
        readiness._check_relations(connection, {"leo_ads.audience": "SELECT,UPDATE"})
    assert cursor.execute.call_args_list[-1].args[1] == ("leo_ads.audience", "UPDATE")


def test_readiness_rejects_missing_relation():
    connection, _ = _connection([None])
    with pytest.raises(readiness.ReadinessError, match="Missing relation"):
        readiness._check_relations(connection, {"leo_ads.audience": "SELECT"})


def test_readiness_rejects_unsupported_postgres():
    connection, _ = _connection(["150000"])
    with pytest.raises(readiness.ReadinessError, match="PostgreSQL 16"):
        readiness._check_postgresql_version(connection, "Destination")


def test_readiness_does_not_expose_database_connection_errors(monkeypatch, capsys):
    def fail():
        raise psycopg2.OperationalError("sensitive connection details")

    monkeypatch.setattr(readiness, "check_readiness", fail)
    assert readiness.main() == 1
    assert "sensitive connection details" not in capsys.readouterr().out


def test_readiness_fails_when_no_active_mapping_exists(monkeypatch, capsys):
    monkeypatch.setattr(readiness, "check_readiness", lambda: False)
    assert readiness.main() == 1
    assert "no active tenant mapping" in capsys.readouterr().out
