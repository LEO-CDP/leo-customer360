-- IAB Audience Taxonomy 1.1 catalog (customer360.cdp_audience_tags) and
-- audience_tags TEXT[] columns on cdp_master_profiles and cdp_segments.
BEGIN;

CREATE TABLE IF NOT EXISTS customer360.cdp_audience_tags (
    tag_name TEXT PRIMARY KEY CHECK (btrim(tag_name) <> ''),
    unique_id TEXT NOT NULL CHECK (btrim(unique_id) <> ''),
    parent_id TEXT,
    taxonomy_version TEXT NOT NULL DEFAULT '1.1' CHECK (btrim(taxonomy_version) <> ''),
    condensed_name TEXT NOT NULL CHECK (btrim(condensed_name) <> ''),
    tier_1 TEXT NOT NULL CHECK (btrim(tier_1) <> ''),
    tier_2 TEXT,
    tier_3 TEXT,
    tier_4 TEXT,
    tier_5 TEXT,
    tier_6 TEXT,
    extension_notes TEXT,
    status_code SMALLINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_cdp_audience_tags_version_unique_id
        UNIQUE (taxonomy_version, unique_id)
);

COMMENT ON TABLE customer360.cdp_audience_tags IS
    'IAB Audience Taxonomy 1.1 catalog keyed by tag_name (slugified tier path). Used to map first-party cdp_master_profiles and cdp_segments to standardized contextual and audience segmentation taxonomies for analytics, personalization, and programmatic advertising.';
COMMENT ON COLUMN customer360.cdp_audience_tags.tag_name IS
    'Primary key: deterministic slugified tier path (e.g. demographic/age-range/18-20) referenced by cdp_segments.audience_tags and cdp_master_profiles.audience_tags.';
COMMENT ON COLUMN customer360.cdp_audience_tags.unique_id IS
    'IAB Audience Taxonomy Unique ID from the official TSV specification.';
COMMENT ON COLUMN customer360.cdp_audience_tags.parent_id IS
    'Parent IAB Unique ID in the taxonomy hierarchy (NULL for Tier 1 root nodes).';
COMMENT ON COLUMN customer360.cdp_audience_tags.condensed_name IS
    'IAB Condensed Name (1st, 2nd, Last Tier) from the taxonomy TSV.';

CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_unique_id
    ON customer360.cdp_audience_tags (unique_id);
CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_parent_id
    ON customer360.cdp_audience_tags (parent_id)
    WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_tiers
    ON customer360.cdp_audience_tags (tier_1, tier_2, tier_3);
CREATE INDEX IF NOT EXISTS idx_cdp_audience_tags_status
    ON customer360.cdp_audience_tags (status_code);

ALTER TABLE customer360.cdp_master_profiles
    ADD COLUMN IF NOT EXISTS audience_tags TEXT[];

CREATE INDEX IF NOT EXISTS idx_cdp_mp_audience_tags
    ON customer360.cdp_master_profiles USING GIN (audience_tags);

ALTER TABLE customer360.cdp_segments
    ADD COLUMN IF NOT EXISTS audience_tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

CREATE INDEX IF NOT EXISTS idx_cdp_segments_audience_tags
    ON customer360.cdp_segments USING GIN (audience_tags);

INSERT INTO customer360.cdp_profile_attributes (
    attribute_internal_code,
    master_profile_column,
    name,
    description,
    attribute_group,
    source_table,
    data_type,
    domain_scope,
    is_pii,
    status,
    is_identity_resolution,
    matching_rule,
    matching_threshold,
    consolidation_rule,
    is_ai_agent,
    agent_code,
    agent_version,
    value_type,
    value_min,
    value_max,
    refresh_frequency,
    display_order
) VALUES (
    'audience_tags',
    'audience_tags',
    'IAB Audience Tags',
    'Standardized IAB Audience Taxonomy tag names mapped from segments for audience analytics, personalization, and programmatic advertising.',
    'MARKETING',
    'cdp_master_profiles',
    'ARRAY',
    'all',
    FALSE,
    'ACTIVE',
    FALSE,
    NULL,
    NULL,
    NULL,
    FALSE,
    NULL,
    NULL,
    'label',
    NULL,
    NULL,
    NULL,
    365
)
ON CONFLICT (attribute_internal_code) DO UPDATE SET
    master_profile_column = EXCLUDED.master_profile_column,
    name                  = EXCLUDED.name,
    description           = EXCLUDED.description,
    attribute_group       = EXCLUDED.attribute_group,
    source_table          = EXCLUDED.source_table,
    data_type             = EXCLUDED.data_type,
    domain_scope          = EXCLUDED.domain_scope,
    is_pii                = EXCLUDED.is_pii,
    status                = EXCLUDED.status,
    value_type            = EXCLUDED.value_type,
    display_order         = EXCLUDED.display_order,
    updated_at            = now();

