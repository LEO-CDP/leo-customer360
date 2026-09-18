"""Safety net for executing admin-authored SQL fragments/statements stored on
``cdp_segments`` (see core/models/segmentation.py).

``sql_rules`` is a WHERE-clause fragment translated client-side (by jQuery
QueryBuilder's ``getSQL()``) from a structured rule tree and stored verbatim
-- there's no way to safely bind-parameterize an arbitrary user-composed
boolean expression, so instead of parameterizing it we apply strict
allow-list-style validation before ever interpolating it into a query: no
statement separators/comments (blocks stacked-query injection) and no DML/
DDL/query keywords. ``final_generated_sql`` is a full (never executed by this
API -- informational/audit only) SELECT statement, so it's checked with a
looser variant that still blocks statement stacking and DML/DDL but allows
SELECT/FROM/JOIN.

This is defense-in-depth, not a full SQL parser. It's applied both when a
segment is created/updated (core/schemas/segmentation.py) AND again
immediately before every execution (core/routers/segment_api.py), so rows
seeded/migrated outside the API (e.g. core/init_core_data.py, which builds
ORM objects directly and never goes through the Pydantic schemas) are still
checked at execution time.
"""

import re

# Defensive length caps, checked before any regex scan -- guards against
# pathologically long payloads (regex-scan CPU cost, oversized stored rows)
# regardless of what they contain.
_MAX_WHERE_FRAGMENT_LENGTH = 4000
_MAX_READONLY_STATEMENT_LENGTH = 20000

# Control characters (other than plain whitespace) have no legitimate use in
# a SQL fragment. They're a common way to smuggle payloads past naive
# string-based filters/WAFs/log scrubbers, so they're rejected outright here
# rather than trying to special-case each one.
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Statement stacking / comment-based injection -- never legitimate in either
# a WHERE fragment or a single read-only SELECT statement. Also blocks the
# MySQL-style "#" line comment in case these validators are ever reused
# against a different backend.
_STACKING_OR_COMMENT_PATTERN = re.compile(r";|--|/\*|\*/|#")

# DML/DDL/session/transaction-control/administrative keywords -- never
# legitimate in a segment rule, whether it's a fragment or a full statement.
_DML_DDL_KEYWORDS = (
    r"\b(insert|update|delete|drop|alter|grant|revoke|truncate|create|exec|execute|call|copy|"
    r"vacuum|reindex|cluster|analyze|explain|listen|notify|unlisten|prepare|deallocate|"
    r"declare|fetch|commit|rollback|savepoint|begin|start|lock|merge|do|"
    r"set|reset|show|comment|import|foreign)\b"
)
_DML_DDL_PATTERN = re.compile(_DML_DDL_KEYWORDS, re.IGNORECASE)

# Administrative/system function *families*, matched by prefix so one rule
# catches every variant instead of trying to enumerate each dangerous
# function individually: pg_sleep/pg_read_file/pg_read_binary_file/
# pg_terminate_backend/pg_ls_dir/... (Postgres), lo_import/lo_export/...
# (large objects), dblink*/xp_*/sp_*/utl_*/dbms_* (cross-engine admin/
# extension functions), plus a few specific high-value targets that don't
# share one of those prefixes.
_DANGEROUS_FUNCTION_PATTERN = re.compile(
    r"\b(pg_\w*|lo_\w*|dblink\w*|xp_\w*|sp_\w*|utl_\w*|dbms_\w*|"
    r"current_setting|set_config|txid_current\w*|"
    r"pg_advisory\w*|pg_terminate_backend|pg_cancel_backend)\b",
    re.IGNORECASE,
)

# For a WHERE-clause *fragment* specifically (sql_rules), SELECT/FROM/JOIN/
# UNION/INTO/WITH have no legitimate use either -- their presence strongly
# suggests a subquery/stacked-query/CTE injection attempt.
_QUERY_KEYWORDS_PATTERN = re.compile(r"\b(select|from|join|union|into|with)\b", re.IGNORECASE)


def _reject_control_characters(value: str, field_name: str) -> None:
    if _CONTROL_CHAR_PATTERN.search(value):
        raise ValueError(f"{field_name} must not contain control characters")


def _reject_unbalanced_parentheses(value: str, field_name: str) -> None:
    """Both call sites (core/routers/segment_api.py) wrap ``sql_rules`` in one
    extra pair of parentheses before interpolating it into a query. An
    unbalanced fragment could close that wrapping paren early and change how
    the remainder of the surrounding, otherwise-fixed query text is parsed,
    so it's rejected even though it doesn't itself match another rule."""
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"{field_name} has unbalanced parentheses")
    if depth != 0:
        raise ValueError(f"{field_name} has unbalanced parentheses")


def validate_sql_where_fragment(fragment: str) -> str:
    """Returns ``fragment`` unchanged if it looks like a single, safe boolean
    WHERE-clause expression (e.g. ``"churn_risk_tier IN ('high', 'critical')"``),
    else raises ``ValueError``."""
    if not fragment or not fragment.strip():
        raise ValueError("sql_rules must not be empty")
    if len(fragment) > _MAX_WHERE_FRAGMENT_LENGTH:
        raise ValueError(f"sql_rules must not exceed {_MAX_WHERE_FRAGMENT_LENGTH} characters")
    _reject_control_characters(fragment, "sql_rules")
    if _STACKING_OR_COMMENT_PATTERN.search(fragment):
        raise ValueError("sql_rules must not contain statement separators (;) or comments (--, /* */, #)")
    if _DML_DDL_PATTERN.search(fragment):
        raise ValueError("sql_rules must not contain DML/DDL/administrative SQL keywords")
    if _QUERY_KEYWORDS_PATTERN.search(fragment):
        raise ValueError("sql_rules must be a single WHERE-clause expression (no SELECT/FROM/JOIN/UNION/WITH)")
    if _DANGEROUS_FUNCTION_PATTERN.search(fragment):
        raise ValueError("sql_rules must not call administrative/system functions")
    _reject_unbalanced_parentheses(fragment, "sql_rules")
    return fragment


def validate_readonly_sql_statement(sql: str) -> str:
    """Looser validation for ``final_generated_sql`` (a full, human-readable
    SELECT statement kept for reference -- never executed by this API):
    blocks statement stacking/comments, DML/DDL, and administrative/system
    functions, but allows SELECT/FROM/JOIN/UNION since those are expected
    here."""
    if not sql or not sql.strip():
        return sql
    if len(sql) > _MAX_READONLY_STATEMENT_LENGTH:
        raise ValueError(f"final_generated_sql must not exceed {_MAX_READONLY_STATEMENT_LENGTH} characters")
    _reject_control_characters(sql, "final_generated_sql")
    if _STACKING_OR_COMMENT_PATTERN.search(sql):
        raise ValueError("final_generated_sql must not contain statement separators (;) or comments (--, /* */, #)")
    if _DML_DDL_PATTERN.search(sql):
        raise ValueError("final_generated_sql must not contain DML/DDL/administrative SQL keywords")
    if _DANGEROUS_FUNCTION_PATTERN.search(sql):
        raise ValueError("final_generated_sql must not call administrative/system functions")
    return sql
