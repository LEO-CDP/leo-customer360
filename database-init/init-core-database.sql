---------------------------------------------------
-- SCHEMA CREATION
---------------------------------------------------

CREATE SCHEMA IF NOT EXISTS customer360;

---------------------------------------------------
-- DEFAULT TENANT
---------------------------------------------------

-- Required parent record for all system seed data.
-- Idempotent: safe to re-run.
INSERT INTO customer360.sys_tenant (
    tenant_id,
    tenant_code,
    tenant_name,
    company_name,
    business_type,
    status
)
VALUES (
    '11111111-1111-1111-1111-111111111111'::uuid,
    'DEFAULT',
    'Default Tenant',
    'Default Company',
    'DEFAULT',
    'ACTIVE'
)
ON CONFLICT (tenant_id) DO NOTHING;

---------------------------------------------------
-- SYSTEM DOMAINS
---------------------------------------------------

INSERT INTO customer360.sys_domain (
    domain_code,
    domain_name,
    description,
    display_order
)
VALUES
('retail',        'Retail & E-Commerce',         'Retail stores, supermarkets, online commerce',             1),
('banking',       'Banking & Financial Services','Retail banking, digital banking and fintech',              2),
('insurance',     'Insurance',                   'Life, health, vehicle and general insurance',              3),
('healthcare',    'Healthcare',                  'Hospitals, clinics, pharmacies and healthcare providers',  4),
('telecom',       'Telecommunications',          'Mobile, broadband and telecom operators',                  5),
('travel',        'Travel & Hospitality',        'Hotels, airlines, travel agencies and tourism',            6),
('real_estate',   'Real Estate',                 'Property developers, brokers and property portals',        7),
('education',     'Education',                   'Schools, universities and online learning',                8),
('manufacturing', 'Manufacturing',               'Manufacturing and industrial enterprises',                  9),
('media',         'Media & Entertainment',       'Publishing, streaming, gaming and entertainment',         10)
ON CONFLICT (domain_code) DO NOTHING;

---------------------------------------------------
-- DEFAULT TENANT -> ALL DOMAINS
---------------------------------------------------

-- The default tenant supports every system domain.
INSERT INTO customer360.sys_tenant_domain (
    tenant_id,
    domain_id,
    is_default,
    is_active
)
SELECT
    '11111111-1111-1111-1111-111111111111'::uuid,
    d.domain_id,
    CASE
        WHEN d.domain_code = 'retail' THEN TRUE
        ELSE FALSE
    END AS is_default,
    TRUE
FROM customer360.sys_domain d
ON CONFLICT (tenant_id, domain_id) DO NOTHING;

---------------------------------------------------
-- EVENT CATALOG: SEED DATA
---------------------------------------------------

