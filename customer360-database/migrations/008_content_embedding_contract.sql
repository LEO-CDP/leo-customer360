-- Allow 384- and 768-dimensional recommendation vectors with matching indexes.
BEGIN;

ALTER TABLE customer360.cdp_content_items
    ADD COLUMN IF NOT EXISTS embedding_text TEXT,
    ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(50),
    ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ;

UPDATE customer360.cdp_content_items
SET segment_tags = ARRAY[]::TEXT[]
WHERE segment_tags IS NULL;
ALTER TABLE customer360.cdp_content_items
    ALTER COLUMN segment_tags SET DEFAULT ARRAY[]::TEXT[],
    ALTER COLUMN segment_tags SET NOT NULL;

DROP INDEX IF EXISTS customer360.idx_cdp_content_items_embedding_hnsw;
DROP INDEX IF EXISTS customer360.idx_cdp_product_items_embedding_hnsw;
ALTER TABLE customer360.cdp_content_items
    ALTER COLUMN embedding TYPE VECTOR
    USING embedding::VECTOR;

UPDATE customer360.cdp_content_items
SET embedding = NULL,
    embedding_model = NULL,
    embedding_text = NULL,
    embedding_version = NULL,
    embedding_updated_at = NULL
WHERE embedding IS NOT NULL
  AND vector_dims(embedding) NOT IN (384, 768);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_content_items'::regclass
          AND conname = 'chk_cdp_content_items_embedding_dimensions'
    ) THEN
        ALTER TABLE customer360.cdp_content_items
            ADD CONSTRAINT chk_cdp_content_items_embedding_dimensions
            CHECK (embedding IS NULL OR vector_dims(embedding) IN (384, 768));
    END IF;
END;
$$;

ALTER TABLE customer360.cdp_product_items
    ADD COLUMN IF NOT EXISTS embedding_text TEXT,
    ADD COLUMN IF NOT EXISTS embedding VECTOR,
    ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(255),
    ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(50),
    ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ;
ALTER TABLE customer360.cdp_product_items
    ALTER COLUMN embedding TYPE VECTOR
    USING embedding::VECTOR;

UPDATE customer360.cdp_product_items
SET embedding = NULL,
    embedding_model = NULL,
    embedding_text = NULL,
    embedding_version = NULL,
    embedding_updated_at = NULL
WHERE embedding IS NOT NULL
  AND vector_dims(embedding) NOT IN (384, 768);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'customer360.cdp_product_items'::regclass
          AND conname = 'chk_cdp_product_items_embedding_dimensions'
    ) THEN
        ALTER TABLE customer360.cdp_product_items
            ADD CONSTRAINT chk_cdp_product_items_embedding_dimensions
            CHECK (embedding IS NULL OR vector_dims(embedding) IN (384, 768));
    END IF;
END;
$$;

DROP INDEX IF EXISTS customer360.idx_cdp_content_items_embedding_384_hnsw;
DROP INDEX IF EXISTS customer360.idx_cdp_content_items_embedding_768_hnsw;
CREATE INDEX idx_cdp_content_items_embedding_384_hnsw
    ON customer360.cdp_content_items
    USING hnsw ((embedding::VECTOR(384)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 384;
CREATE INDEX idx_cdp_content_items_embedding_768_hnsw
    ON customer360.cdp_content_items
    USING hnsw ((embedding::VECTOR(768)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 768;

DROP INDEX IF EXISTS customer360.idx_cdp_product_items_embedding_384_hnsw;
DROP INDEX IF EXISTS customer360.idx_cdp_product_items_embedding_768_hnsw;
CREATE INDEX idx_cdp_product_items_embedding_384_hnsw
    ON customer360.cdp_product_items
    USING hnsw ((embedding::VECTOR(384)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 384;
CREATE INDEX idx_cdp_product_items_embedding_768_hnsw
    ON customer360.cdp_product_items
    USING hnsw ((embedding::VECTOR(768)) vector_cosine_ops)
    WHERE vector_dims(embedding) = 768;

DROP INDEX IF EXISTS customer360.idx_cdp_content_items_domain_type;
CREATE INDEX idx_cdp_content_items_domain_type
    ON customer360.cdp_content_items (tenant_id, domain, item_type);

CREATE OR REPLACE FUNCTION customer360.invalidate_content_item_embedding()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.domain IS DISTINCT FROM OLD.domain
       OR NEW.item_type IS DISTINCT FROM OLD.item_type
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.summary IS DISTINCT FROM OLD.summary
       OR NEW.segment_tags IS DISTINCT FROM OLD.segment_tags THEN
        NEW.embedding := NULL;
        NEW.embedding_text := NULL;
        NEW.embedding_model := NULL;
        NEW.embedding_version := NULL;
        NEW.embedding_updated_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION customer360.invalidate_product_item_embedding()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.domain IS DISTINCT FROM OLD.domain
       OR NEW.product_type IS DISTINCT FROM OLD.product_type
       OR NEW.keywords IS DISTINCT FROM OLD.keywords
       OR NEW.ext_attributes IS DISTINCT FROM OLD.ext_attributes
       OR NEW.source_fields IS DISTINCT FROM OLD.source_fields THEN
        NEW.embedding := NULL;
        NEW.embedding_text := NULL;
        NEW.embedding_model := NULL;
        NEW.embedding_version := NULL;
        NEW.embedding_updated_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invalidate_product_item_embedding
    ON customer360.cdp_product_items;
CREATE TRIGGER trg_invalidate_product_item_embedding
    BEFORE UPDATE OF
        domain,
        product_type,
        keywords,
        ext_attributes,
        source_fields
    ON customer360.cdp_product_items
    FOR EACH ROW EXECUTE FUNCTION customer360.invalidate_product_item_embedding();

COMMENT ON COLUMN customer360.cdp_content_items.embedding IS
    '384- or 768-dimensional content embedding used for semantic and hybrid recommendation ranking.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_text IS
    'Canonical text embedded for recommendation ranking; refreshed when source content changes.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_model IS
    'Provider and model key used to generate the current content embedding.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_version IS
    'Application embedding contract version used to generate the current vector.';
COMMENT ON COLUMN customer360.cdp_content_items.embedding_updated_at IS
    'Time the current content embedding was generated.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_text IS
    'Canonical product text reserved for future product-vector generation.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding IS
    '384- or 768-dimensional product embedding reserved for product similarity and recommendation.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_model IS
    'Provider and model key used to generate the current product embedding.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_version IS
    'Application embedding contract version used to generate the current product vector.';
COMMENT ON COLUMN customer360.cdp_product_items.embedding_updated_at IS
    'Time the current product embedding was generated.';

COMMIT;