-- Backfill standardized IAB audience_tags on default cdp_segments (when empty)
-- and sync cdp_master_profiles.audience_tags across tenants.
UPDATE customer360.cdp_segments
SET audience_tags = CASE
    WHEN segment_tag IN ('high_value', 'champions') THEN
        ARRAY['demographic/personal-finance/personal-level-affluence-band/high-net-worth']::text[]
    WHEN segment_tag IN ('growth_potential', 'hot_leads') THEN
        ARRAY['demographic/personal-finance/personal-level-affluence-band/mid-net-worth']::text[]
    WHEN segment_tag LIKE 'retail_%' OR segment_tag LIKE 'ecommerce_%' THEN
        ARRAY['interest/shopping', 'purchase-intent/consumer-packaged-goods']::text[]
    WHEN segment_tag LIKE 'travel_%' THEN
        ARRAY['interest/travel', 'purchase-intent/travel-and-tourism']::text[]
    WHEN segment_tag LIKE 'education_%' THEN
        ARRAY['interest/education', 'purchase-intent/education-and-careers']::text[]
    WHEN segment_tag LIKE 'real_estate_%' THEN
        ARRAY['interest/real-estate', 'purchase-intent/real-estate']::text[]
    ELSE audience_tags
END,
updated_at = now()
WHERE COALESCE(cardinality(audience_tags), 0) = 0
  AND (
      segment_tag IN ('high_value', 'champions', 'growth_potential', 'hot_leads')
      OR segment_tag LIKE 'retail_%'
      OR segment_tag LIKE 'ecommerce_%'
      OR segment_tag LIKE 'travel_%'
      OR segment_tag LIKE 'education_%'
      OR segment_tag LIKE 'real_estate_%'
  );

UPDATE customer360.cdp_master_profiles AS mp
SET audience_tags = ARRAY(
    SELECT DISTINCT tag
    FROM (
        -- 1. Tags inherited from active matched segments in cdp_segments
        SELECT u.tag
        FROM customer360.cdp_segments AS s
        CROSS JOIN LATERAL unnest(COALESCE(s.audience_tags, ARRAY[]::text[])) AS u(tag)
        WHERE s.tenant_id = mp.tenant_id
          AND s.is_active = TRUE
          AND s.status_code = 1
          AND s.segment_tag = ANY(COALESCE(mp.segmentation_tags, ARRAY[]::text[]))
        UNION
        -- 2. Existing profile audience_tags
        SELECT e.tag
        FROM unnest(COALESCE(mp.audience_tags, ARRAY[]::text[])) AS e(tag)
        UNION
        -- 3. Standardized IAB Demographic Gender tag
        SELECT CASE lower(btrim(COALESCE(mp.gender, '')))
            WHEN 'female' THEN 'demographic/gender/female'
            WHEN 'male' THEN 'demographic/gender/male'
            WHEN 'other' THEN 'demographic/gender/other-gender'
            ELSE NULL
        END AS tag
        UNION
        -- 4. Standardized IAB Demographic Age Range tag
        SELECT CASE
            WHEN mp.date_of_birth IS NULL THEN NULL
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 18 AND 20 THEN 'demographic/age-range/18-20'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 21 AND 24 THEN 'demographic/age-range/21-24'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 25 AND 29 THEN 'demographic/age-range/25-29'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 30 AND 34 THEN 'demographic/age-range/30-34'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 35 AND 39 THEN 'demographic/age-range/35-39'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 40 AND 44 THEN 'demographic/age-range/40-44'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 45 AND 49 THEN 'demographic/age-range/45-49'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 50 AND 54 THEN 'demographic/age-range/50-54'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 55 AND 59 THEN 'demographic/age-range/55-59'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 60 AND 64 THEN 'demographic/age-range/60-64'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 65 AND 69 THEN 'demographic/age-range/65-69'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) BETWEEN 70 AND 74 THEN 'demographic/age-range/70-74'
            WHEN EXTRACT(YEAR FROM age(CURRENT_DATE, mp.date_of_birth)) >= 75 THEN 'demographic/age-range/75-plus'
            ELSE NULL
        END AS tag
        UNION
        -- 5. Standardized IAB Domain Interest tag
        SELECT CASE lower(btrim(COALESCE(mp.domain, '')))
            WHEN 'retail' THEN 'interest/shopping'
            WHEN 'banking' THEN 'interest/personal-finance'
            WHEN 'travel' THEN 'interest/travel'
            WHEN 'education' THEN 'interest/education'
            WHEN 'real_estate' THEN 'interest/real-estate'
            WHEN 'media' THEN 'interest/television'
            ELSE NULL
        END AS tag
    ) AS derived(tag)
    WHERE tag IS NOT NULL AND btrim(tag) <> ''
    ORDER BY tag
)
WHERE COALESCE(cardinality(mp.audience_tags), 0) = 0;

COMMIT;