-- Core event vocabulary seed: GENERAL/FEEDBACK (cross-domain) plus the
-- requested verticals (retail, banking, real_estate, travel, media, education).
-- Idempotent: safe to re-run.
INSERT INTO customer360.cdp_event_catalog (
    event_name,
    event_category,
    domain_scope,
    description,
    is_conversion_default,
    value_field,
    display_order
) VALUES
    -- GENERAL
    ('page-view',      'GENERAL', 'all', 'User viewed a web page or app screen.',               FALSE, NULL, 10),
    ('search',         'GENERAL', 'all', 'User performed a search query.',                      FALSE, NULL, 20),
    ('content-view',   'GENERAL', 'all', 'User viewed a content asset (article, video, listing).',FALSE, NULL, 30),
    ('item-view',      'GENERAL', 'all', 'User viewed a generic catalog item.',                 FALSE, NULL, 40),
    ('user-login',     'GENERAL', 'all', 'User authenticated into the app/site.',               FALSE, NULL, 50),
    ('file-download',  'GENERAL', 'all', 'User downloaded a file/document.',                    FALSE, NULL, 60),
    ('social-sharing', 'GENERAL', 'all', 'User shared content to a social network.',            FALSE, NULL, 70),
    ('submit-contact', 'GENERAL', 'all', 'User submitted a contact-us form.',                   FALSE, NULL, 80),
    ('ad-impression',  'GENERAL', 'all', 'An ad was rendered/served to the user.',              FALSE, NULL, 90),

    -- FEEDBACK (cross-domain)
    ('submit-nps-form',   'FEEDBACK', 'all', 'User submitted an NPS survey.',                  FALSE, 'nps_score',  100),
    ('submit-csat-form',  'FEEDBACK', 'all', 'User submitted a CSAT survey.',                  FALSE, 'csat_score', 110),
    ('product-review',    'FEEDBACK', 'all', 'User submitted a product/service review.',       FALSE, NULL,         120),
    ('negative-feedback', 'FEEDBACK', 'all', 'Negative feedback/sentiment recorded.',          FALSE, NULL,         130),
    ('positive-feedback', 'FEEDBACK', 'all', 'Positive feedback/sentiment recorded.',          FALSE, NULL,         140),

    -- COMMERCE (retail)
    ('add-to-cart',      'COMMERCE', 'retail', 'User added an item to their cart.',            FALSE, NULL,          150),
    ('remove-from-cart', 'COMMERCE', 'retail', 'User removed an item from their cart.',        FALSE, NULL,          160),
    ('order-checkout',   'COMMERCE', 'retail', 'User started/completed checkout.',             FALSE, 'order_total', 170),
    ('purchase',         'COMMERCE', 'retail', 'User completed a purchase.',                   TRUE,  'order_total', 180),
    ('first-purchase',   'COMMERCE', 'retail', 'User''s first-ever purchase.',                 TRUE,  'order_total', 190),
    ('made-payment',     'COMMERCE', 'retail', 'Payment was successfully captured.',           TRUE,  'amount',      200),
    ('subscribe',        'COMMERCE', 'retail', 'User subscribed to a recurring plan.',         TRUE,  'plan_value',  210),
    ('add-wishlist',     'COMMERCE', 'retail', 'User added an item to their wishlist.',        FALSE, NULL,          220),

    -- FINANCE (banking)
    ('apply-loan',         'FINANCE', 'banking', 'User submitted a loan application.',           FALSE, 'loan_amount',      230),
    ('approve-loan',       'FINANCE', 'banking', 'A loan application was approved.',             TRUE,  'loan_amount',      240),
    ('loan-repayment',     'FINANCE', 'banking', 'User made a loan repayment.',                  FALSE, 'repayment_amount', 250),
    ('open-bank-account',  'FINANCE', 'banking', 'User opened a new bank account.',              TRUE,  NULL,               260),
    ('transfer-money',     'FINANCE', 'banking', 'User transferred money between accounts.',     FALSE, 'transfer_amount',  270),
    ('pay-bill',           'FINANCE', 'banking', 'User paid a bill via the banking app.',        FALSE, 'bill_amount',      280),
    ('credit-score-check', 'FINANCE', 'banking', 'User checked their credit score.',             FALSE, NULL,               290),
    ('kyc-completed',      'FINANCE', 'banking', 'User completed KYC/eKYC verification.',        FALSE, NULL,               300),

    -- STOCK_TRADING (banking/wealth)
    ('view-stock',     'STOCK_TRADING', 'banking', 'User viewed a stock/security detail page.', FALSE, NULL,           310),
    ('buy-stock',      'STOCK_TRADING', 'banking', 'User bought a stock/security.',             TRUE,  'trade_amount', 320),
    ('sell-stock',     'STOCK_TRADING', 'banking', 'User sold a stock/security.',               FALSE, 'trade_amount', 330),
    ('view-portfolio', 'STOCK_TRADING', 'banking', 'User viewed their investment portfolio.',   FALSE, NULL,           340),

    -- TRAVEL
    ('search-flight',       'TRAVEL', 'travel', 'User searched for flights.',                        FALSE, NULL,            350),
    ('search-hotel',        'TRAVEL', 'travel', 'User searched for hotels.',                         FALSE, NULL,            360),
    ('view-destination',    'TRAVEL', 'travel', 'User viewed a destination/listing page.',           FALSE, NULL,            370),
    ('booking',             'TRAVEL', 'travel', 'User completed a travel booking.',                  TRUE,  'booking_value', 380),
    ('check-in',            'TRAVEL', 'travel', 'User checked in for a flight/hotel stay.',          FALSE, NULL,            390),
    ('check-out',           'TRAVEL', 'travel', 'User checked out of a flight/hotel stay.',          FALSE, NULL,            400),
    ('cancel-booking',      'TRAVEL', 'travel', 'User cancelled a travel booking.',                  FALSE, 'booking_value', 410),
    ('add-travel-wishlist', 'TRAVEL', 'travel', 'User added a destination/trip to their wishlist.',  FALSE, NULL,            420),

    -- REAL_ESTATE
    ('view-property',          'REAL_ESTATE', 'real_estate', 'User viewed a property listing.',           FALSE, NULL,           430),
    ('property-favorite',      'REAL_ESTATE', 'real_estate', 'User favorited a property listing.',        FALSE, NULL,           440),
    ('request-property-tour',  'REAL_ESTATE', 'real_estate', 'User requested a property tour.',           FALSE, NULL,           450),
    ('schedule-property-tour', 'REAL_ESTATE', 'real_estate', 'A property tour was scheduled.',            FALSE, NULL,           460),
    ('contact-agent',          'REAL_ESTATE', 'real_estate', 'User contacted a real-estate agent.',       FALSE, NULL,           470),
    ('submit-mortgage-form',   'REAL_ESTATE', 'real_estate', 'User submitted a mortgage inquiry form.',   FALSE, 'loan_amount',  480),
    ('mortgage-pre-approval',  'REAL_ESTATE', 'real_estate', 'User received mortgage pre-approval.',      FALSE, 'loan_amount',  490),
    ('submit-property-offer',  'REAL_ESTATE', 'real_estate', 'User submitted an offer on a property.',    TRUE,  'offer_amount', 500),

    -- MEDIA & CONTENT SUBSCRIPTIONS
    ('play-video',                'GENERAL', 'media', 'User started playing a video.',                        FALSE, NULL, 510),
    ('finish-video',              'GENERAL', 'media', 'User finished watching a video.',                      FALSE, NULL, 520),
    ('listen-audio',              'GENERAL', 'media', 'User listened to an audio track or podcast.',          FALSE, NULL, 530),
    ('read-article',              'GENERAL', 'media', 'User read an article or blog post.',                   FALSE, NULL, 540),
    ('subscribe-media',           'COMMERCE', 'media', 'User subscribed to a premium media/content plan.',    TRUE,  'subscription_amount', 550),
    ('cancel-media-subscription', 'COMMERCE', 'media', 'User cancelled their media subscription.',            FALSE, NULL, 560),

    -- EDUCATION
    ('enroll-course',     'EDUCATION', 'education', 'User enrolled in a course.',                     TRUE,  'course_fee', 570),
    ('start-course',      'EDUCATION', 'education', 'User started a course.',                         FALSE, NULL, 580),
    ('complete-course',   'EDUCATION', 'education', 'User completed a course.',                       FALSE, NULL, 590),
    ('start-lesson',      'EDUCATION', 'education', 'User started a specific lesson or module.',      FALSE, NULL, 600),
    ('complete-lesson',   'EDUCATION', 'education', 'User completed a lesson.',                       FALSE, NULL, 610),
    ('submit-quiz',       'EDUCATION', 'education', 'User submitted a quiz or assignment.',           FALSE, 'score', 620),
    ('download-material', 'EDUCATION', 'education', 'User downloaded course materials or syllabus.',  FALSE, NULL, 630)
