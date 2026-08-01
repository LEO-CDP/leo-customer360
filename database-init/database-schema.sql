-- =========================================================
-- Extensions
-- =========================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE EXTENSION IF NOT EXISTS vector;
-- Geo support for domain events that need location (real estate listings,
-- retail store/POS locations, travel destinations, bank branches). Already
-- present in the dev image (postgis/postgis:16-3.5, see dev-start-pgsql.sh).
CREATE EXTENSION IF NOT EXISTS postgis;

-- =========================================================
-- Schema
-- =========================================================
CREATE SCHEMA IF NOT EXISTS customer360;

---------------------------------------------------
-- ENTITY TABLES
---------------------------------------------------

-- ==========================================================
-- Tenant table
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.sys_tenant (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_code VARCHAR(50) UNIQUE NOT NULL,
    tenant_name TEXT NOT NULL,
    company_name TEXT NOT NULL,
    business_type TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    metadata JSONB
);

COMMENT ON TABLE customer360.sys_tenant IS 'Top-level workspace/tenant record. Every tenant-scoped table in this schema carries a NOT NULL tenant_id FK to this table, enforced additionally via Row-Level Security (see the ROW LEVEL SECURITY section at the end of this file).';

-- ==========================================================
-- Business Domain Master
-- System-defined business domains
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.sys_domain (
    domain_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    domain_code VARCHAR(50) NOT NULL UNIQUE,
    domain_name VARCHAR(200) NOT NULL,

    description TEXT,

    icon VARCHAR(100),
    color VARCHAR(20),

    display_order SMALLINT DEFAULT 0 NOT NULL,

    is_system BOOLEAN DEFAULT TRUE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),

    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sys_domain_active
    ON customer360.sys_domain(is_active);

CREATE INDEX IF NOT EXISTS idx_sys_domain_display_order
    ON customer360.sys_domain(display_order);

CREATE INDEX IF NOT EXISTS idx_sys_domain_metadata
    ON customer360.sys_domain
    USING GIN(metadata);

COMMENT ON TABLE customer360.sys_domain IS 'System-defined business domains (e.g., retail, banking, real_estate, travel, media, education).';

