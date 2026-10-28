"""Regression checks for the RLS-safe unclassified-domain migration."""

import unittest
from pathlib import Path


class UnclassifiedDomainMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "016_add_unclassified_profile_domain.sql"
        )
        cls.sql = migration_path.read_text(encoding="utf-8")

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
