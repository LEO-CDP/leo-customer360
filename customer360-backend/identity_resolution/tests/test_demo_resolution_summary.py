"""Verify reset seeding checks resolved profiles rather than staged records."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest


path = Path(__file__).resolve().parents[1] / "scripts" / "test_resolution_task.py"
spec = importlib.util.spec_from_file_location("demo_resolution_task", path)
assert spec and spec.loader
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_demo_resolution_reports_total_and_all_required_domains(monkeypatch):
    monkeypatch.setenv("CIR_VALIDATE_DEMO_COVERAGE", "1")
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [{"domain": domain, "profile_count": 1_000} for domain in demo.DEMO_DOMAINS],
        [],
        [],
    ]

    assert demo.print_summary(cursor) == 6_000
    assert "LIMIT 10" in cursor.execute.call_args_list[1].args[0]


@pytest.mark.parametrize(
    "counts",
    [
        [{"domain": domain, "profile_count": 1_000} for domain in demo.DEMO_DOMAINS[:5]],
        [{"domain": domain, "profile_count": 1_000} for domain in demo.DEMO_DOMAINS[1:]],
    ],
)
def test_demo_resolution_rejects_insufficient_count_or_missing_domain(monkeypatch, counts):
    monkeypatch.setenv("CIR_VALIDATE_DEMO_COVERAGE", "1")
    cursor = MagicMock()
    cursor.fetchall.return_value = counts
    with pytest.raises(RuntimeError, match="more than 5000 resolved profiles"):
        demo.print_summary(cursor)