-- ==========================================================
-- Tenant Business Domains
-- A tenant can support multiple industries/domains.
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.sys_tenant_domain (

    tenant_id UUID NOT NULL,
    domain_id UUID NOT NULL,

    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP NOT NULL DEFAULT now(),
    metadata JSONB DEFAULT '{}'::jsonb,

    PRIMARY KEY (tenant_id, domain_id),

    CONSTRAINT fk_sys_tenant_domain_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES customer360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_sys_tenant_domain_domain
        FOREIGN KEY (domain_id)
        REFERENCES customer360.sys_domain(domain_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_sys_tenant_domain_tenant
ON customer360.sys_tenant_domain(tenant_id);

CREATE INDEX IF NOT EXISTS idx_sys_tenant_domain_domain
ON customer360.sys_tenant_domain(domain_id);

CREATE INDEX IF NOT EXISTS idx_sys_tenant_domain_default
ON customer360.sys_tenant_domain(tenant_id, is_default);

-- ==========================================================
-- Organization table
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.sys_organization (
    organization_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    parent_organization_id UUID NULL REFERENCES customer360.sys_organization (organization_id),
    organization_code VARCHAR(100) NOT NULL,
    organization_name VARCHAR(255) NOT NULL,
    organization_type VARCHAR(50), -- COMPANY, DIVISION, BRANCH, DEPARTMENT
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    metadata JSONB,
    CONSTRAINT uq_org_code UNIQUE (tenant_id, organization_code)
);

COMMENT ON TABLE customer360.sys_organization IS 'Hierarchical business unit within a tenant (COMPANY/DIVISION/BRANCH/DEPARTMENT), self-referencing via parent_organization_id. Used to scope sys_user membership below the tenant level.';

CREATE INDEX idx_org_tenant ON customer360.sys_organization (tenant_id);

CREATE INDEX idx_org_parent ON customer360.sys_organization (parent_organization_id);

-- ==========================================================
-- Application User table
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.sys_user (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    organization_id UUID NULL REFERENCES customer360.sys_organization (organization_id),
    keycloak_user_id UUID UNIQUE,
    username VARCHAR(150) NOT NULL,
    email VARCHAR(255),
    full_name VARCHAR(255),
    phone VARCHAR(30),
    job_title VARCHAR(100),
    department VARCHAR(100),
    language_code VARCHAR(10) DEFAULT 'en',
    timezone VARCHAR(50),
    status VARCHAR(20) DEFAULT 'ACTIVE',
    last_login_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    metadata JSONB,
    CONSTRAINT uq_username UNIQUE (tenant_id, username),
    CONSTRAINT uq_email UNIQUE (tenant_id, email)
);

COMMENT ON TABLE customer360.sys_user IS 'Internal application user/staff account, optionally backed by a Keycloak SSO identity (keycloak_user_id). Referenced as the nullable "data owner" (user_id) on most crm_*/cdp_* tables -- NULL means the row was created by an ingestion pipeline rather than an interactive admin user.';

CREATE INDEX idx_user_tenant ON customer360.sys_user (tenant_id);

CREATE INDEX idx_user_org ON customer360.sys_user (organization_id);

CREATE INDEX idx_user_keycloak ON customer360.sys_user (keycloak_user_id);

CREATE TABLE IF NOT EXISTS customer360.sys_role (
    role_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    role_code VARCHAR(100) NOT NULL,
    role_name VARCHAR(255) NOT NULL,
    description TEXT,
    is_system_role BOOLEAN DEFAULT FALSE,
    status VARCHAR(20) DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    metadata JSONB,
    CONSTRAINT uq_role_code UNIQUE (tenant_id, role_code)
);

COMMENT ON TABLE customer360.sys_role IS 'RBAC role definition scoped to a tenant (e.g. Admin, Marketer, Analyst). Granted permissions via sys_role_permission and assigned to users via sys_user_role.';

CREATE INDEX idx_role_tenant ON customer360.sys_role (tenant_id);

CREATE TABLE IF NOT EXISTS customer360.sys_permission (
    permission_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    permission_code VARCHAR(150) UNIQUE NOT NULL,
    resource VARCHAR(100) NOT NULL,
    action VARCHAR(50) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT now(),
    metadata JSONB
);

COMMENT ON TABLE customer360.sys_permission IS 'Global RBAC permission dictionary (resource + action pair, e.g. profile/read, campaign/write). Shared vocabulary across all tenants -- no tenant_id, same pattern as the other reference dictionaries in this schema.';

CREATE TABLE IF NOT EXISTS customer360.sys_role_permission (
    role_id UUID NOT NULL REFERENCES customer360.sys_role (role_id) ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES customer360.sys_permission (permission_id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (role_id, permission_id)
);

COMMENT ON TABLE customer360.sys_role_permission IS 'Join table granting permissions (sys_permission) to roles (sys_role) -- many-to-many.';

CREATE TABLE IF NOT EXISTS customer360.sys_user_role (
    user_id UUID NOT NULL REFERENCES customer360.sys_user (user_id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES customer360.sys_role (role_id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT now(),
    assigned_by UUID,
    PRIMARY KEY (user_id, role_id)
);

COMMENT ON TABLE customer360.sys_user_role IS 'Join table assigning roles (sys_role) to users (sys_user) -- many-to-many.';

-- ==========================================================
-- Audit Log
-- Enterprise Multi-Tenant CDP
-- ==========================================================

CREATE TABLE IF NOT EXISTS customer360.sys_audit_log (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),

    -- Multi-tenant
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    organization_id UUID NULL,

    -- User
    user_id UUID NULL,
    username VARCHAR(150),
    session_id UUID NULL,

    -- Authentication
    auth_provider VARCHAR(50), -- Keycloak, AzureAD, Google, API_KEY
    auth_subject TEXT, -- JWT sub

    -- Action
    action VARCHAR(50) NOT NULL, -- CREATE, UPDATE, DELETE, LOGIN, EXPORT...
    resource_type VARCHAR(100) NOT NULL,
    resource_id TEXT,

    -- API
    service_name VARCHAR(100), -- customer360-api
    api_endpoint TEXT,
    http_method VARCHAR(10),
    http_status SMALLINT,

    -- Network
    ip_address INET,
    user_agent TEXT,

    -- Before / After
    before_data JSONB,
    after_data JSONB,

    -- Optional changed fields only
    changed_fields JSONB,

    -- Result
    success BOOLEAN DEFAULT TRUE,
    error_code VARCHAR(100),
    error_message TEXT,

    -- Traceability
    trace_id UUID,
    request_id UUID,
    correlation_id UUID,

    -- Geo (optional)
    country_code VARCHAR(5),
    timezone VARCHAR(50),

    -- Event time
    created_at TIMESTAMP NOT NULL DEFAULT now(),

    metadata JSONB
);

COMMENT ON TABLE customer360.sys_audit_log IS 'Compliance/audit trail: one row per user or API action (CREATE/UPDATE/DELETE/LOGIN/EXPORT/...) with before/after JSONB snapshots, auth provenance, request tracing IDs, and success/error outcome.';

-- Campaign
CREATE TABLE IF NOT EXISTS customer360.crm_campaign (
    campaign_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    name TEXT NOT NULL,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    start_date DATE,
    end_date DATE,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE customer360.crm_campaign IS 'CRM journey-graph entity: a marketing initiative (part of the Lead -> Contact -> Opportunity B2B journey). Responders are tracked via crm_campaign_member.';

-- CampaignMember
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_member (
    campaign_member_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    campaign_id UUID REFERENCES customer360.crm_campaign(campaign_id),
    contact_id UUID,
    status TEXT,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    metadata JSONB
);

COMMENT ON TABLE customer360.crm_campaign_member IS 'A person who responded to / joined a crm_campaign, optionally already linked to a crm_contact.';

-- Lead
CREATE TABLE IF NOT EXISTS customer360.crm_lead (
    lead_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    phone TEXT,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    metadata JSONB
);

COMMENT ON TABLE customer360.crm_lead IS 'CRM journey-graph entity: a potential buyer not yet tied to a crm_opportunity, sourced via crm_lead_source.';

-- Lead Source
CREATE TABLE IF NOT EXISTS customer360.crm_lead_source (
    lead_source_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    name TEXT NOT NULL,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    metadata JSONB
);

COMMENT ON TABLE customer360.crm_lead_source IS 'Dictionary of channels/origins that generate crm_lead rows (e.g. web form, trade show, referral).';

-- Contact
CREATE TABLE IF NOT EXISTS customer360.crm_contact (
    contact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    phone TEXT,
    account_id UUID,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    metadata JSONB
);

COMMENT ON TABLE customer360.crm_contact IS 'CRM journey-graph entity: a crm_lead engaged seriously by sales, belonging to a crm_account.';

-- Account
CREATE TABLE IF NOT EXISTS customer360.crm_account (
    account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    name TEXT NOT NULL,
    industry_id UUID,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    metadata JSONB
);

COMMENT ON TABLE customer360.crm_account IS 'CRM journey-graph entity: an organization/company, classified by crm_industry, that crm_contact and crm_opportunity rows belong to.';

-- Opportunity
CREATE TABLE IF NOT EXISTS customer360.crm_opportunity (
    opportunity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    account_id UUID REFERENCES customer360.crm_account(account_id),
    name TEXT,
    value NUMERIC,
    stage TEXT,
    close_date DATE,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    metadata JSONB
);

COMMENT ON TABLE customer360.crm_opportunity IS 'CRM journey-graph entity: a potential sales transaction tied to a crm_account, with monetary value/stage/close_date.';

-- Industry
CREATE TABLE IF NOT EXISTS customer360.crm_industry (
    industry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner
    name TEXT NOT NULL,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    metadata JSONB
);

COMMENT ON TABLE customer360.crm_industry IS 'Dictionary of industry classifications used to categorize crm_account rows.';

-- tenant_id indexes for the CRM entity tables above, used both for lookup
-- performance and by the tenant_id RLS policies (see ROW LEVEL SECURITY
-- section at the end of this file).
CREATE INDEX idx_crm_campaign_tenant ON customer360.crm_campaign (tenant_id);

CREATE INDEX idx_crm_campaign_member_tenant ON customer360.crm_campaign_member (tenant_id);

CREATE INDEX idx_crm_lead_tenant ON customer360.crm_lead (tenant_id);

CREATE INDEX idx_crm_lead_source_tenant ON customer360.crm_lead_source (tenant_id);

CREATE INDEX idx_crm_contact_tenant ON customer360.crm_contact (tenant_id);

CREATE INDEX idx_crm_account_tenant ON customer360.crm_account (tenant_id);

CREATE INDEX idx_crm_opportunity_tenant ON customer360.crm_opportunity (tenant_id);

CREATE INDEX idx_crm_industry_tenant ON customer360.crm_industry (tenant_id);

---------------------------------------------------
-- MASTER PROFILES & IDENTITY RESOLUTION
---------------------------------------------------

-- ============================================================================
-- LEO CDP MASTER PROFILE SCHEMA (PostgreSQL 16+)
-- ============================================================================
-- Description: Golden customer profile containing the consolidated ("resolved")
-- identity across multiple data sources (AppsFlyer, MoEngage, Web Tracking / GA4,
-- POS, Core Banking, etc.) for both retail and banking domains.
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer360.cdp_master_profiles (
    -- ------------------------------------------------------------------------
    -- SYSTEM & TENANT METADATA
    -- ------------------------------------------------------------------------
    master_profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Multi-tenancy support. Ensures data isolation between different workspaces.
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    -- Data owner: internal sys_user who created/manages this profile (nullable -- most
    -- profiles are created by ingestion pipelines, not an interactive admin user).
    user_id UUID REFERENCES customer360.sys_user(user_id),
    -- Business context of the profile to drive domain-specific UI and activation logic.
    domain TEXT NOT NULL DEFAULT 'retail' CHECK (domain IN ('retail', 'banking', 'real_estate', 'travel', 'media', 'education')),

    -- ------------------------------------------------------------------------
    -- CORE IDENTITY (PII & DEMOGRAPHICS)
    -- Standard demographic data used for personalization and primary matching.
    -- ------------------------------------------------------------------------
    full_name TEXT,
    first_name TEXT,
    last_name TEXT,
    profile_picture_url TEXT,
    -- True if full_name/email/phone_number/national_id are SHA-256 hashed for privacy
    -- (e.g. hashed-match ingestion a la Meta/Google Customer Match). Whenever TRUE,
    -- persona_name (below) MUST be populated -- see the CHECK constraint at the end of
    -- this table -- since hashed PII can no longer be used as a human-readable label for
    -- browsing/semantic search. persona_name is computed by application code (see
    -- backend-system/identity_resolution/identity_resolution/persona.py), never by the DB.
    is_hashed BOOLEAN DEFAULT FALSE,

    -- Primary contact info (used for primary identity stitching and marketing)
    email TEXT,
    phone_number TEXT,

    -- Secondary contact info
    -- Format: [{"email": "work@abc.com", "label": "work"}, {"email": "old@xyz.com", "label": "personal"}]
    secondary_emails JSONB DEFAULT '[]'::JSONB,
    -- Format: [{"phone": "+84901234567", "label": "home"}]
    secondary_phones JSONB DEFAULT '[]'::JSONB,
    
    date_of_birth DATE,
    gender TEXT CHECK (gender IN ('male','female','other')),
    -- Flexible JSON document for complex address storage 
    -- Format: {"street": "123 Le Loi", "city": "Ho Chi Minh", "country": "VN"}
    address JSONB,

    -- ------------------------------------------------------------------------
    -- CROSS-CHANNEL IDENTITY GRAPH
    -- Identifiers resolved and merged from cdp_raw_profiles_stage.
    -- ------------------------------------------------------------------------
    -- Maps a source_system to its own customer identifier (Deterministic matching).
    external_ids JSONB DEFAULT '{}'::JSONB,
    -- Hardware or app-specific identifiers for mobile attribution (IDFV, Android ID).
    device_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Mobile advertising identifiers for retargeting campaigns (AppsFlyer IDFA/GAID).
    advertising_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Anonymous browser cookies for web tracking and session stitching.
    cookie_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Stored tokens for push notification services (MoEngage, Firebase).
    -- Format: {"fcm": "token_string", "apns": "token_string"}
    push_tokens JSONB DEFAULT '{}'::JSONB,

    -- ------------------------------------------------------------------------
    -- RETAIL DOMAIN ATTRIBUTES
    -- Fields specific to e-commerce, POS, and physical retail operations.
    -- ------------------------------------------------------------------------
    loyalty_id TEXT,
    membership_tier TEXT,
    preferred_store_code TEXT,

    -- ------------------------------------------------------------------------
    -- BANKING DOMAIN ATTRIBUTES
    -- Highly regulated fields specific to Fintech and Core Banking systems.
    -- ------------------------------------------------------------------------
    -- National Identification (CMND/CCCD in Vietnam, or Passport number).
    national_id TEXT,
    -- Core Banking Customer Information File number. The golden record ID in legacy banking.
    cif_number TEXT,
    -- Array of active account numbers associated with this CIF.
    account_numbers TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Know Your Customer (eKYC/KYC) progression state.
    kyc_status TEXT CHECK (kyc_status IN ('unverified','pending','verified','rejected')),
    -- Risk categorization for AML or credit scoring.
    risk_segment TEXT,

    -- ------------------------------------------------------------------------
    -- REAL ESTATE DOMAIN ATTRIBUTES
    -- Fields specific to property search, listings, and real-estate CRM.
    -- ------------------------------------------------------------------------
    -- Types of properties the prospect is interested in (e.g. apartment, villa, land).
    property_types_of_interest TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Preferred city/district/area codes for property search.
    preferred_location_codes TEXT[] DEFAULT ARRAY[]::TEXT[],

    -- ------------------------------------------------------------------------
    -- TRAVEL DOMAIN ATTRIBUTES
    -- Fields specific to airlines, hotels, and travel loyalty programs.
    -- ------------------------------------------------------------------------
    -- Travel loyalty program membership identifier.
    travel_loyalty_program_id TEXT,
    -- Preferred cabin/travel class (e.g. economy, business, first).
    preferred_travel_class TEXT,

    -- ------------------------------------------------------------------------
    -- MEDIA DOMAIN ATTRIBUTES
    -- Fields specific to content subscriptions and media consumption.
    -- ------------------------------------------------------------------------
    -- Platform subscription or account identifier.
    media_subscription_id TEXT,
    -- Content genres the user prefers (e.g. news, sports, entertainment).
    preferred_content_genres TEXT[] DEFAULT ARRAY[]::TEXT[],

    -- ------------------------------------------------------------------------
    -- EDUCATION DOMAIN ATTRIBUTES
    -- Fields specific to learners, students, and education platforms.
    -- ------------------------------------------------------------------------
    -- Student identifier issued by the education institution/platform.
    student_id TEXT,
    -- Name of the education institution or learning platform.
    institution_name TEXT,

    -- ------------------------------------------------------------------------
    -- MARKETING & ENGAGEMENT
    -- Attribution data and computed fields used for audience building.
    -- ------------------------------------------------------------------------
    -- Persona Name for segmentation, marketing campaigns and semantic search (e.g., "Gen Z
    -- Shopper", "High-Value Investor"). REQUIRED whenever is_hashed = TRUE: once real PII is
    -- SHA-256 hashed, persona_name is the only human-readable label left to browse/search
    -- profiles by, so it is auto-generated by backend-system/identity_resolution (see persona.py)
    -- for every profile whose PII looks hashed.
    persona_name TEXT default 'anonymous_user',
    -- First-touch channel attribution (e.g., 'organic_search', 'paid_social').
    acquisition_source TEXT,
    -- First-touch campaign attribution.
    acquisition_campaign TEXT,
    -- Stored embeddings generated by LLMs for semantic search/lookalike modeling.
    persona_embedding vector(768),
    -- Computed labels for fast Audience Builder queries (e.g., 'gen_z', 'frequent_buyer').
    segmentation_tags TEXT[],
    -- Schemaless payload for flexible traits extracted dynamically.
    -- Format: {"occupation": "engineer", "income_segment": "high", "preferred_category": "electronics"}
    attributes JSONB DEFAULT '{}'::JSONB,

    -- ------------------------------------------------------------------------
    -- LINEAGE & AUDIT
    -- ------------------------------------------------------------------------
    -- Array of all external systems that have contributed data to this profile.
    source_systems TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Lineage pointer back to the raw_profile_id that initiated this profile.
    first_seen_raw_profile_id UUID,

    -- ------------------------------------------------------------------------
    -- CUSTOMER LIFECYCLE & ENGAGEMENT TRACKING
    -- The lead-to-customer journey can span months; these fields track where a
    -- profile currently sits in that journey and how fresh/actionable it is.
    -- ------------------------------------------------------------------------
    -- Date the profile first converted from lead/prospect to paying customer.
    customer_since DATE,
    -- Timestamp of the profile's most recent activity across any channel.
    -- Updated continuously by the streaming/event pipeline (not batch).
    last_activity_at TIMESTAMP,
    -- Channel the customer engages with most, used to drive recommendation/
    -- next-best-action logic (e.g. 'Mobile App', 'Website', 'Internet Banking App').
    preferred_channel TEXT,
    -- Current stage in the prospect-to-customer journey, for lifecycle marketing
    -- and reporting. Distinct from churn_risk_tier (a churn-model score) --
    -- 'churn_risk' here is a lifecycle bucket, not a probability.
    lifecycle_stage TEXT CHECK (
        lifecycle_stage IN (
            'prospect',
            'lead',
            'customer',
            'vip',
            'dormant',
            'churn_risk'
        )
    ),
    -- Longer, human-readable narrative summary of the customer (behavior,
    -- preferences, notable traits) usually generated by an LLM or the
    -- segmentation pipeline -- complements the short persona_name label above.
    persona_summary TEXT,

    -- ------------------------------------------------------------------------
    -- 🚀 ML & ANALYTICS SCORING MODELS
    -- Computed asynchronously by data pipelines / ML models.
    -- ------------------------------------------------------------------------

    -- 1. Lead & Conversion Scoring
    -- Propensity of the user to convert or purchase a new product (0.0000 to 1.0000)
    lead_conversion_probability NUMERIC(5, 4),
    -- Categorical grade (e.g., 'A', 'B', 'Hot', 'Cold') for quick segmentation
    lead_grade TEXT,

    -- 2. Churn Scoring
    -- Probability that the user will stop using the service/bank (0.0000 to 1.0000)
    churn_probability NUMERIC(5, 4),
    -- Bucketized risk level for marketing automation
    churn_risk_tier TEXT CHECK (
        churn_risk_tier IN (
            'low',
            'medium',
            'high',
            'critical'
        )
    ),

    -- 3. Customer Lifetime Value (CLV) Scoring
    -- Actual realized revenue/profit to date
    historical_clv NUMERIC(15, 2) DEFAULT 0.00,
    -- ML-predicted future revenue generation
    predictive_clv NUMERIC(15, 2),
    -- Combined or segmented CLV tier
    clv_segment TEXT,

    -- 4. Customer Experience (CX) & Engagement Scoring
    -- Overall interaction frequency/depth score (0 to 100)
    engagement_score NUMERIC(5, 2),
    -- Most recent Net Promoter Score (0 to 10)
    latest_nps_score INTEGER CHECK (
        latest_nps_score >= 0
        AND latest_nps_score <= 10
    ),
    -- Average Customer Satisfaction Score across interactions
    average_csat NUMERIC(3, 2),
    -- NLP-derived sentiment from support tickets and social mentions (-1.0 to 1.0)
    overall_sentiment_score NUMERIC(5, 4),

    -- 5. Data Quality & Identity Resolution Scoring
    -- Percentage of critical profile fields filled out (0 to 100)
    profile_completeness_score NUMERIC(5, 2),
    -- Confidence score of the identity stitching algorithm (0.0000 to 1.0000)
    identity_confidence_score NUMERIC(5, 4),

    -- Scoring Metadata
    -- Tracks which ML model versions generated the current scores.
    -- Format: {"churn_model": "v2.1", "clv_model": "v1.4"}
    model_versions JSONB DEFAULT '{}'::JSONB,
    -- Tracks the last time the batch or streaming pipelines updated these scores.
    scores_updated_at TIMESTAMP,

    -- =========================================================================
    -- SYSTEM METADATA
    -- =========================================================================
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    status_code SMALLINT DEFAULT 1, -- 1: active, 0: inactive, -1: delete

    -- Business rule: a profile with hashed PII is not human-readable/searchable without a
    -- persona_name stand-in. Enforced at the DB layer in addition to application code.
    CONSTRAINT chk_cdp_mp_hashed_requires_persona_name CHECK (is_hashed = FALSE OR persona_name IS NOT NULL)
);

COMMENT ON TABLE customer360.cdp_master_profiles IS 'The golden/resolved customer profile (identity-resolution output): consolidated demographics, cross-channel identity graph, retail/banking/real-estate/travel/media/education domain attributes, marketing/persona fields, lineage, lifecycle tracking, and the full ML scoring block (lead, churn, CLV, CX, data quality). One row per real person per tenant+domain, built by CustomerIdentityResolver from cdp_raw_profiles_stage.';

-- Raw profiles staging
-- Landing zone for every inbound source: AppsFlyer (mobile attribution/install
-- events), MoEngage (engagement/push events), Web Tracking / GA4 (browser
-- events), and domain-specific sources like POS or Core Banking, for both the
-- retail and banking domains.
CREATE TABLE IF NOT EXISTS customer360.cdp_raw_profiles_stage (
    raw_profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    -- Data owner: internal sys_user who created/manages this row (nullable -- rows are
    -- normally landed by ingestion pipelines, not an interactive admin user).
    user_id UUID REFERENCES customer360.sys_user(user_id),
    domain TEXT NOT NULL DEFAULT 'banking' CHECK (domain IN ('retail', 'banking', 'real_estate', 'travel', 'media', 'education')),
    source_system TEXT NOT NULL,        -- 'AppsFlyer' | 'MoEngage' | 'WebTracking' | 'CoreBanking' | 'POS' | ...
    channel TEXT,                       -- 'mobile_app' | 'web' | 'pos' | 'call_center' | ...

    -- Core identity fields as reported by the source
    profile_type TEXT CHECK (
        profile_type IN (
            'individual',
            'business',
            'organization'
        )
    ) DEFAULT 'individual',
    external_customer_id TEXT, -- AppsFlyer customer_user_id / MoEngage unique_id / core banking CIF / loyalty_id
    full_name TEXT,
    email TEXT,
    phone_number TEXT,
    national_id TEXT, -- banking KYC identifier (CMND/CCCD/passport)

    -- Device & marketing identity (AppsFlyer / MoEngage / Web Tracking)
    device_id TEXT, -- IDFV / Android ID / app instance id
    advertising_id TEXT, -- IDFA / GAID
    platform TEXT, -- ios | android | web
    app_version TEXT,
    push_token TEXT,
    cookie_id TEXT, -- Web Tracking anonymous/browser cookie id
    ga_client_id TEXT, -- Google Analytics client id
    session_id TEXT,
    ip_address INET,
    user_agent TEXT,

    -- Marketing attribution (AppsFlyer install/campaign touch + Web UTM)
    media_source TEXT,
    campaign TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,

    event_name TEXT,                    -- e.g. install, login, page_view, purchase
    event_time TIMESTAMP WITH TIME ZONE,
    event_payload JSONB,                -- full raw source payload / extracted attributes

    status_code SMALLINT DEFAULT 1,  -- 3: processed, 2: in-progress, 1: new, 0: inactive, -1: delete
    processed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP DEFAULT now()
);

COMMENT ON TABLE customer360.cdp_raw_profiles_stage IS 'Landing zone for every inbound source (AppsFlyer, MoEngage, Web Tracking/GA4, POS, Core Banking, ...) before Customer Identity Resolution (CIR). Carries per-source identity + marketing attribution and a processing-queue status_code (1 new -> 2 in-progress -> 3 processed).';

-- Links (raw → master)
CREATE TABLE IF NOT EXISTS customer360.cdp_profile_links (
    link_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    user_id UUID REFERENCES customer360.sys_user (user_id), -- data owner (nullable, pipeline-created)
    raw_profile_id UUID NOT NULL REFERENCES customer360.cdp_raw_profiles_stage (raw_profile_id),
    master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles (master_profile_id),
    match_score NUMERIC(5, 4),
    match_method TEXT,
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE (tenant_id, raw_profile_id)
);

COMMENT ON TABLE customer360.cdp_profile_links IS 'Join table recording every raw_profile_id -> master_profile_id link made by CIR, with match_score/match_method. Unique per (tenant_id, raw_profile_id).';

-- ============================================================================
-- cdp_raw_events: high-volume behavioral/transactional event fact table
-- ============================================================================
-- Range-partitioned by event_time (monthly) so a single tenant's event volume
-- can scale to billions of rows without one giant table/index: writes only
-- touch the current month's partition, old partitions can be compressed/
-- archived/dropped independently, and queries that filter on event_time get
-- automatic partition pruning.
--
-- event_category values mirror leotech.cdp.domain.schema.BehavioralEvent's
-- inner classes (General/Education/Commerce/Feedback/Finance/StockTrading/
-- Travel/RealEstate/ServiceIndustry -> upper-snake here) in core-leo-cdp, so
-- the same event vocabulary is used whether an event lands in ArangoDB
-- (cdp_trackingevent, via leo.observer.js) or here in the Postgres golden-
-- record/analytics store. See cdp_event_catalog below for the seeded core
-- event names per domain (banking, retail, real_estate, travel).
--
-- Identity columns (device_id/advertising_id/cookie_id/external_customer_id/
-- session_id) are carried directly on the event row -- NOT only reachable via
-- master_profile_id/raw_profile_id -- so high-throughput ingestion never
-- blocks waiting for Customer Identity Resolution (CIR) to link the event to
-- a resolved profile first. raw_profile_id is required at ingest time (event
-- must link to cdp_raw_profiles_stage), while master_profile_id is expected to
-- be backfilled asynchronously once CIR resolves the identity.
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer360.cdp_raw_events (
    event_id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    -- Data owner: internal sys_user who created/manages this row (nullable -- almost
    -- always NULL for high-throughput pipeline ingestion; the event's actual actor is
    -- the resolved profile/customer, tracked separately via master_profile_id).
    user_id UUID REFERENCES customer360.sys_user(user_id),
    -- Business vertical this event belongs to (drives which cdp_event_catalog
    -- rows/entity_type values are relevant).
    domain TEXT NOT NULL DEFAULT 'retail' CHECK (domain IN ('retail', 'banking', 'real_estate', 'travel', 'media', 'education')),

    -- Lineage to resolved/staged profiles. raw_profile_id is required and points to
    -- cdp_raw_profiles_stage; master_profile_id remains nullable/backfilled.
    master_profile_id UUID REFERENCES customer360.cdp_master_profiles (master_profile_id),
    raw_profile_id UUID NOT NULL REFERENCES customer360.cdp_raw_profiles_stage (raw_profile_id),

    -- Direct identity carry, available at ingest time even before/without CIR.
    external_customer_id TEXT,
    device_id TEXT,
    advertising_id TEXT,
    cookie_id TEXT,
    session_id TEXT,

    -- Source & channel of the event.
    source_system TEXT NOT NULL, -- 'AppsFlyer' | 'MoEngage' | 'WebTracking' | 'CoreBanking' | 'POS' | 'PMS' | 'GDS' | ...
    -- Optional idempotency key from ingestion caller; when present it is
    -- unique per (tenant_id, source_system) to make repeated retries safe.
    event_dedup_key TEXT,
    channel TEXT, -- 'mobile_app' | 'web' | 'pos' | 'call_center' | 'branch' | 'agent' | 'ivr' | ...
    platform TEXT, -- ios | android | web
    ip_address INET,
    user_agent TEXT,

    -- Event taxonomy (see cdp_event_catalog for the governed event_name list per category).
    event_category TEXT NOT NULL DEFAULT 'GENERAL' CHECK (
        event_category IN (
            'GENERAL',
            'EDUCATION',
            'COMMERCE',
            'FEEDBACK',
            'FINANCE',
            'STOCK_TRADING',
            'TRAVEL',
            'REAL_ESTATE',
            'SERVICE_INDUSTRY'
        )
    ),
    event_name TEXT NOT NULL, -- e.g. page-view, purchase, apply-loan, booking, view-property
    is_conversion BOOLEAN NOT NULL DEFAULT FALSE,

    -- Generic entity reference (product/account/loan/property/booking/course/...).
    -- Keeps this table free of dozens of per-domain columns while staying indexable.
    entity_type TEXT, -- 'product' | 'account' | 'loan' | 'property' | 'booking' | 'course' | ...
    entity_id TEXT,

    -- Monetary value, generic across domains (purchase amount, loan amount,
    -- booking value, transfer amount, trade amount, ...). See cdp_event_catalog.value_field.
    event_value NUMERIC(15, 2),
    currency TEXT DEFAULT 'USD',

    -- Transaction linkage (purchase/booking/loan/trade confirmation, etc.)
    transaction_id TEXT,
    transaction_status TEXT,

    -- Geo (optional). Useful for real-estate listing location, retail POS/store
    -- location, travel destinations, and bank-branch visits.
    geo_location GEOGRAPHY(POINT, 4326),
    location_code TEXT,
    location_name TEXT,

    event_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),  -- when the event actually happened
    event_payload JSONB,                -- full raw source payload / domain-specific attributes

    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),           -- when the row was ingested (may lag event_time for batch/late data)

    PRIMARY KEY (event_id, event_time)
) PARTITION BY RANGE (event_time);

COMMENT ON TABLE customer360.cdp_raw_events IS 'High-volume behavioral/transactional event fact table, range-partitioned monthly by event_time. Identity columns are carried directly on the row so ingestion never blocks on CIR; raw_profile_id is mandatory (linked to cdp_raw_profiles_stage) while master_profile_id is backfilled asynchronously. See cdp_event_catalog for the governed event_category/event_name vocabulary.';

-- Creates (idempotently) the monthly partition covering for_date, e.g.
-- customer360.cdp_raw_events_2026_07 for FOR VALUES FROM ('2026-07-01') TO ('2026-08-01').
-- Call this from a scheduled job (cron/Airflow) a month or two ahead of need;
-- the DEFAULT partition below acts as a safety net if that job falls behind.
CREATE OR REPLACE FUNCTION customer360.ensure_cdp_raw_events_partition(for_date DATE)
RETURNS void AS $$
DECLARE
    part_start DATE := date_trunc('month', for_date);
    part_end DATE := part_start + INTERVAL '1 month';
    part_name TEXT := 'cdp_raw_events_' || to_char(part_start, 'YYYY_MM');
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS customer360.%I PARTITION OF customer360.cdp_raw_events FOR VALUES FROM (%L) TO (%L);',
        part_name, part_start, part_end
    );
END;
$$ LANGUAGE plpgsql;

-- Bootstrap a rolling window of monthly partitions (3 months back .. 12 months
-- forward from today) so ingestion works immediately after a fresh install.
DO $$
DECLARE
    i INT;
BEGIN
    FOR i IN -3..12 LOOP
        PERFORM customer360.ensure_cdp_raw_events_partition((CURRENT_DATE + (i || ' months')::INTERVAL)::DATE);
    END LOOP;
END;
$$;

-- Catch-all so ingestion never fails for a month outside the bootstrapped
-- window while partition maintenance catches up.
CREATE TABLE IF NOT EXISTS customer360.cdp_raw_events_default PARTITION OF customer360.cdp_raw_events DEFAULT;

---------------------------------------------------
-- EVENT CATALOG (governed cross-domain event vocabulary)
---------------------------------------------------

-- ============================================================================
-- cdp_event_catalog: the governed list of event_category/event_name pairs
-- that may be written to cdp_raw_events (and, by convention, to ArangoDB's
-- cdp_trackingevent via core-leo-cdp's BehavioralEvent constants). Mirrors
-- the same "attribute catalog" pattern already used by cdp_profile_attributes
-- above. Deliberately NOT enforced via a hard FK from cdp_raw_events.event_name
-- (event_name stays free TEXT) so high-throughput ingestion is never blocked
-- by a missing catalog row for a brand-new event; the catalog exists for
-- discoverability/admin-UI dropdowns/analytics governance instead.
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer360.cdp_event_catalog (
    id BIGSERIAL PRIMARY KEY,
    event_name TEXT UNIQUE NOT NULL,
    event_category TEXT NOT NULL CHECK (
        event_category IN (
            'GENERAL',
            'EDUCATION',
            'COMMERCE',
            'FEEDBACK',
            'FINANCE',
            'STOCK_TRADING',
            'TRAVEL',
            'REAL_ESTATE',
            'SERVICE_INDUSTRY'
        )
    ),
    domain_scope TEXT NOT NULL DEFAULT 'all' CHECK (
        domain_scope IN (
            'all',
            'retail',
            'banking',
            'real_estate',
            'travel',
            'media',
            'education'
        )
    ),
    description TEXT,
    is_conversion_default BOOLEAN NOT NULL DEFAULT FALSE,
    -- Conceptual name of the event_payload key that should be mirrored into
    -- cdp_raw_events.event_value for this event (documentation aid only).
    value_field TEXT,
    display_order INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE customer360.cdp_event_catalog IS 'Governed vocabulary of event_category/event_name pairs (seeded below) across GENERAL/FEEDBACK/COMMERCE/FINANCE/STOCK_TRADING/TRAVEL/REAL_ESTATE. Not FK-enforced from cdp_raw_events so ingestion is never blocked by a missing catalog row; exists for discoverability/governance.';

---------------------------------------------------
-- PROFILE ATTRIBUTE METADATA REGISTRY
---------------------------------------------------

-- ============================================================================
-- cdp_profile_attributes: full metadata of all attributes in cdp_master_profiles
-- ============================================================================
-- One row per attribute exposed anywhere on the CDP golden record: identity /
-- demographic / retail / banking / marketing / lineage columns AND the
-- ML scoring-model outputs (Lead, Churn, CLV, Customer Experience, Data
-- Quality / Identity Resolution confidence). Also carries the
-- cdp_raw_profiles_stage matching keys (device_id, advertising_id, cookie_id,
-- external_customer_id) consumed dynamically by the Customer Identity
-- Resolution (CIR) engine (core-customer360/backend-system/identity_resolution ->
-- identity_resolution.resolver.CustomerIdentityResolver), which only reads
-- attribute_internal_code / is_identity_resolution / status / matching_rule /
-- matching_threshold, so the extra metadata columns below are additive and
-- safe for that consumer.
-- Uses CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS so it stays
-- additive/idempotent for databases where a narrower cdp_profile_attributes
-- table was already created at runtime (pre-existing behavior of
-- backend-system/identity_resolution/scripts/init_sample_data.py).
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer360.cdp_profile_attributes (
    id BIGSERIAL PRIMARY KEY,

    -- Attribute identity. Matches the cdp_raw_profiles_stage column name when
    -- used as an identity-resolution matching key, otherwise matches the
    -- cdp_master_profiles column name directly.
    attribute_internal_code VARCHAR(100) UNIQUE NOT NULL,
    -- The cdp_master_profiles column this attribute is stored in / consolidated
    -- into, e.g. matching key 'device_id' consolidates into master 'device_ids'.
    master_profile_column VARCHAR(100),
    name VARCHAR(255) NOT NULL,
    description TEXT,

    -- Logical grouping for catalog browsing / admin UI.
    attribute_group VARCHAR(50) NOT NULL DEFAULT 'GENERAL' CHECK (
        attribute_group IN (
            'SYSTEM',
            'IDENTITY',
            'IDENTITY_GRAPH',
            'RETAIL',
            'BANKING',
            'REAL_ESTATE',
            'TRAVEL',
            'MEDIA',
            'EDUCATION',
            'MARKETING',
            'LINEAGE',
            'LIFECYCLE',
            'LEAD_SCORING',
            'CHURN_SCORING',
            'CLV_SCORING',
            'CX_SCORING',
            'DATA_QUALITY',
            'GENERAL'
        )
    ),
    -- Physical table(s) this attribute lives on.
    source_table VARCHAR(150) NOT NULL DEFAULT 'cdp_master_profiles',
    domain_scope VARCHAR(20) NOT NULL DEFAULT 'all' CHECK (
        domain_scope IN (
            'all',
            'retail',
            'banking',
            'real_estate',
            'travel',
            'media',
            'education'
        )
    ),
    is_pii BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',

    -- ------------------------------------------------------------------
    -- Customer Identity Resolution (CIR) matching-rule metadata, consumed
    -- dynamically by identity_resolution.resolver.CustomerIdentityResolver.
    -- ------------------------------------------------------------------
    is_identity_resolution BOOLEAN NOT NULL DEFAULT FALSE,
    matching_rule VARCHAR(50) CHECK (
        matching_rule IN (
            'exact',
            'fuzzy_trgm',
            'fuzzy_dmetaphone',
            'none'
        )
    ),
    matching_threshold NUMERIC(5, 4),
    -- Merge precedence for conflicting values on the same master profile.
    -- Supported strategies include most_recent, verified_first,
    -- verified_then_most_recent, source_priority, non_null,
    -- append_distinct, and overwrite.
    consolidation_rule VARCHAR(50) CHECK (
        consolidation_rule IS NULL OR consolidation_rule IN (
            'most_recent',
            'verified_first',
            'verified_then_most_recent',
            'source_priority',
            'non_null',
            'append_distinct',
            'overwrite'
        )
    ),
    -- Optional rule-specific parameters such as timestamp_field,
    -- verified_field, verified_values, or source_priority.
    consolidation_config JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- segmentation metadata: whether this attribute can be used for audience segmentation, and its data type (TEXT, NUMERIC, DATE, TIMESTAMP, BOOLEAN, JSONB).
    is_segmentable BOOLEAN NOT NULL DEFAULT TRUE,
    data_type VARCHAR(50) NOT NULL DEFAULT 'TEXT',

    -- ------------------------------------------------------------------
    -- ML / scoring-model metadata: Lead, Churn, CLV, Customer Experience (CX)
    -- and Data Quality / Identity Resolution confidence scoring models.
    -- ------------------------------------------------------------------
    is_scoring_model BOOLEAN NOT NULL DEFAULT FALSE,
    scoring_model_name VARCHAR(100),
    scoring_model_version VARCHAR(20),
    value_type VARCHAR(50) CHECK (value_type IS NULL OR value_type IN (
        'probability', 'score', 'tier', 'currency', 'percentage', 'sentiment',
        'count', 'label', 'metadata', 'identifier', 'timestamp'
    )),
    value_min NUMERIC,
    value_max NUMERIC,
    -- How often this attribute/score gets (re)computed: 'realtime' | 'hourly' |
    -- 'daily' | 'weekly' | 'batch' | 'event_driven'.
    refresh_frequency VARCHAR(50),

    display_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE customer360.cdp_profile_attributes IS 'Metadata-driven attribute catalog: one row per cdp_master_profiles column (plus cdp_raw_profiles_stage matching keys), grouped by attribute_group. Drives Customer Identity Resolution (CIR) matching rules (is_identity_resolution/matching_rule/matching_threshold) and merge precedence (consolidation_rule/consolidation_config) without hard-coding them in application code.';

---------------------------------------------------
-- RELATIONS & EVENTS
---------------------------------------------------

-- Relation Types dictionary
CREATE TABLE IF NOT EXISTS customer360.cdp_relation_types (
    relation_type_id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL, -- e.g., 'friend', 'colleague', 'family', 'customer-contact'
    description TEXT
);

COMMENT ON TABLE customer360.cdp_relation_types IS 'Dictionary of relationship types (e.g. friend, colleague, family, customer-contact) usable between two cdp_master_profiles rows via cdp_relations.';

-- Profile Relations
CREATE TABLE IF NOT EXISTS customer360.cdp_relations (
    relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    user_id UUID REFERENCES customer360.sys_user (user_id), -- data owner
    source_master_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles (master_profile_id),
    target_master_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles (master_profile_id),
    relation_type_id INT NOT NULL REFERENCES customer360.cdp_relation_types (relation_type_id),
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE (
        tenant_id,
        source_master_id,
        target_master_id,
        relation_type_id
    )
);

COMMENT ON TABLE customer360.cdp_relations IS 'Typed relationship edge between two resolved master profiles (e.g. "friend", "family", "customer-contact"), typed via cdp_relation_types.';

-- Customer Contacts (interactions)
CREATE TABLE IF NOT EXISTS customer360.crm_customer_contacts (
    contact_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    user_id UUID REFERENCES customer360.sys_user (user_id), -- data owner
    master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles (master_profile_id),
    contact_type TEXT,
    contact_channel TEXT,
    contact_content TEXT,
    contact_date TIMESTAMP DEFAULT now()
);

COMMENT ON TABLE customer360.crm_customer_contacts IS 'Interaction/contact log (type/channel/content/date) recorded against a resolved master profile.';

-- Customer Transactions (financial, retail, travel, etc.)
CREATE TABLE IF NOT EXISTS customer360.crm_transactions (
    transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),

    -- Data owner: internal sys_user who created/manages this row (nullable -- almost
    -- always NULL for pipeline-imported transactions).
    user_id UUID REFERENCES customer360.sys_user (user_id),

    -- Nullable + no hard NOT NULL, same async-backfill pattern as cdp_raw_events:
    -- a transaction can be ingested from a source system before Customer Identity
    -- Resolution (CIR) has linked it to a resolved profile.
    master_profile_id UUID REFERENCES customer360.cdp_master_profiles(master_profile_id),

    source_system VARCHAR(50),
    source_transaction_id VARCHAR(255),

    transaction_type VARCHAR(50),
    transaction_status VARCHAR(30),

    entity_type VARCHAR(50),
    entity_id VARCHAR(255),
    entity_name TEXT,

    quantity NUMERIC(18,4),
    amount NUMERIC(18,2),
    currency CHAR(3),

    channel VARCHAR(100),

    merchant_id VARCHAR(255),
    merchant_name TEXT,

    location_id VARCHAR(255),
    location_name TEXT,

    campaign_id VARCHAR(255),
    campaign_name TEXT,

    staff_id VARCHAR(255),
    staff_name TEXT,

    transaction_time TIMESTAMP,

    attributes JSONB DEFAULT '{}'::jsonb,

    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE customer360.crm_transactions IS 'Source-agnostic transaction fact (retail purchase, banking transfer, travel booking, ...). master_profile_id is nullable and backfilled asynchronously by CIR, the same pattern as cdp_raw_events, so ingestion is never blocked waiting for identity resolution.';

-- ============================================================================
-- cdp_content_items: personalized content library (news/video/product/article)
-- ============================================================================
-- Backs the Customer 360 profile dashboard's "Personalized Items" panel
-- (core-customer360/frontend-admin). Items are ranked per master profile by
-- segment_tags overlap with cdp_master_profiles.segmentation_tags -- see
-- customer360-api's GET /api/v1/content-items/recommended.
CREATE TABLE IF NOT EXISTS customer360.cdp_content_items (
    content_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    domain TEXT NOT NULL DEFAULT 'all' CHECK (domain IN ('all', 'retail', 'banking', 'real_estate', 'travel', 'media', 'education')),
    item_type TEXT NOT NULL CHECK (item_type IN ('news', 'video', 'product', 'article')),
    title TEXT NOT NULL,
    summary TEXT,
    image_url TEXT,
    cta_label TEXT,
    cta_url TEXT,
    segment_tags TEXT[] DEFAULT ARRAY[]::text[],
    published_at TIMESTAMPTZ DEFAULT now(),
    status_code SMALLINT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE customer360.cdp_content_items IS 'Personalized content library (news/video/product/article) for the Customer 360 profile dashboard "Personalized Items" panel; ranked per profile by segment_tags overlap with cdp_master_profiles.segmentation_tags via /api/v1/content-items/recommended.';

CREATE INDEX idx_cdp_content_items_domain_type ON customer360.cdp_content_items (domain, item_type);
CREATE INDEX idx_cdp_content_items_tags ON customer360.cdp_content_items USING GIN (segment_tags);

-- ============================================================================
-- cdp_segments: segmentation tag metadata (Audience Builder)
-- ============================================================================
-- One row per named audience/segment tag (the same tag strings that end up in
-- cdp_master_profiles.segmentation_tags). Stores the rule definition behind a
-- segment in two complementary forms -- the raw jQuery QueryBuilder rule tree
-- (json_rules) and its translated SQL WHERE-clause fragment (sql_rules) --
-- plus final_generated_sql, the full SELECT statement (base cdp_master_profiles
-- query + sql_rules) actually executed to (re)compute segment membership.
-- processed_by records whether the rules were authored by a human via the
-- jQuery QueryBuilder admin UI or generated by an AI agent.
CREATE TABLE IF NOT EXISTS customer360.cdp_segments (
    segment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    -- Data owner: internal sys_user who created/manages this segment (nullable --
    -- segments generated by an ai_agent may have no interactive owner).
    user_id UUID REFERENCES customer360.sys_user(user_id),
    domain TEXT NOT NULL DEFAULT 'all' CHECK (domain IN ('all', 'retail', 'banking', 'real_estate', 'travel', 'media', 'education')),

    -- Unique short tag written into cdp_master_profiles.segmentation_tags for
    -- every profile that matches this segment (e.g. 'gen_z_shopper').
    segment_tag TEXT NOT NULL,
    segment_name TEXT NOT NULL,
    description TEXT,

    -- Raw jQuery QueryBuilder rule tree, e.g. {"condition":"AND","rules":[...]}.
    json_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- WHERE-clause fragment translated from json_rules (QueryBuilder.getSQL()
    -- style output), e.g. "age >= 18 AND city = 'Ho Chi Minh'".
    sql_rules TEXT,
    -- Full SELECT statement (base query + sql_rules) actually executed to
    -- (re)compute this segment's membership against cdp_master_profiles.
    final_generated_sql TEXT,
    -- Who produced sql_rules/json_rules: an interactive admin using the jQuery
    -- QueryBuilder UI, or an AI agent (e.g. natural-language-to-segment).
    processed_by TEXT NOT NULL DEFAULT 'human' CHECK (processed_by IN ('human', 'ai_agent')),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    -- Last computed size of this segment; refreshed whenever final_generated_sql
    -- is (re)run.
    member_count INTEGER DEFAULT 0,
    last_computed_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    status_code SMALLINT DEFAULT 1, -- 1: active, 0: inactive, -1: delete

    CONSTRAINT uq_cdp_segments_tenant_tag UNIQUE (tenant_id, segment_tag)
);

COMMENT ON TABLE customer360.cdp_segments IS 'Segmentation/Audience Builder metadata: one row per named segment tag (mirrored into cdp_master_profiles.segmentation_tags), storing its jQuery QueryBuilder rule tree (json_rules), translated SQL fragment (sql_rules), the full executable query (final_generated_sql), and whether it was authored by a human or an ai_agent.';

CREATE INDEX idx_cdp_segments_tenant ON customer360.cdp_segments (tenant_id);
CREATE INDEX idx_cdp_segments_json_rules ON customer360.cdp_segments USING GIN (json_rules);

---------------------------------------------------
-- GRAPH EDGES (Partitioned by Relation)
---------------------------------------------------

-- Parent
CREATE TABLE IF NOT EXISTS customer360.graph_edges (
    edge_id BIGSERIAL NOT NULL,
    from_id UUID NOT NULL,
    to_id UUID NOT NULL,
    from_type TEXT NOT NULL,
    to_type TEXT NOT NULL,
    relation TEXT NOT NULL,
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (edge_id, relation)
) PARTITION BY LIST (relation);

COMMENT ON TABLE customer360.graph_edges IS 'General-purpose graph edge table (from_id/to_id + from_type/to_type), list-partitioned by relation (belongs_to, converted, follows, has_role, ...). Carries its own embedding vector(1536) for relationship-aware semantic search.';

-- Partitions for known relations
CREATE TABLE customer360.graph_edges_belongs_to PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('belongs_to');

CREATE TABLE customer360.graph_edges_comes_from PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('comes_from');

CREATE TABLE customer360.graph_edges_converted PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('converted');

CREATE TABLE customer360.graph_edges_follows PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('follows');

CREATE TABLE customer360.graph_edges_is_part_of PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_part_of');

CREATE TABLE customer360.graph_edges_is_active_as PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_active_as');

CREATE TABLE customer360.graph_edges_is_connected_to PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_connected_to');

CREATE TABLE customer360.graph_edges_is_from PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_from');

CREATE TABLE customer360.graph_edges_created_by PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('created_by');

CREATE TABLE customer360.graph_edges_is_driven_by PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_driven_by');

CREATE TABLE customer360.graph_edges_has_role PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('has_role');

CREATE TABLE customer360.graph_edges_has PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('has');

CREATE TABLE customer360.graph_edges_is_for_the PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_for_the');

CREATE TABLE customer360.graph_edges_belongs_to_industry PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('belongs_to_industry');

-- Catch-all
CREATE TABLE customer360.graph_edges_other PARTITION OF customer360.graph_edges DEFAULT;

---------------------------------------------------
-- INDEXES
---------------------------------------------------

-- =========================================================================
-- RECOMMENDED INDICES FOR LEO CDP MASTER PROFILES
-- =========================================================================

-- -------------------------------------------------------------------------
-- 1. ENTITY & IDENTITY INDEXES (B-TREE)
-- Upgraded to UNIQUE per tenant_id to guarantee that master profiles
-- remain true "golden records" without duplicates in a single workspace.
-- -------------------------------------------------------------------------

-- Email is unique per workspace. Ignored if NULL (e.g., mobile-only users).
CREATE UNIQUE INDEX ux_cdp_mp_tenant_email ON customer360.cdp_master_profiles (tenant_id, email)
WHERE
    email IS NOT NULL;

-- Phone is unique per workspace. Ignored if NULL (e.g., web-only users).
CREATE UNIQUE INDEX ux_cdp_mp_tenant_phone ON customer360.cdp_master_profiles (tenant_id, phone_number)
WHERE
    phone_number IS NOT NULL;

-- Core Banking & Retail Identifiers (Must be unique per tenant)
CREATE UNIQUE INDEX ux_cdp_mp_tenant_national_id ON customer360.cdp_master_profiles (tenant_id, national_id)
WHERE
    national_id IS NOT NULL;

CREATE UNIQUE INDEX ux_cdp_mp_tenant_cif_number ON customer360.cdp_master_profiles (tenant_id, cif_number)
WHERE
    cif_number IS NOT NULL;

CREATE UNIQUE INDEX ux_cdp_mp_tenant_loyalty_id ON customer360.cdp_master_profiles (tenant_id, loyalty_id)
WHERE
    loyalty_id IS NOT NULL;

-- -------------------------------------------------------------------------
-- 2. ML, SCORING & SEGMENTATION INDEXES (B-TREE)
-- Improved by leading with tenant_id. Since segmentation queries always
-- happen within a specific tenant, this massively speeds up campaign lookups.
-- -------------------------------------------------------------------------

-- Fast retrieval for churn prevention campaigns (Partial index saves space)
CREATE INDEX idx_cdp_mp_churn_tier ON customer360.cdp_master_profiles (tenant_id, churn_risk_tier)
WHERE
    churn_risk_tier IN ('high', 'critical');

-- Fast retrieval for high-value customer targeting (Whales)
CREATE INDEX idx_cdp_mp_pred_clv ON customer360.cdp_master_profiles (
    tenant_id,
    predictive_clv DESC NULLS LAST
);

-- Fast routing of high-probability leads to sales/CRM
CREATE INDEX idx_cdp_mp_lead_prob ON customer360.cdp_master_profiles (
    tenant_id,
    lead_conversion_probability DESC NULLS LAST
);

-- Analytics lookup for profiles needing data enrichment
CREATE INDEX idx_cdp_mp_data_quality ON customer360.cdp_master_profiles (
    tenant_id,
    profile_completeness_score,
    identity_confidence_score
);

-- -------------------------------------------------------------------------
-- 3. CROSS-CHANNEL IDENTITY GRAPH INDEXES (GIN)
-- Used for fast querying inside JSON objects and TEXT arrays.
-- Deduplicated external_ids and standardized names.
--
-- Note: If you frequently query these alongside tenant_id, consider enabling
-- the 'btree_gin' PostgreSQL extension to allow (tenant_id, json_column)
-- composite GIN indexes in the future.
-- -------------------------------------------------------------------------

-- Deterministic external IDs (e.g., {"appsflyer_id": "...", "ga_client_id": "..."})
CREATE INDEX idx_cdp_mp_external_ids ON customer360.cdp_master_profiles USING GIN (external_ids);

-- Secondary contacts
CREATE INDEX idx_cdp_mp_sec_emails ON customer360.cdp_master_profiles USING GIN (secondary_emails);

CREATE INDEX idx_cdp_mp_sec_phones ON customer360.cdp_master_profiles USING GIN (secondary_phones);

-- Device & Ad Graph (Arrays)
CREATE INDEX idx_cdp_mp_device_ids ON customer360.cdp_master_profiles USING GIN (device_ids);

CREATE INDEX idx_cdp_mp_advertising_ids ON customer360.cdp_master_profiles USING GIN (advertising_ids);

CREATE INDEX idx_cdp_mp_cookie_ids ON customer360.cdp_master_profiles USING GIN (cookie_ids);

-- Raw staging indexes: identity fields used for matching, plus the
-- processing-queue lookup (tenant_id, status_code).
CREATE INDEX idx_raw_profiles_stage_tenant_status ON customer360.cdp_raw_profiles_stage (tenant_id, status_code);

CREATE INDEX idx_raw_profiles_stage_email ON customer360.cdp_raw_profiles_stage (email)
WHERE
    email IS NOT NULL;

CREATE INDEX idx_raw_profiles_stage_phone ON customer360.cdp_raw_profiles_stage (phone_number)
WHERE
    phone_number IS NOT NULL;

CREATE INDEX idx_raw_profiles_stage_external_customer_id ON customer360.cdp_raw_profiles_stage (external_customer_id)
WHERE
    external_customer_id IS NOT NULL;

CREATE INDEX idx_raw_profiles_stage_device_id ON customer360.cdp_raw_profiles_stage (device_id)
WHERE
    device_id IS NOT NULL;

CREATE INDEX idx_raw_profiles_stage_advertising_id ON customer360.cdp_raw_profiles_stage (advertising_id)
WHERE
    advertising_id IS NOT NULL;

CREATE INDEX idx_raw_profiles_stage_cookie_id ON customer360.cdp_raw_profiles_stage (cookie_id)
WHERE
    cookie_id IS NOT NULL;

CREATE INDEX idx_raw_profiles_stage_national_id ON customer360.cdp_raw_profiles_stage (national_id)
WHERE
    national_id IS NOT NULL;

CREATE INDEX idx_contacts_date ON customer360.crm_customer_contacts (contact_date);

-- crm_transactions indexes: tenant timeline, resolved-profile timeline, generic
-- entity lookups, and idempotent re-ingestion protection (mirrors the
-- cdp_raw_events / cdp_profile_links index conventions above).
CREATE INDEX idx_crm_transactions_tenant_time ON customer360.crm_transactions (
    tenant_id,
    transaction_time DESC
);

CREATE INDEX idx_crm_transactions_master_profile ON customer360.crm_transactions (
    master_profile_id,
    transaction_time DESC
)
WHERE
    master_profile_id IS NOT NULL;

CREATE INDEX idx_crm_transactions_entity ON customer360.crm_transactions (entity_type, entity_id)
WHERE
    entity_id IS NOT NULL;

CREATE UNIQUE INDEX ux_crm_transactions_tenant_source ON customer360.crm_transactions (
    tenant_id,
    source_system,
    source_transaction_id
)
WHERE
    source_transaction_id IS NOT NULL;

-- Profile attribute metadata registry: catalog browsing by group, fast
-- lookup of active CIR matching rules, and lookup of attributes by scoring model.
CREATE INDEX IF NOT EXISTS idx_cdp_pa_group ON customer360.cdp_profile_attributes (attribute_group);

CREATE INDEX IF NOT EXISTS idx_cdp_pa_identity_resolution ON customer360.cdp_profile_attributes (attribute_internal_code)
WHERE
    is_identity_resolution = TRUE
    AND status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_cdp_pa_scoring_model ON customer360.cdp_profile_attributes (scoring_model_name)
WHERE
    is_scoring_model = TRUE;

-- cdp_raw_events indexes: created on the partitioned parent, Postgres
-- propagates each of these automatically to every monthly partition (current
-- + future ones created via ensure_cdp_raw_events_partition()).
-- Optional idempotency key per source-system ingestion stream.
-- NOTE: event_time must be included because Postgres requires every unique
-- index on a partitioned table to include all partitioning columns
-- (cdp_raw_events is PARTITION BY RANGE (event_time)) -- so this dedups
-- (tenant_id, source_system, event_dedup_key) per event_time value rather
-- than globally across all time.
CREATE UNIQUE INDEX ux_cdp_raw_events_tenant_source_dedup ON customer360.cdp_raw_events (
    tenant_id,
    source_system,
    event_dedup_key,
    event_time
)
WHERE
    event_dedup_key IS NOT NULL;
-- Tenant timeline queries (most common access pattern for a Customer 360 view).
CREATE INDEX idx_cdp_raw_events_tenant_time ON customer360.cdp_raw_events (tenant_id, event_time DESC);
-- Event taxonomy / funnel analysis per tenant+domain.
CREATE INDEX idx_cdp_raw_events_taxonomy ON customer360.cdp_raw_events (
    tenant_id,
    domain,
    event_category,
    event_name,
    event_time DESC
);
-- Resolved-profile timeline (Customer 360 activity feed).
CREATE INDEX idx_cdp_raw_events_master_profile ON customer360.cdp_raw_events (
    master_profile_id,
    event_time DESC
)
WHERE
    master_profile_id IS NOT NULL;
-- Backfill lookups from cdp_raw_profiles_stage.
CREATE INDEX idx_cdp_raw_events_raw_profile ON customer360.cdp_raw_events (raw_profile_id)
WHERE
    raw_profile_id IS NOT NULL;
-- Pre-resolution identity lookups (event arrives before/without CIR linking).
CREATE INDEX idx_cdp_raw_events_device_id ON customer360.cdp_raw_events (device_id)
WHERE
    device_id IS NOT NULL;

CREATE INDEX idx_cdp_raw_events_advertising_id ON customer360.cdp_raw_events (advertising_id)
WHERE
    advertising_id IS NOT NULL;

CREATE INDEX idx_cdp_raw_events_cookie_id ON customer360.cdp_raw_events (cookie_id)
WHERE
    cookie_id IS NOT NULL;

CREATE INDEX idx_cdp_raw_events_external_customer_id ON customer360.cdp_raw_events (external_customer_id)
WHERE
    external_customer_id IS NOT NULL;

CREATE INDEX idx_cdp_raw_events_session_id ON customer360.cdp_raw_events (session_id)
WHERE
    session_id IS NOT NULL;
-- Generic entity lookups (all events about a given product/property/booking/...).
CREATE INDEX idx_cdp_raw_events_entity ON customer360.cdp_raw_events (entity_type, entity_id)
WHERE
    entity_id IS NOT NULL;
-- Conversion funnel / revenue reporting.
CREATE INDEX idx_cdp_raw_events_conversion ON customer360.cdp_raw_events (tenant_id, event_time DESC)
WHERE
    is_conversion = TRUE;
-- Point lookup by event_id alone (without needing event_time for partition pruning).
CREATE INDEX idx_cdp_raw_events_event_id ON customer360.cdp_raw_events (event_id);
-- Ad-hoc querying of the raw source payload.
CREATE INDEX idx_cdp_raw_events_payload ON customer360.cdp_raw_events USING GIN (event_payload);
-- Geo-proximity queries (property/store/destination location search).
CREATE INDEX idx_cdp_raw_events_geo ON customer360.cdp_raw_events USING GIST (geo_location)
WHERE
    geo_location IS NOT NULL;

-- Event catalog: browsing by category/domain and fast active-event lookup.
CREATE INDEX idx_cdp_event_catalog_category ON customer360.cdp_event_catalog (event_category);

CREATE INDEX idx_cdp_event_catalog_domain_scope ON customer360.cdp_event_catalog (domain_scope)
WHERE
    status = 'ACTIVE';

-- Graph edges indexes
CREATE INDEX ON customer360.graph_edges_belongs_to (from_id, to_id);

CREATE INDEX ON customer360.graph_edges_comes_from (from_id);

CREATE INDEX ON customer360.graph_edges_converted (from_id);

CREATE INDEX ON customer360.graph_edges_follows USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE INDEX ON customer360.graph_edges_is_driven_by (created_at);

CREATE INDEX ON customer360.graph_edges_belongs_to_industry (created_at);

---------------------------------------------------
-- ROW LEVEL SECURITY (RBAC / Multi-Tenant Isolation)
---------------------------------------------------
-- Tenant isolation via PostgreSQL Row-Level Security.
-- ============================================================================
-- Every table below carries a NOT NULL tenant_id FK to customer360.sys_tenant
-- (see the "all crm_*/cdp_* tables must have tenant_id" convention introduced
-- alongside the RBAC tables -- sys_tenant/sys_organization/sys_user/sys_role/
-- sys_permission/sys_role_permission/sys_user_role/sys_audit_log -- above).
--
-- The application must SET the current tenant on every pooled connection
-- before running any query, e.g.:
--   SELECT set_config('app.tenant_id', '<tenant-uuid>', true);  -- true = tx-local
-- or via SQLAlchemy: conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
--
-- FORCE ROW LEVEL SECURITY is applied in addition to ENABLE so that the
-- policy is also enforced for the table owner (the role the application
-- normally connects as) -- without FORCE, RLS is bypassed for the owner of
-- the table, which would silently defeat tenant isolation for the app's own
-- DB user. Only a superuser (or BYPASSRLS role) can still see cross-tenant
-- rows; the app's runtime DB role should NOT be granted BYPASSRLS/superuser.
--
-- IMPORTANT (verified): PostgreSQL superusers ALWAYS bypass RLS, regardless
-- of ENABLE/FORCE -- this cannot be overridden. The default local/dev
-- DB_USER=postgres in .env.example is typically a superuser, so RLS has NO
-- effect on that connection. For RLS to actually protect production data,
-- customer360-api (and any other tenant-facing consumer) MUST connect as a
-- dedicated non-superuser role, e.g.:
--   CREATE ROLE customer360_app LOGIN PASSWORD '...';
--   GRANT USAGE ON SCHEMA customer360 TO customer360_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA customer360 TO customer360_app;
-- backend-system/identity_resolution (CIR) intentionally processes many tenants per
-- batch/connection (see run_resolution_batch in resolver.py), so it either
-- needs its own BYPASSRLS role, OR -- the approach taken here -- it re-issues
-- set_config('app.tenant_id', ...) per row before each row's queries, which
-- works fine against a plain (non-BYPASSRLS) role too.
--
-- current_setting('app.tenant_id') is called with missing_ok = true so a
-- connection that never set app.tenant_id gets NULL (and therefore denies
-- all rows, since tenant_id can never equal NULL) rather than raising an
-- error -- fail-closed instead of fail-open.
-- ============================================================================

DO $$
DECLARE
    t TEXT;
    tenant_tables TEXT[] := ARRAY[
        'sys_organization',
        'sys_user',
        'sys_role',
        'sys_audit_log',
        'crm_campaign',
        'crm_campaign_member',
        'crm_lead',
        'crm_lead_source',
        'crm_contact',
        'crm_account',
        'crm_opportunity',
        'crm_industry',
        'crm_customer_contacts',
        'crm_transactions',
        'cdp_master_profiles',
        'cdp_raw_profiles_stage',
        'cdp_profile_links',
        'cdp_raw_events',
        'cdp_relations',
        'cdp_segments',
        'cdp_content_items'
    ];
BEGIN
    FOREACH t IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE customer360.%I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE customer360.%I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_policy ON customer360.%I;', t);
        EXECUTE format(
            'CREATE POLICY tenant_policy ON customer360.%I
                USING (tenant_id = current_setting(''app.tenant_id'', true)::uuid)
                WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true)::uuid);',
            t
        );
    END LOOP;
END;
$$;

-- Example (as requested) -- equivalent to the loop-generated policy above for
-- this one table, kept here for documentation/readability:
-- CREATE POLICY tenant_policy
-- ON customer360.cdp_master_profiles
-- USING (
--     tenant_id =
--     current_setting('app.tenant_id')::uuid
-- );