"""The agent server is self-contained: it must not depend on the DAO. The pure
shared modules in the client package (the SQL guard and the schema-doc builder)
are used by both the server and customer360-api, so importing them must not pull
in the client, which imports the DAO."""

import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _fresh(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {SRC!r}); {code}"],
        capture_output=True,
        text=True,
    )


def test_the_shared_sql_guard_imports_without_the_dao():
    result = _fresh(
        "import leo_customer360_agent.sql_guard as g; "
        "assert 'leo_customer360_dao' not in sys.modules, 'the DAO was imported'; "
        "assert g.ensure_select_only('SELECT 1') == 'SELECT 1'"
    )

    assert result.returncode == 0, result.stderr


def test_the_shared_schema_doc_builder_imports_without_the_dao():
    result = _fresh(
        "from leo_customer360_agent.schema_docs import build_schema_docs; "
        "assert 'leo_customer360_dao' not in sys.modules, 'the DAO was imported'; "
        "assert build_schema_docs({'t': ['a']}, {}) == {'t': 't. Columns: a'}"
    )

    assert result.returncode == 0, result.stderr


def test_the_client_names_are_still_exported_from_the_package_root():
    # The root keeps exporting the planning client lazily, so existing imports keep working.
    import leo_customer360_agent as pkg

    assert {"AIProviderError", "generate_campaign_plan", "CampaignPlanBrief"} <= set(pkg.__all__)
