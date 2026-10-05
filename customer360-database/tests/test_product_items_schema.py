from pathlib import Path


DATABASE_ROOT = Path(__file__).resolve().parents[1]


def test_product_item_table_has_tenant_scoped_ext_attributes_jsonb():
    schema = (DATABASE_ROOT / "database-schema.sql").read_text(encoding="utf-8")
    migration = (DATABASE_ROOT / "migrations" / "007_cdp_product_items.sql").read_text(
        encoding="utf-8"
    )

    assert not (DATABASE_ROOT / "migrations" / "008_cdp_product_ext_attributes.sql").exists()
    assert "CREATE TABLE IF NOT EXISTS customer360.cdp_product_items" in schema
    assert "product_item_id TEXT PRIMARY KEY" in schema
    assert "ext_attributes JSONB NOT NULL DEFAULT '{}'::jsonb" in schema
    assert "jsonb_typeof(ext_attributes) = 'object'" in schema
    assert "content_item_id UUID," in schema
    assert "ext_attributes JSONB NOT NULL DEFAULT '{}'::jsonb" in migration
    assert "ADD COLUMN IF NOT EXISTS ext_attributes JSONB NOT NULL DEFAULT '{}'::jsonb" in migration
    assert "ALTER COLUMN content_item_id DROP NOT NULL" in migration
    assert "DROP CONSTRAINT IF EXISTS cdp_product_items_store_id_check" in migration
    assert "source_id TEXT NOT NULL" in schema
    assert "source_type TEXT NOT NULL DEFAULT ''" in schema
    assert "product_id TEXT NOT NULL" in schema
    assert "product_item_id TEXT PRIMARY KEY" in migration
    assert "RENAME COLUMN store_id TO source_id" in migration
    assert "UNIQUE (tenant_id, source_type, source_id, product_id_type, product_id)" in schema
