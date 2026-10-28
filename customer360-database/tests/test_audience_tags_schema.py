from pathlib import Path


DATABASE_ROOT = Path(__file__).resolve().parents[1]


def test_audience_tags_table_and_profile_segment_columns():
    schema = (DATABASE_ROOT / "database-schema.sql").read_text(encoding="utf-8")
    init_core = (DATABASE_ROOT / "init-core-database.sql").read_text(encoding="utf-8")
    migration = (DATABASE_ROOT / "migrations" / "011_cdp_audience_tags.sql").read_text(
        encoding="utf-8"
    )

    for sql in (schema, migration):
        assert "CREATE TABLE IF NOT EXISTS customer360.cdp_audience_tags" in sql
        assert "tag_name TEXT PRIMARY KEY" in sql
        assert "unique_id TEXT NOT NULL" in sql
        assert "condensed_name TEXT NOT NULL" in sql
        assert "tier_1 TEXT NOT NULL" in sql
        assert "idx_cdp_audience_tags_unique_id" in sql
        assert "idx_cdp_audience_tags_tiers" in sql
        assert "idx_cdp_mp_audience_tags" in sql
        assert "idx_cdp_segments_audience_tags" in sql

    assert "audience_tags TEXT[]," in schema
    assert "audience_tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]," in schema
    profile_column_ddl = "ALTER TABLE customer360.cdp_master_profiles\n    ADD COLUMN IF NOT EXISTS audience_tags TEXT[];"
    segment_column_ddl = (
        "ALTER TABLE customer360.cdp_segments\n"
        "    ADD COLUMN IF NOT EXISTS audience_tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];"
    )
    assert profile_column_ddl in schema
    assert profile_column_ddl.index("ADD COLUMN IF NOT EXISTS") < schema.index(
        "CREATE INDEX IF NOT EXISTS idx_cdp_mp_audience_tags"
    )
    assert segment_column_ddl in schema
    assert segment_column_ddl.index("ADD COLUMN IF NOT EXISTS") < schema.index(
        "CREATE INDEX IF NOT EXISTS idx_cdp_segments_audience_tags"
    )
    assert "ADD COLUMN IF NOT EXISTS audience_tags TEXT[];" in migration
    assert "ADD COLUMN IF NOT EXISTS audience_tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];" in migration
    assert "'audience_tags'" in init_core
    assert "'IAB Audience Tags'" in init_core
    assert "'audience_tags'" in migration
