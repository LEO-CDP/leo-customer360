"""Regression checks for the RLS-safe unclassified-domain upgrade."""

import unittest
from pathlib import Path


class UnclassifiedDomainMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        upgrade_path = Path(__file__).resolve().parents[1] / "legacy-upgrade.sql"
        cls.sql = upgrade_path.read_text(encoding="utf-8").split(
            "-- Historical step: 016_add_unclassified_profile_domain.sql", 1
        )[1].split("-- Historical step: 017_agent_campagn_journey.sql", 1)[0]

    def test_registers_the_neutral_domain(self):
        self.assertIn("'_'", self.sql)
        self.assertIn("'Unclassified / Not set'", self.sql)
        self.assertIn("domain_code = '_'", self.sql)

    def test_tenant_scoped_writes_set_rls_context(self):
        self.assertIn("FOR current_tenant_id IN", self.sql)
        self.assertIn(
            "set_config('app.tenant_id', current_tenant_id::text, true)",
            self.sql,
        )
        self.assertIn("WHERE tenant_id = current_tenant_id", self.sql)
        self.assertIn("set_config('app.tenant_id', '', true)", self.sql)