ON CONFLICT (event_name) DO UPDATE SET
    event_category        = EXCLUDED.event_category,
    domain_scope          = EXCLUDED.domain_scope,
    description           = EXCLUDED.description,
    is_conversion_default = EXCLUDED.is_conversion_default,
    value_field           = EXCLUDED.value_field,
    display_order         = EXCLUDED.display_order,
    updated_at            = now();



-- ============================================================================
-- FULL ATTRIBUTE CATALOG SEED
-- ============================================================================
-- Full attribute catalog for cdp_master_profiles (every column, grouped) plus
-- the cdp_raw_profiles_stage matching keys used by backend-system/identity_resolution.
-- Idempotent: safe to re-run (ON CONFLICT upserts by attribute_internal_code).

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
    is_scoring_model,
    scoring_model_name,
    scoring_model_version,
    value_type,
    value_min,
    value_max,
    refresh_frequency,
    display_order
) VALUES
    -- SYSTEM
    ('master_profile_id', 'master_profile_id', 'Master Profile ID', 'Primary key of the golden, resolved customer record.', 'SYSTEM', 'cdp_master_profiles', 'UUID', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 10),
    ('tenant_id', 'tenant_id', 'Tenant ID', 'Workspace/tenant scope used for multi-tenant data isolation.', 'SYSTEM', 'cdp_master_profiles', 'UUID', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 20),
    ('user_id', 'user_id', 'User ID', 'Internal system user who owns or manages this profile (nullable).', 'SYSTEM', 'cdp_master_profiles', 'UUID', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 25),
    ('domain', 'domain', 'Business Domain', 'retail, banking, real_estate, travel, media, or education; drives domain-specific UI and activation logic.', 'SYSTEM', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 30),
    ('created_at', 'created_at', 'Profile Created At', 'Timestamp the master profile was first created.', 'SYSTEM', 'cdp_master_profiles', 'TIMESTAMP', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'timestamp', NULL, NULL, NULL, 40),
    ('updated_at', 'updated_at', 'Profile Updated At', 'Timestamp of the most recent update to this profile.', 'SYSTEM', 'cdp_master_profiles', 'TIMESTAMP', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'timestamp', NULL, NULL, NULL, 50),
    ('status_code', 'status_code', 'Status Code', 'Active (1), Inactive (0), or Deleted (-1) state of the profile.', 'SYSTEM', 'cdp_master_profiles', 'SMALLINT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 55),

    -- IDENTITY (demographics + core/secondary contact info)
    -- full_name: is_identity_resolution=FALSE, matching_rule/consolidation_rule=NULL --
    -- resolver.py's _get_active_rules() only loads BOTH matching AND consolidation
    -- config for rows with is_identity_resolution=TRUE, so a 'most_recent' merge
    -- policy here would be silently dead/unreachable config. Kept simple/NULL
    -- instead of implying a merge behavior that never actually runs.
    ('full_name', 'full_name', 'Full Name', 'Customer full display name (SHA-256 hashed). NOT used as an identity-resolution matching key -- common/shared names (especially Vietnamese given+family names) create false-positive merge risk; use device_id/email/phone_number/national_id instead.', 'IDENTITY', 'cdp_master_profiles, cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 60),
    ('first_name', 'first_name', 'First Name', 'Given name.', 'IDENTITY', 'cdp_master_profiles', 'TEXT', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 70),
    ('last_name', 'last_name', 'Last Name', 'Family name.', 'IDENTITY', 'cdp_master_profiles', 'TEXT', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 80),
    ('profile_picture_url', 'profile_picture_url', 'Profile Picture URL', 'URL to the customer avatar or profile image.', 'IDENTITY', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 82),
    ('is_hashed', 'is_hashed', 'PII Is Hashed', 'True if full_name/email/phone_number/national_id are SHA-256 hashed (hashed-match ingestion). When TRUE, persona_name is required.', 'IDENTITY', 'cdp_master_profiles', 'BOOLEAN', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 85),
    ('email', 'email', 'Email Address', 'Primary email; identity-resolution matching key (exact, SHA-256 hashed).', 'IDENTITY', 'cdp_master_profiles, cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 90),
    ('phone_number', 'phone_number', 'Phone Number', 'Primary phone; identity-resolution matching key (exact, SHA-256 hashed).', 'IDENTITY', 'cdp_master_profiles, cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 100),
    ('secondary_emails', 'secondary_emails', 'Secondary Emails', 'Additional emails, e.g. [{"email":"work@abc.com","label":"work"}].', 'IDENTITY', 'cdp_master_profiles', 'JSONB', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 110),
    ('secondary_phones', 'secondary_phones', 'Secondary Phones', 'Additional phone numbers, e.g. [{"phone":"+84901234567","label":"home"}].', 'IDENTITY', 'cdp_master_profiles', 'JSONB', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 120),
    ('date_of_birth', 'date_of_birth', 'Date of Birth', 'Customer date of birth.', 'IDENTITY', 'cdp_master_profiles', 'DATE', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, NULL, NULL, NULL, NULL, 130),
    ('gender', 'gender', 'Gender', 'male, female, or other.', 'IDENTITY', 'cdp_master_profiles', 'TEXT', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 140),
    ('address', 'address', 'Address', 'Flexible address document, e.g. {"street":"123 Le Loi","city":"Ho Chi Minh","country":"VN"}.', 'IDENTITY', 'cdp_master_profiles', 'JSONB', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 150),

    -- Fuzzy matching on address components (raw stage columns, consolidated to address JSONB on master)
    ('address_line1', 'address', 'Address Line 1 (raw)', 'Street address line 1 on cdp_raw_profiles_stage; identity-resolution matching key (fuzzy_trgm), consolidated into address.', 'IDENTITY', 'cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', TRUE, 'fuzzy_trgm', 0.60, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 151),
    ('address_line2', 'address', 'Address Line 2 (raw)', 'Street address line 2 (suite/apt) on cdp_raw_profiles_stage; optional fuzzy matching key.', 'IDENTITY', 'cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 152),
    ('city', 'address', 'City (raw)', 'City on cdp_raw_profiles_stage; identity-resolution matching key (exact), consolidated into address.', 'IDENTITY', 'cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 153),
    ('state_province', 'address', 'State/Province (raw)', 'State or province on cdp_raw_profiles_stage; supporting field for address context.', 'IDENTITY', 'cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 154),
    ('postal_code', 'address', 'Postal Code (raw)', 'Postal/ZIP code on cdp_raw_profiles_stage; exact matching key for address disambiguation.', 'IDENTITY', 'cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 155),
    ('country', 'address', 'Country (raw)', 'ISO-3166 country code on cdp_raw_profiles_stage; exact matching key for address disambiguation.', 'IDENTITY', 'cdp_raw_profiles_stage', 'TEXT', 'all', TRUE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 156),
    ('company_name', 'company_name', 'Company Name (raw)', 'Company/employer name on cdp_raw_profiles_stage; identity-resolution matching key (fuzzy_trgm), consolidated to master.', 'IDENTITY', 'cdp_raw_profiles_stage', 'TEXT', 'all', FALSE, 'ACTIVE', TRUE, 'fuzzy_trgm', 0.65, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 157),

    -- IDENTITY_GRAPH (cross-channel device/ad/cookie/external identifiers)
    ('external_ids', 'external_ids', 'External System IDs', 'Map of source_system to that source external customer id (deterministic matching).', 'IDENTITY_GRAPH', 'cdp_master_profiles', 'JSONB', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 160),
    ('external_customer_id', 'external_ids', 'External Customer ID (raw)', 'Per-source customer id on cdp_raw_profiles_stage (AppsFlyer customer_user_id / core banking CIF / loyalty_id); identity-resolution matching key, consolidated into external_ids.', 'IDENTITY_GRAPH', 'cdp_raw_profiles_stage', 'TEXT', 'all', FALSE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 170),
    ('device_ids', 'device_ids', 'Device IDs', 'Consolidated array of device identifiers (IDFV/Android ID/app instance id).', 'IDENTITY_GRAPH', 'cdp_master_profiles', 'ARRAY', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 180),
    ('device_id', 'device_ids', 'Device ID (raw)', 'Raw per-event device id on cdp_raw_profiles_stage; identity-resolution matching key, consolidated into device_ids.', 'IDENTITY_GRAPH', 'cdp_raw_profiles_stage', 'TEXT', 'all', FALSE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 190),
    ('advertising_ids', 'advertising_ids', 'Advertising IDs', 'Consolidated array of mobile advertising identifiers (IDFA/GAID) for retargeting.', 'IDENTITY_GRAPH', 'cdp_master_profiles', 'ARRAY', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 200),
    ('advertising_id', 'advertising_ids', 'Advertising ID (raw)', 'Raw per-event advertising id on cdp_raw_profiles_stage; identity-resolution matching key, consolidated into advertising_ids.', 'IDENTITY_GRAPH', 'cdp_raw_profiles_stage', 'TEXT', 'all', FALSE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 210),
    ('cookie_ids', 'cookie_ids', 'Cookie IDs', 'Consolidated array of anonymous browser cookies for web session stitching.', 'IDENTITY_GRAPH', 'cdp_master_profiles', 'ARRAY', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 220),
    ('cookie_id', 'cookie_ids', 'Cookie ID (raw)', 'Raw per-event web cookie id on cdp_raw_profiles_stage; identity-resolution matching key, consolidated into cookie_ids.', 'IDENTITY_GRAPH', 'cdp_raw_profiles_stage', 'TEXT', 'all', FALSE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 230),
    ('push_tokens', 'push_tokens', 'Push Notification Tokens', 'Stored push tokens, e.g. {"fcm":"token","apns":"token"}.', 'IDENTITY_GRAPH', 'cdp_master_profiles', 'JSONB', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 240),
    ('attributes', 'attributes', 'Custom Attributes', 'Schemaless payload of dynamically extracted traits (e.g. occupation, income_segment).', 'MARKETING', 'cdp_master_profiles', 'JSONB', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 245),

    -- RETAIL
    ('loyalty_id', 'loyalty_id', 'Loyalty ID', 'Retail loyalty program membership identifier.', 'RETAIL', 'cdp_master_profiles', 'TEXT', 'retail', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 250),
    ('membership_tier', 'membership_tier', 'Membership Tier', 'Loyalty program tier (e.g. Silver/Gold/Platinum).', 'RETAIL', 'cdp_master_profiles', 'TEXT', 'retail', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'tier', NULL, NULL, NULL, 260),
    ('preferred_store_code', 'preferred_store_code', 'Preferred Store Code', 'Physical store the customer shops at most often.', 'RETAIL', 'cdp_master_profiles', 'TEXT', 'retail', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 270),

    -- BANKING
    ('national_id', 'national_id', 'National ID / KYC ID', 'CMND/CCCD/passport number; identity-resolution matching key (exact, SHA-256 hashed).', 'BANKING', 'cdp_master_profiles, cdp_raw_profiles_stage', 'TEXT', 'banking', TRUE, 'ACTIVE', TRUE, 'exact', NULL, 'non_null', FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 280),
    ('cif_number', 'cif_number', 'Core Banking CIF Number', 'Customer Information File number; the golden record id in legacy core banking.', 'BANKING', 'cdp_master_profiles', 'TEXT', 'banking', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 290),
    ('account_numbers', 'account_numbers', 'Account Numbers', 'Array of active bank account numbers associated with this CIF.', 'BANKING', 'cdp_master_profiles', 'ARRAY', 'banking', TRUE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 300),
    ('kyc_status', 'kyc_status', 'KYC Status', 'unverified, pending, verified, or rejected.', 'BANKING', 'cdp_master_profiles', 'TEXT', 'banking', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 310),
    ('risk_segment', 'risk_segment', 'Risk Segment', 'AML/credit risk categorization.', 'BANKING', 'cdp_master_profiles', 'TEXT', 'banking', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 320),

    -- REAL ESTATE
    ('property_types_of_interest', 'property_types_of_interest', 'Property Types of Interest', 'Real-estate property types the prospect is interested in (e.g. apartment, villa, land).', 'REAL_ESTATE', 'cdp_master_profiles', 'ARRAY', 'real_estate', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 321),
    ('preferred_location_codes', 'preferred_location_codes', 'Preferred Location Codes', 'Preferred city/district/area codes for property search.', 'REAL_ESTATE', 'cdp_master_profiles', 'ARRAY', 'real_estate', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 322),

    -- TRAVEL
    ('travel_loyalty_program_id', 'travel_loyalty_program_id', 'Travel Loyalty Program ID', 'Travel loyalty program membership identifier.', 'TRAVEL', 'cdp_master_profiles', 'TEXT', 'travel', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 323),
    ('preferred_travel_class', 'preferred_travel_class', 'Preferred Travel Class', 'Preferred cabin/travel class (e.g. economy, business, first).', 'TRAVEL', 'cdp_master_profiles', 'TEXT', 'travel', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 324),

    -- MEDIA
    ('media_subscription_id', 'media_subscription_id', 'Media Subscription ID', 'Media platform subscription or account identifier.', 'MEDIA', 'cdp_master_profiles', 'TEXT', 'media', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 325),
    ('preferred_content_genres', 'preferred_content_genres', 'Preferred Content Genres', 'Content genres the user prefers (e.g. news, sports, entertainment).', 'MEDIA', 'cdp_master_profiles', 'ARRAY', 'media', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 326),

    -- EDUCATION
    ('student_id', 'student_id', 'Student ID', 'Student identifier issued by the education institution or learning platform.', 'EDUCATION', 'cdp_master_profiles', 'TEXT', 'education', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 327),
    ('institution_name', 'institution_name', 'Institution Name', 'Name of the education institution or learning platform.', 'EDUCATION', 'cdp_master_profiles', 'TEXT', 'education', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 328),

    -- MARKETING
    ('acquisition_source', 'acquisition_source', 'Acquisition Source', 'First-touch channel attribution (e.g. organic_search, paid_social).', 'MARKETING', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 330),
    ('acquisition_campaign', 'acquisition_campaign', 'Acquisition Campaign', 'First-touch campaign attribution.', 'MARKETING', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 340),
    ('persona_name', 'persona_name', 'Persona Name', 'Human-readable, non-PII label for segmentation/marketing and semantic search (e.g. "Gen Z Shopper"). Required whenever is_hashed = TRUE; auto-generated by backend-system/identity_resolution when real PII is hashed.', 'MARKETING', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 345),
    ('persona_embedding', NULL, 'Persona Embedding', 'LLM-generated embedding used for semantic search / lookalike modeling. Lives on cdp_customer_personas (joined via cdp_master_profiles.current_persona_id), not on cdp_master_profiles directly.', 'MARKETING', 'cdp_customer_personas', 'VECTOR', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 350),
    ('segmentation_tags', 'segmentation_tags', 'Segmentation Tags', 'Computed labels for fast Audience Builder queries (e.g. gen_z, frequent_buyer).', 'MARKETING', 'cdp_master_profiles', 'ARRAY', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, NULL, 360),
    ('communication_preferences', 'communication_preferences', 'Communication Preferences', 'Tracks explicit user consent across multiple channels. Essential for omnichannel marketing compliance (e.g., GDPR, PDPA) before activating campaigns. Format: {"email_opt_in": true, "sms_opt_in": false, "push_opt_in": true}', 'MARKETING', 'cdp_master_profiles', 'JSONB', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 370),

    -- LINEAGE
    ('source_systems', 'source_systems', 'Source Systems', 'All external systems that have contributed data to this profile.', 'LINEAGE', 'cdp_master_profiles', 'ARRAY', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 380),
    ('first_seen_raw_profile_id', 'first_seen_raw_profile_id', 'First Seen Raw Profile ID', 'Lineage pointer back to the raw_profile_id that initiated this profile.', 'LINEAGE', 'cdp_master_profiles', 'UUID', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'identifier', NULL, NULL, NULL, 390),

    -- LIFECYCLE (prospect -> lead -> customer journey tracking)
    ('customer_since', 'customer_since', 'Customer Since', 'Date the profile first converted from lead/prospect to paying customer.', 'LIFECYCLE', 'cdp_master_profiles', 'DATE', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'timestamp', NULL, NULL, NULL, 391),
    ('last_activity_at', 'last_activity_at', 'Last Activity At', 'Timestamp of the most recent activity across any channel; updated continuously by the streaming pipeline.', 'LIFECYCLE', 'cdp_master_profiles', 'TIMESTAMP', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'timestamp', NULL, NULL, 'realtime', 392),
    ('preferred_channel', 'preferred_channel', 'Preferred Channel', 'Channel the customer engages with most (e.g. Mobile App, Website, Internet Banking App); used for recommendation/next-best-action.', 'LIFECYCLE', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'label', NULL, NULL, 'daily', 393),
    ('lifecycle_stage', 'lifecycle_stage', 'Lifecycle Stage', 'Current stage in the prospect-to-customer journey (prospect, lead, customer, vip, dormant, churn_risk).', 'LIFECYCLE', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'lifecycle_stage_model', 'v1', 'tier', NULL, NULL, 'daily', 394),
    ('persona_summary', 'persona_summary', 'Persona Summary', 'Longer narrative summary of the customer''s behavior/preferences, usually generated by an LLM or the segmentation pipeline; complements persona_name.', 'LIFECYCLE', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'persona_summary_generator', 'v1', 'label', NULL, NULL, 'batch', 395),

    -- LEAD & CONVERSION SCORING
    ('lead_conversion_probability', 'lead_conversion_probability', 'Lead Conversion Probability', 'ML-predicted probability the profile converts or purchases a new product.', 'LEAD_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'lead_scoring_model', 'v1', 'probability', 0, 1, 'daily', 400),
    ('lead_grade', 'lead_grade', 'Lead Grade', 'Categorical grade (e.g. A/B, Hot/Cold) derived from lead_conversion_probability for quick segmentation.', 'LEAD_SCORING', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'lead_scoring_model', 'v1', 'tier', NULL, NULL, 'daily', 410),

    -- CHURN SCORING
    ('churn_probability', 'churn_probability', 'Churn Probability', 'ML-predicted probability the user stops using the service/bank.', 'CHURN_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'churn_scoring_model', 'v1', 'probability', 0, 1, 'daily', 420),
    ('churn_risk_tier', 'churn_risk_tier', 'Churn Risk Tier', 'Bucketized churn risk (low/medium/high/critical) for marketing automation.', 'CHURN_SCORING', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'churn_scoring_model', 'v1', 'tier', NULL, NULL, 'daily', 430),

    -- CUSTOMER LIFETIME VALUE (CLV) SCORING
    ('historical_clv', 'historical_clv', 'Historical CLV', 'Actual realized revenue/profit to date.', 'CLV_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'currency', 0, NULL, 'weekly', 440),
    ('predictive_clv', 'predictive_clv', 'Predictive CLV', 'ML-predicted future revenue generation.', 'CLV_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'clv_scoring_model', 'v1', 'currency', 0, NULL, 'weekly', 450),
    ('clv_segment', 'clv_segment', 'CLV Segment', 'Combined or segmented CLV tier (e.g. high/medium/low value).', 'CLV_SCORING', 'cdp_master_profiles', 'TEXT', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'clv_scoring_model', 'v1', 'tier', NULL, NULL, 'weekly', 460),

    -- CUSTOMER EXPERIENCE (CX) & ENGAGEMENT SCORING
    ('engagement_score', 'engagement_score', 'Engagement Score', 'Overall interaction frequency/depth score.', 'CX_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'cx_scoring_model', 'v1', 'score', 0, 100, 'daily', 470),
    ('latest_nps_score', 'latest_nps_score', 'Latest NPS Score', 'Most recent Net Promoter Score.', 'CX_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'cx_scoring_model', 'v1', 'score', 0, 10, 'event_driven', 480),
    ('average_csat', 'average_csat', 'Average CSAT', 'Average Customer Satisfaction Score across interactions.', 'CX_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'cx_scoring_model', 'v1', 'score', 0, 5, 'daily', 490),
    ('overall_sentiment_score', 'overall_sentiment_score', 'Overall Sentiment Score', 'NLP-derived sentiment from support tickets and social mentions.', 'CX_SCORING', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'cx_scoring_model', 'v1', 'sentiment', -1, 1, 'daily', 500),

    -- DATA QUALITY & IDENTITY RESOLUTION SCORING
    ('profile_completeness_score', 'profile_completeness_score', 'Profile Completeness Score', 'Percentage of critical profile fields filled out.', 'DATA_QUALITY', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'data_quality_model', 'v1', 'percentage', 0, 100, 'daily', 510),
    ('identity_confidence_score', 'identity_confidence_score', 'Identity Confidence Score', 'Confidence score of the identity-stitching (CIR) algorithm.', 'DATA_QUALITY', 'cdp_master_profiles', 'NUMERIC', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, TRUE, 'identity_resolution_scoring_model', 'v1', 'probability', 0, 1, 'realtime', 520),
    ('model_versions', 'model_versions', 'Model Versions', 'Tracks which ML model versions generated the current scores, e.g. {"churn_model":"v2.1","clv_model":"v1.4"}.', 'DATA_QUALITY', 'cdp_master_profiles', 'JSONB', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'metadata', NULL, NULL, NULL, 530),
    ('scores_updated_at', 'scores_updated_at', 'Scores Updated At', 'Last time the batch or streaming pipelines updated the scoring fields.', 'DATA_QUALITY', 'cdp_master_profiles', 'TIMESTAMP', 'all', FALSE, 'ACTIVE', FALSE, NULL, NULL, NULL, FALSE, NULL, NULL, 'timestamp', NULL, NULL, NULL, 540)

-- Conflict handling to safely upsert existing definitions
ON CONFLICT (attribute_internal_code) DO UPDATE SET
    master_profile_column  = EXCLUDED.master_profile_column,
    name                   = EXCLUDED.name,
    description            = EXCLUDED.description,
    attribute_group        = EXCLUDED.attribute_group,
    source_table           = EXCLUDED.source_table,
    data_type              = EXCLUDED.data_type,
    domain_scope           = EXCLUDED.domain_scope,
    is_pii                 = EXCLUDED.is_pii,
    status                 = EXCLUDED.status,
    is_identity_resolution = EXCLUDED.is_identity_resolution,
    matching_rule          = EXCLUDED.matching_rule,
    matching_threshold     = EXCLUDED.matching_threshold,
    consolidation_rule     = EXCLUDED.consolidation_rule,
    is_scoring_model       = EXCLUDED.is_scoring_model,
    scoring_model_name     = EXCLUDED.scoring_model_name,
    scoring_model_version  = EXCLUDED.scoring_model_version,
    value_type             = EXCLUDED.value_type,
    value_min              = EXCLUDED.value_min,
    value_max              = EXCLUDED.value_max,
    refresh_frequency      = EXCLUDED.refresh_frequency,
    display_order          = EXCLUDED.display_order,
    updated_at             = now();

-- Default CIR merge policy seed: lets the resolver prefer recent or
-- verified values without hard-coding a single merge strategy in Python.
-- full_name is intentionally excluded (is_identity_resolution=FALSE means
-- resolver.py never loads a consolidation_rule for it either -- see the
-- comment on its INSERT row above).
UPDATE customer360.cdp_profile_attributes
SET
    consolidation_rule = CASE attribute_internal_code
        WHEN 'email' THEN 'verified_first'
        WHEN 'phone_number' THEN 'verified_first'
        WHEN 'national_id' THEN 'verified_first'
        WHEN 'external_customer_id' THEN 'source_priority'
        WHEN 'device_id' THEN 'source_priority'
        WHEN 'advertising_id' THEN 'source_priority'
        WHEN 'cookie_id' THEN 'source_priority'
        ELSE consolidation_rule
    END,
    consolidation_config = CASE attribute_internal_code
        WHEN 'email' THEN '{"mode":"verified_first","verified_field":"kyc_status","verified_values":["verified"],"verified_event_names":["kyc-completed"],"fallback_mode":"most_recent","timestamp_field":"updated_at"}'::jsonb
        WHEN 'phone_number' THEN '{"mode":"verified_first","verified_field":"kyc_status","verified_values":["verified"],"verified_event_names":["kyc-completed"],"fallback_mode":"most_recent","timestamp_field":"updated_at"}'::jsonb
        WHEN 'national_id' THEN '{"mode":"verified_first","verified_field":"kyc_status","verified_values":["verified"],"verified_event_names":["kyc-completed"],"fallback_mode":"most_recent","timestamp_field":"updated_at"}'::jsonb
        WHEN 'external_customer_id' THEN '{"mode":"source_priority","source_priority":["CoreBanking","POS","WebTracking","AppsFlyer","MoEngage"]}'::jsonb
        WHEN 'device_id' THEN '{"mode":"source_priority","source_priority":["WebTracking","AppsFlyer","MoEngage"]}'::jsonb
        WHEN 'advertising_id' THEN '{"mode":"source_priority","source_priority":["WebTracking","AppsFlyer","MoEngage"]}'::jsonb
        WHEN 'cookie_id' THEN '{"mode":"source_priority","source_priority":["WebTracking"]}'::jsonb
        ELSE consolidation_config
    END,
    updated_at = now()
WHERE attribute_internal_code IN (
    'email',
    'phone_number',
    'national_id',
    'external_customer_id',
    'device_id',
    'advertising_id',
    'cookie_id'
);

-- CIR rule-engine seed: priority ranking (used for limit-demotion), max
-- allowed values per master profile, and their enforcement window. Higher-
-- assurance/lower-churn identifiers (KYC id, per-source customer id) get
-- rank 1 and a tight value_limit; loosely-scoped/high-churn identifiers
-- (cookies) get a lower rank and a shorter, weekly-refreshed window.
UPDATE customer360.cdp_profile_attributes
SET
    priority_rank = CASE attribute_internal_code
        WHEN 'external_customer_id' THEN 1
        WHEN 'national_id' THEN 1
        WHEN 'email' THEN 2
        WHEN 'phone_number' THEN 2
        WHEN 'device_id' THEN 3
        WHEN 'advertising_id' THEN 3
        WHEN 'cookie_id' THEN 4
        ELSE priority_rank
    END,
    value_limit = CASE attribute_internal_code
        WHEN 'external_customer_id' THEN 1
        WHEN 'national_id' THEN 1
        WHEN 'email' THEN 5
        WHEN 'phone_number' THEN 5
        WHEN 'device_id' THEN 5
        WHEN 'advertising_id' THEN 5
        WHEN 'cookie_id' THEN 10
        ELSE value_limit
    END,
    limit_timeframe = CASE attribute_internal_code
        WHEN 'external_customer_id' THEN '1_ever'
        WHEN 'national_id' THEN '1_ever'
        WHEN 'cookie_id' THEN '5_weekly'
        ELSE '5_annually'
    END,
    updated_at = now()
WHERE attribute_internal_code IN (
    'email',
    'phone_number',
    'national_id',
    'external_customer_id',
    'device_id',
    'advertising_id',
    'cookie_id'
);


---------------------------------------------------
-- SEGMENTS: SEED DATA (Audience Builder)
---------------------------------------------------

-- Default segmentation tags for demo tenant. Idempotent: safe to re-run.
-- Each segment defines a named audience via jQuery QueryBuilder rule tree
-- (json_rules), translated WHERE-clause fragment (sql_rules), and full
-- executable query (final_generated_sql).
INSERT INTO customer360.cdp_segments (
    tenant_id,
    segment_tag,
    segment_name,
    description,
    json_rules,
    sql_rules,
    final_generated_sql,
    processed_by,
    is_active,
    member_count,
    status_code
) VALUES
    -- New Customers: became a paying customer in the last 30 days
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'new_customer',
        'New Customers',
        'Profiles that became a paying customer in the last 30 days.',
        '{"condition": "AND", "rules": [{"field": "customer_since", "operator": "greater_or_equal", "value": "-30 days"}]}'::jsonb,
        'customer_since >= (CURRENT_DATE - INTERVAL ''30 days'')',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (customer_since >= (CURRENT_DATE - INTERVAL ''30 days''))',
        'human',
        TRUE,
        0,
        1
    ),
    -- High-Value Customers: predictive CLV above 1000
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'high_value',
        'High-Value Customers',
        'Profiles with predictive customer lifetime value above 1000.',
        '{"condition": "AND", "rules": [{"field": "predictive_clv", "operator": "greater", "value": 1000}]}'::jsonb,
        'predictive_clv > 1000',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (predictive_clv > 1000)',
        'human',
        TRUE,
        0,
        1
    ),
    -- At Risk of Churn: high or critical churn risk tier
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'churn_risk',
        'At Risk of Churn',
        'Profiles with a high or critical churn risk tier.',
        '{"condition": "AND", "rules": [{"field": "churn_risk_tier", "operator": "in", "value": ["high", "critical"]}]}'::jsonb,
        'churn_risk_tier IN (''high'', ''critical'')',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (churn_risk_tier IN (''high'', ''critical''))',
        'human',
        TRUE,
        0,
        1
    ),
    -- Dormant Profiles: no activity in the last 90 days
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'dormant',
        'Dormant Profiles',
        'Profiles with no activity in the last 90 days.',
        '{"condition": "AND", "rules": [{"field": "last_activity_at", "operator": "less", "value": "-90 days"}]}'::jsonb,
        'last_activity_at < (now() - INTERVAL ''90 days'')',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (last_activity_at < (now() - INTERVAL ''90 days''))',
        'human',
        TRUE,
        0,
        1
    ),
    -- Recently Active: profiles active in the last 30 days
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'recently_active',
        'Recently Active',
        'Profiles active in the last 30 days.',
        '{"condition": "AND", "rules": [{"field": "last_activity_at", "operator": "greater_or_equal", "value": "-30 days"}]}'::jsonb,
        'last_activity_at >= (now() - INTERVAL ''30 days'')',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (last_activity_at >= (now() - INTERVAL ''30 days''))',
        'human',
        TRUE,
        0,
        1
    ),
    -- Growth Potential: mid-value profiles (500-1000 CLV) with room to grow
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'growth_potential',
        'Growth Potential',
        'Mid-value profiles with room to grow into high-value customers.',
        '{"condition": "AND", "rules": [{"field": "predictive_clv", "operator": "greater_or_equal", "value": 500}, {"field": "predictive_clv", "operator": "less", "value": 1001}]}'::jsonb,
        'predictive_clv >= 500 AND predictive_clv < 1001',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (predictive_clv >= 500 AND predictive_clv < 1001)',
        'human',
        TRUE,
        0,
        1
    ),
    -- Win-Back Candidates: inactive 30-180 days with elevated churn risk
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'win_back',
        'Win-Back Candidates',
        'Profiles inactive for 30-180 days with elevated churn risk.',
        '{"condition": "AND", "rules": [{"field": "last_activity_at", "operator": "less", "value": "-30 days"}, {"field": "last_activity_at", "operator": "greater", "value": "-180 days"}, {"field": "churn_risk_tier", "operator": "in", "value": ["medium", "high", "critical"]}]}'::jsonb,
        'last_activity_at < (now() - INTERVAL ''30 days'') AND last_activity_at > (now() - INTERVAL ''180 days'') AND churn_risk_tier IN (''medium'', ''high'', ''critical'')',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (last_activity_at < (now() - INTERVAL ''30 days'') AND last_activity_at > (now() - INTERVAL ''180 days'') AND churn_risk_tier IN (''medium'', ''high'', ''critical''))',
        'human',
        TRUE,
        0,
        1
    ),
    -- Champions: long-tenure, top-value customers (CLV > 2500, tenure > 365 days)
    (
        '11111111-1111-1111-1111-111111111111'::uuid,
        'champions',
        'Champions',
        'Long-tenure, top-value customers to prioritize for loyalty experiences.',
        '{"condition": "AND", "rules": [{"field": "predictive_clv", "operator": "greater", "value": 2500}, {"field": "customer_since", "operator": "less", "value": "-365 days"}]}'::jsonb,
        'predictive_clv > 2500 AND customer_since < (CURRENT_DATE - INTERVAL ''365 days'')',
        'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = ''11111111-1111-1111-1111-111111111111''::uuid AND (predictive_clv > 2500 AND customer_since < (CURRENT_DATE - INTERVAL ''365 days''))',
        'human',
        TRUE,
        0,
        1
    )
ON CONFLICT (tenant_id, segment_tag) DO UPDATE SET
    segment_name        = EXCLUDED.segment_name,
    description         = EXCLUDED.description,
    json_rules          = EXCLUDED.json_rules,
    sql_rules           = EXCLUDED.sql_rules,
    final_generated_sql = EXCLUDED.final_generated_sql,
    processed_by        = EXCLUDED.processed_by,
    is_active           = EXCLUDED.is_active,
    status_code         = EXCLUDED.status_code,
    updated_at          = now();