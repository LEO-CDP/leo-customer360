-- SQLBook: Code
-- Customer 360 Database Schema -- 

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
-- Required by identity_resolution/resolver.py's fuzzy_trgm (similarity())
-- and fuzzy_dmetaphone (dmetaphone()) CIR matching_rule query builders.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS btree_gist;

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

CREATE INDEX IF NOT EXISTS idx_org_tenant ON customer360.sys_organization (tenant_id);

CREATE INDEX IF NOT EXISTS idx_org_parent ON customer360.sys_organization (parent_organization_id);

-- ==========================================================
-- Application User table
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.sys_user (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Added ON DELETE CASCADE/SET NULL to prevent orphan records if a tenant/org is deleted
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    organization_id UUID NULL REFERENCES customer360.sys_organization(organization_id) ON DELETE SET NULL,
    
    -- REMOVED: keycloak_user_id UUID UNIQUE 
    -- Reason: Identity mapping is now correctly handled by the 1-to-Many sys_userinfo table.

    username VARCHAR(150) NOT NULL,
    email VARCHAR(255),
    full_name VARCHAR(255),
    phone VARCHAR(30),
    job_title VARCHAR(100),
    department VARCHAR(100),
    language_code VARCHAR(10) DEFAULT 'en',
    
    -- Added default UTC to prevent null timezone logic errors in the application
    timezone VARCHAR(50) DEFAULT 'UTC',
    
    -- Added NOT NULL constraint to status
    status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL,
    
    -- Upgraded all timestamps to TIMESTAMPTZ (WITH TIME ZONE). 
    -- Storing naked TIMESTAMPs in a multi-tenant CDP leads to data corruption across timezones.
    last_login_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    
    -- Added default empty JSON object to avoid dealing with NULL JSONB values in queries
    metadata JSONB DEFAULT '{}'::jsonb,
    
    -- Added CHECK constraints to force lowercase emails and usernames. 
    -- This prevents accidental duplicates like 'Admin' vs 'admin' or 'User@Email.com' bypassing the UNIQUE constraints.
    CONSTRAINT chk_sys_user_username_lower CHECK (username = lower(username)),
    CONSTRAINT chk_sys_user_email_lower CHECK (email IS NULL OR email = lower(email)),

    -- Tenant-scoped uniqueness
    CONSTRAINT uq_username UNIQUE (tenant_id, username),
    CONSTRAINT uq_email UNIQUE (tenant_id, email)
);

COMMENT ON TABLE customer360.sys_user IS 'Internal application user/staff account. Decoupled from SSO identities (which live in sys_userinfo).';

-- ----------------------------------------------------------------------------
-- INDEXES
-- ----------------------------------------------------------------------------
-- The UNIQUE constraints above automatically create B-Tree indexes for (tenant_id, username) and (tenant_id, email).
-- We only need to manually index foreign keys and frequent filter columns.

CREATE INDEX IF NOT EXISTS idx_user_tenant ON customer360.sys_user(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_org ON customer360.sys_user(organization_id);

-- Added index for status, as admin dashboards frequently filter by active/inactive users
CREATE INDEX IF NOT EXISTS idx_user_status ON customer360.sys_user(tenant_id, status);

-- ==========================================================
-- User Login & SSO Identity Management (sys_userinfo)
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.sys_userinfo (
    userinfo_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Multi-tenant and User mappings
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES customer360.sys_user(user_id) ON DELETE CASCADE,

    -- Authentication Provider Details
    -- Examples: 'LOCAL', 'KEYCLOAK', 'GOOGLE', 'MICROSOFT', 'APPLE'
    auth_provider VARCHAR(50) NOT NULL,
    
    -- The unique external subject ID from the identity provider (e.g., JWT sub claim)
    -- For 'LOCAL' auth_provider, this can act as the unique username or email for login.
    provider_subject_id TEXT NOT NULL, 

    -- Local Authentication
    -- Stores the bcrypt/argon2 hash. Only populated when auth_provider = 'LOCAL'.
    password_hash TEXT,

    -- External SSO Token Caching (Optional)
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMP WITH TIME ZONE,

    -- Status & Tracking
    status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL,
    last_login_at TIMESTAMP WITH TIME ZONE,

    -- Standard Audit & Extensibility Fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    metadata JSONB DEFAULT '{}'::jsonb,

    -- Constraints
    -- 1. Ensure external SSO IDs (or local usernames) are globally unique per tenant/provider
    CONSTRAINT uq_sys_userinfo_provider_id UNIQUE (tenant_id, auth_provider, provider_subject_id),
    
    -- 2. Ensure a user only links one account per provider per tenant 
    -- (e.g., a user can only have ONE Google account and ONE Local password linked)
    CONSTRAINT uq_sys_userinfo_user_provider UNIQUE (tenant_id, user_id, auth_provider)
);

COMMENT ON TABLE customer360.sys_userinfo IS 'Handles multi-tenant SSO identities (Keycloak, Google, Microsoft) and local password credentials linked to a core sys_user account. Decouples login methods from the core user record to support 1-to-many authentication methods.';

-- ----------------------------------------------------------------------------
-- INDEXES
-- ----------------------------------------------------------------------------
-- Optimizes general tenant and user relationship queries
CREATE INDEX IF NOT EXISTS idx_sys_userinfo_tenant ON customer360.sys_userinfo(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sys_userinfo_user ON customer360.sys_userinfo(user_id);

-- Highly optimized lookup index for the authentication pipeline 
-- (Used immediately upon login to find the user by their SSO token or local username)
CREATE INDEX IF NOT EXISTS idx_sys_userinfo_provider_lookup ON customer360.sys_userinfo(tenant_id, auth_provider, provider_subject_id);

-- ==========================================================
-- RBAC Role & Permission Tables
-- ==========================================================
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

CREATE INDEX IF NOT EXISTS idx_role_tenant ON customer360.sys_role (tenant_id);

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
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES customer360.sys_user (user_id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES customer360.sys_role (role_id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT now(),
    assigned_by UUID,
    PRIMARY KEY (tenant_id, user_id, role_id)
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

-- ==========================================================
-- Campaign & Performance Schema
-- ==========================================================
CREATE TABLE IF NOT EXISTS customer360.crm_campaign (
    campaign_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    user_id UUID REFERENCES customer360.sys_user(user_id), -- data owner

    -- Agentic email campaign planning and approval
    segment_id UUID,
    template_id UUID,
    approval_status VARCHAR(50) NOT NULL DEFAULT 'Draft',
    approved_by UUID,
    approved_at TIMESTAMP WITH TIME ZONE,
    strategy_summary TEXT,
    ai_plan JSONB,
    
    -- Dashboard Dimensions
    campaign_code VARCHAR(100),
    name TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'Draft',
    channel VARCHAR(100),
    platform VARCHAR(100),
    objective VARCHAR(100),
    
    -- Core details
    description TEXT,
    keywords TEXT[],
    lang TEXT DEFAULT 'en',
    embedding vector(1536),
    start_date DATE,
    end_date DATE,
    
    -- Financials
    budget_amount NUMERIC(18, 2),
    currency CHAR(3) DEFAULT 'VND',
    
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    
    CONSTRAINT uq_crm_campaign_code UNIQUE (tenant_id, campaign_code)
);

COMMENT ON TABLE customer360.crm_campaign IS 'CRM journey-graph entity: a marketing initiative. Updated to support omnichannel dashboard dimensions (channel, platform, objective, status). Responders are tracked via crm_campaign_member.';

-- Campaign Performance Daily
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_performance_daily (
    performance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    
    -- Time dimension for daily trend aggregation
    report_date DATE NOT NULL,
    
    -- Core funnel metrics tracking
    spend NUMERIC(18, 2) DEFAULT 0.00,
    impressions BIGINT DEFAULT 0,
    clicks BIGINT DEFAULT 0,
    conversions BIGINT DEFAULT 0,
    revenue_estimated NUMERIC(18, 2) DEFAULT 0.00,
    
    -- System audit fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    
    -- Constraint: Only one performance record per campaign per day per tenant
    CONSTRAINT uq_campaign_daily_performance UNIQUE (tenant_id, campaign_id, report_date)
);

COMMENT ON TABLE customer360.crm_campaign_performance_daily IS 'Daily aggregated performance metrics for omnichannel campaigns. Tracks spend, impressions, clicks, conversions, and estimated revenue over time.';

-- Campaign Performance Metrics View
CREATE OR REPLACE VIEW customer360.vw_campaign_performance_metrics AS
SELECT 
    c.tenant_id,
    c.campaign_id,
    c.campaign_code,
    c.name,
    c.status,
    c.channel,
    c.platform,
    c.objective,
    
    -- Base Aggregates
    COALESCE(SUM(p.spend), 0) AS total_spend,
    COALESCE(SUM(p.impressions), 0) AS total_impressions,
    COALESCE(SUM(p.clicks), 0) AS total_clicks,
    COALESCE(SUM(p.conversions), 0) AS total_conversions,
    COALESCE(SUM(p.revenue_estimated), 0) AS total_revenue,
    
    -- Derived KPI: Click-Through Rate (CTR %)
    CASE WHEN SUM(p.impressions) > 0 
         THEN ROUND((SUM(p.clicks)::NUMERIC / SUM(p.impressions)) * 100, 2) 
         ELSE 0.00 END AS ctr_percentage,
         
    -- Derived KPI: Conversion Rate (CVR %)
    CASE WHEN SUM(p.clicks) > 0 
         THEN ROUND((SUM(p.conversions)::NUMERIC / SUM(p.clicks)) * 100, 2) 
         ELSE 0.00 END AS cvr_percentage,
         
    -- Derived KPI: Cost Per Acquisition (CPA)
    CASE WHEN SUM(p.conversions) > 0 
         THEN ROUND(SUM(p.spend) / SUM(p.conversions), 0) 
         ELSE 0.00 END AS cpa,
         
    -- Derived KPI: Return on Ad Spend (ROAS)
    CASE WHEN SUM(p.spend) > 0 
         THEN ROUND(SUM(p.revenue_estimated) / SUM(p.spend), 2) 
         ELSE 0.00 END AS roas
         
FROM customer360.crm_campaign c
LEFT JOIN customer360.crm_campaign_performance_daily p 
    ON c.campaign_id = p.campaign_id
GROUP BY 
    c.tenant_id, 
    c.campaign_id, 
    c.campaign_code, 
    c.name, 
    c.status, 
    c.channel, 
    c.platform, 
    c.objective;

-- ----------------------------------------------------------------------------
-- INDEXES & ROW LEVEL SECURITY
-- ----------------------------------------------------------------------------

-- Indexes for crm_campaign
CREATE INDEX IF NOT EXISTS idx_crm_campaign_tenant ON customer360.crm_campaign (tenant_id);

-- Indexes for crm_campaign_performance_daily
-- Optimizes time-series queries (e.g., loading the daily spend trend chart)
CREATE INDEX IF NOT EXISTS idx_crm_campaign_perf_tenant_date 
    ON customer360.crm_campaign_performance_daily(tenant_id, report_date DESC);
    
-- Optimizes joins when fetching aggregate totals for a specific campaign list
CREATE INDEX IF NOT EXISTS idx_crm_campaign_perf_campaign 
    ON customer360.crm_campaign_performance_daily(campaign_id);

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
    lead_source_id UUID,
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
CREATE INDEX IF NOT EXISTS idx_crm_campaign_member_tenant ON customer360.crm_campaign_member (tenant_id);

CREATE INDEX IF NOT EXISTS idx_crm_lead_tenant ON customer360.crm_lead (tenant_id);

CREATE INDEX IF NOT EXISTS idx_crm_lead_source_tenant ON customer360.crm_lead_source (tenant_id);

CREATE INDEX IF NOT EXISTS idx_crm_contact_tenant ON customer360.crm_contact (tenant_id);

CREATE INDEX IF NOT EXISTS idx_crm_account_tenant ON customer360.crm_account (tenant_id);

CREATE INDEX IF NOT EXISTS idx_crm_opportunity_tenant ON customer360.crm_opportunity (tenant_id);

CREATE INDEX IF NOT EXISTS idx_crm_industry_tenant ON customer360.crm_industry (tenant_id);

---------------------------------------------------
-- MASTER PROFILES & IDENTITY RESOLUTION
---------------------------------------------------

-- ============================================================================
-- LEO CDP MASTER PROFILE SCHEMA (PostgreSQL 16+)
-- ============================================================================
-- Description: Golden customer profile containing the consolidated ("resolved")
-- identity across multiple data sources (Adjust, OneSignal, Web Tracking / GA4,
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
    -- Validated against active customer360.sys_domain codes at the API layer
    -- (core.utils.domains.validate_domain_value) rather than a hardcoded CHECK,
    -- so new domains can be onboarded via sys_domain without a schema migration.
    domain TEXT NOT NULL DEFAULT 'retail',

    -- ------------------------------------------------------------------------
    -- CORE IDENTITY (PII & DEMOGRAPHICS)
    -- Standard demographic data used for personalization and primary matching.
    -- ------------------------------------------------------------------------
    full_name TEXT,
    first_name TEXT,
    last_name TEXT,
    profile_picture_url TEXT,
    -- True if full_name/email/phone_number and any domain-level PII identifier
    -- (e.g. national_id stored in cdp_domain_profiles.domain_attributes) are
    -- SHA-256 hashed for privacy
    -- (e.g. hashed-match ingestion a la Meta/Google Customer Match). Whenever TRUE,
    -- current_persona_id (below) MUST be populated -- see the CHECK constraint at the end of
    -- this table -- since hashed PII can no longer be used as a human-readable label for
    -- browsing/semantic search. current_persona_id is computed by application code (see
    -- backend-system/identity_resolution/identity_resolution/persona.py), never by the DB.
    is_hashed BOOLEAN NOT NULL DEFAULT FALSE,

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
    -- Company/employer name for B2B matching and corporate account association
    company_name TEXT,

    -- ------------------------------------------------------------------------
    -- CROSS-CHANNEL IDENTITY GRAPH
    -- Identifiers resolved and merged from cdp_raw_profiles_stage.
    -- ------------------------------------------------------------------------
    -- Maps a source_system to its own customer identifier (Deterministic matching).
    -- e.g: adjust_id, google_ads_id, zalo_user_id, onesignal_id, firebase_id, etc.
    external_ids JSONB DEFAULT '{}'::JSONB,
    -- Hardware or app-specific identifiers for mobile attribution (IDFV, Android ID).
    device_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Mobile advertising identifiers for retargeting campaigns (Adjust IDFA/GAID).
    advertising_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Anonymous browser cookies for web tracking and session stitching.
    cookie_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Stored tokens for push notification services (OneSignal, Firebase).
    -- Format: {"fcm": "token_string", "apns": "token_string"}
    push_tokens JSONB DEFAULT '{}'::JSONB,

    -- NOTE: Domain-specific attributes are saved in cdp_domain_profiles.domain_attributes .

    -- ------------------------------------------------------------------------
    -- MARKETING & ENGAGEMENT
    -- Attribution data and computed fields used for audience building.
    -- ------------------------------------------------------------------------
    -- current_persona_id tracks the current assignment while preserving the
    -- full assignment history in cdp_customer_personas. Its FK is declared
    -- after cdp_customer_personas because both tables reference each other.
    current_persona_id UUID,
    persona_name TEXT, -- keep to ADD a short label for the persona (e.g., "Gen Z Shopper", "High-Value Investor") for quick filtering and segmentation in dashboards and queries.
    -- Longer, human-readable narrative summary of the customer (behavior,
    -- preferences, notable traits) usually generated by an LLM or the
    -- segmentation pipeline -- complements the short persona_name label above.
    persona_summary TEXT,

    -- First-touch channel attribution (e.g., 'organic_search', 'paid_social').
    acquisition_source TEXT,
    -- First-touch campaign attribution.
    acquisition_campaign TEXT,
    -- Computed labels for fast Audience Builder queries (e.g., 'gen_z', 'frequent_buyer').
    segmentation_tags TEXT[],
    -- Schemaless payload for flexible traits extracted dynamically.
    -- Format: {"occupation": "engineer", "income_segment": "high", "preferred_category": "electronics"}
    attributes JSONB DEFAULT '{}'::JSONB,
    -- Tracks explicit user consent across multiple channels. 
    -- Essential for omnichannel marketing compliance (e.g., GDPR, PDPA) before activating campaigns.
    -- Format: {"email_opt_in": true, "sms_opt_in": false, "push_opt_in": true}
    communication_preferences JSONB DEFAULT '{}'::JSONB,

    -- Analytics-related data sources and metrics.
    -- Example format: {"Data Source ID": {"page_views": 123, "click_through_rate": 0.05}}
    data_source_analytics JSONB DEFAULT '{}'::JSONB,

    -- ------------------------------------------------------------------------
    -- LINEAGE & AUDIT
    -- ------------------------------------------------------------------------
    -- Array of all external systems that have contributed data to this profile.
    source_systems TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Lineage pointer back to the raw_profile_id that initiated this profile.
    first_seen_raw_profile_id UUID,
    -- Denormalized count of raw profiles (cdp_profile_links, status='ACTIVE')
    -- merged into this golden record -- a CIR match-volume/confidence signal
    -- distinct from source_systems (which only tracks distinct SYSTEMS, not
    -- distinct raw touches).
    linked_raw_profile_count INTEGER NOT NULL DEFAULT 0,
    -- Timestamp Customer Identity Resolution (CIR) last (re)computed/updated
    -- this profile's identity graph; distinct from updated_at (any row touch)
    -- and scores_updated_at (ML scores only).
    last_identity_resolved_at TIMESTAMP WITH TIME ZONE,

    -- ------------------------------------------------------------------------
    -- CUSTOMER LIFECYCLE & ENGAGEMENT TRACKING
    -- The lead-to-customer journey can span months; these fields track where a
    -- profile currently sits in that journey and how fresh/actionable it is.
    -- ------------------------------------------------------------------------
    -- Date the profile first converted from lead/prospect to paying customer.
    customer_since DATE,
    -- Timestamp of the profile's most recent activity across any channel.
    -- Updated continuously by the streaming/event pipeline (not batch).
    last_activity_at TIMESTAMP WITH TIME ZONE,
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
    scores_updated_at TIMESTAMP WITH TIME ZONE,

    -- =========================================================================
    -- SYSTEM METADATA
    -- =========================================================================
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    status_code SMALLINT DEFAULT 1, -- 1: active, 0: inactive, -1: delete

    -- Business rule: a profile with hashed PII is not human-readable/searchable without a
    -- persona_name stand-in. Enforced at the DB layer in addition to application code.
    CONSTRAINT chk_cdp_mp_hashed_requires_persona_name CHECK (is_hashed = FALSE OR persona_name IS NOT NULL),
    CONSTRAINT chk_cdp_mp_probability_ranges CHECK (
        (lead_conversion_probability IS NULL OR lead_conversion_probability BETWEEN 0 AND 1)
        AND (churn_probability IS NULL OR churn_probability BETWEEN 0 AND 1)
        AND (identity_confidence_score IS NULL OR identity_confidence_score BETWEEN 0 AND 1)
    ),
    CONSTRAINT chk_cdp_mp_score_ranges CHECK (
        (engagement_score IS NULL OR engagement_score BETWEEN 0 AND 100)
        AND (overall_sentiment_score IS NULL OR overall_sentiment_score BETWEEN -1 AND 1)
        AND (profile_completeness_score IS NULL OR profile_completeness_score BETWEEN 0 AND 100)
    )
);

COMMENT ON TABLE customer360.cdp_master_profiles IS 'The golden/resolved customer profile (identity-resolution output): consolidated demographics, cross-channel identity graph, retail/banking/real-estate/travel/media/education domain attributes, marketing/persona fields, lineage, lifecycle tracking, and the full ML scoring block (lead, churn, CLV, CX, data quality). One row per real person per tenant+domain, built by CustomerIdentityResolver from cdp_raw_profiles_stage.';


-- ============================================================================
-- CUSTOMER DOMAIN PROFILES
-- ----------------------------------------------------------------------------
-- One Master Customer Profile may have multiple Domain Profiles.
--
-- Examples
-- --------
-- Thomas
--   ├── Retail Profile
--   ├── Banking Profile
--   ├── Travel Profile
--   ├── Education Profile
--   └── Media Profile
--
-- Each domain owns its own metadata, engagement score and AI persona.
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer360.cdp_domain_profiles (

    -- ========================================================================
    -- PRIMARY KEYS
    -- ========================================================================

    domain_profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Tenant
    tenant_id UUID NOT NULL
        REFERENCES customer360.sys_tenant(tenant_id),

    -- Parent Customer 360 profile
    master_profile_id UUID NOT NULL
        REFERENCES customer360.cdp_master_profiles(master_profile_id)
        ON DELETE CASCADE,

    -- Business Domain
    domain_id UUID NOT NULL
        REFERENCES customer360.sys_domain(domain_id),

    -- ========================================================================
    -- DOMAIN PROFILE
    -- ========================================================================

    -- Human friendly display name inside this domain.
    -- Example:
    --   Retail : "VIP Shopper"
    --   Banking: "Priority Customer"
    profile_name TEXT,

    -- Current lifecycle inside this business domain.
    --
    -- Example:
    -- prospect
    -- active
    -- inactive
    -- suspended
    -- closed
    lifecycle_stage TEXT,

    -- ========================================================================
    -- AI PROFILE
    -- ========================================================================

    -- AI generated customer persona for THIS domain only.
    persona_name TEXT,

    -- AI generated explanation.
    persona_summary TEXT,

    -- AI-computed engagement score (0-100).
    --
    -- Retail:
    -- purchase frequency
    -- store visits
    --
    -- Banking:
    -- transaction activity
    -- product usage
    --
    -- Travel:
    -- bookings
    -- trips
    --
    -- Media:
    -- reading
    -- watch time
    --
    -- Education:
    -- lesson completion
    -- study time
    engagement_score NUMERIC(5,2),

    -- ========================================================================
    -- DOMAIN METADATA
    -- ========================================================================

    -- Flexible business metadata.
    --
    -- Retail
    -- {
    --   "loyalty_id":"VIP001",
    --   "membership_tier":"Gold",
    --   "preferred_store":"HCM001"
    -- }
    --
    -- Banking
    -- {
    --   "cif_number":"1000001",
    --   "kyc_status":"verified",
    --   "risk_segment":"Low"
    -- }
    --
    -- Travel
    -- {
    --   "loyalty_program":"SkyTeam",
    --   "preferred_class":"Business"
    -- }
    --
    -- Education
    -- {
    --   "student_id":"ST100",
    --   "institution":"MIT"
    -- }
    --
    -- Media
    -- {
    --   "subscription_id":"NETFLIX001",
    --   "preferred_genres":["Technology","AI"]
    -- }
    -- Generic bag of domain-specific attributes.
    -- Example keys (not exhaustive):
    -- retail: loyalty_id, membership_tier, preferred_store_code
    -- banking: national_id, cif_number, account_numbers, kyc_status, risk_segment
    -- real_estate: property_types_of_interest, preferred_location_codes
    -- travel: travel_loyalty_program_id, preferred_travel_class
    -- media: media_subscription_id, preferred_content_genres
    -- education: student_id, institution_name
    domain_attributes JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- ========================================================================
    -- DOMAIN ANALYTICS
    -- ========================================================================

    -- Flexible AI output
    --
    -- Examples
    --
    -- propensity scores
    -- churn prediction
    -- recommendation vectors
    -- CLV
    -- next best action
    analytics JSONB DEFAULT '{}'::jsonb,

    -- ========================================================================
    -- ACTIVITY
    -- ========================================================================

    first_activity_at TIMESTAMP WITH TIME ZONE,

    last_activity_at TIMESTAMP WITH TIME ZONE,

    -- ========================================================================
    -- AUDIT
    -- ========================================================================

    status_code SMALLINT NOT NULL DEFAULT 1,

    created_at TIMESTAMP NOT NULL DEFAULT now(),

    updated_at TIMESTAMP NOT NULL DEFAULT now(),

    -- One profile per domain
    CONSTRAINT uq_cdp_domain_profiles
        UNIQUE(master_profile_id, domain_id),

    CONSTRAINT chk_cdp_domain_profiles_domain_attributes_object
        CHECK (jsonb_typeof(domain_attributes) = 'object'),

    CONSTRAINT chk_cdp_domain_profiles_analytics_object
        CHECK (analytics IS NULL OR jsonb_typeof(analytics) = 'object'),

    CONSTRAINT chk_cdp_domain_profiles_engagement_range
        CHECK (engagement_score IS NULL OR engagement_score BETWEEN 0 AND 100)

);

COMMENT ON TABLE customer360.cdp_domain_profiles IS
'Business-domain-specific customer profile attached to a Customer 360 Master Profile. Stores AI persona, engagement score and flexible domain metadata.';

CREATE INDEX IF NOT EXISTS idx_cdp_domain_profiles_tenant_domain
    ON customer360.cdp_domain_profiles (tenant_id, domain_id);

CREATE INDEX IF NOT EXISTS idx_cdp_domain_profiles_tenant_master
    ON customer360.cdp_domain_profiles (tenant_id, master_profile_id);

CREATE INDEX IF NOT EXISTS idx_cdp_domain_profiles_attributes
    ON customer360.cdp_domain_profiles
    USING GIN(domain_attributes);

CREATE INDEX IF NOT EXISTS idx_cdp_domain_profiles_analytics
    ON customer360.cdp_domain_profiles
    USING GIN(analytics);

-- ============================================================================
-- Auto-catalog trigger: every domain_attributes JSONB key gets registered
-- into cdp_profile_attributes automatically.
-- ============================================================================
-- Deliberately NOT one btree expression index per domain_attributes key
-- (((domain_attributes ->> 'some_key'))): in production a Customer 360 tenant
-- can introduce arbitrarily many domain-specific keys over time (per
-- tenant/domain), and one physical index per key does not scale -- every new
-- key would require a manual ALTER/migration, bloats pg_class/autovacuum
-- work, and most keys are never queried at all. Instead:
--   1. The single existing GIN index above (idx_cdp_domain_profiles_attributes)
--      already accelerates arbitrary-key containment/existence queries
--      (`domain_attributes @> '{"key":"value"}'`, `domain_attributes ? 'key'`)
--      for ANY key, present or future, with zero per-key maintenance.
--   2. The segmentation JOIN (core/crud/segmentation.py::DOMAIN_ATTRIBUTES_JOIN_SQL)
--      always starts FROM cdp_master_profiles and narrows to a single
--      cdp_domain_profiles row via idx_cdp_domain_profiles_tenant_master
--      (tenant_id, master_profile_id) BEFORE ever touching domain_attributes --
--      the ->>'key' = 'value' filter is then evaluated in-memory on that one
--      already-fetched row, not via an index scan on cdp_domain_profiles. So
--      per-key indexes were never actually load-bearing for that query shape.
--   3. If a SPECIFIC key becomes hot enough to need its own index (proven by
--      real query plans, not speculation), promote it to a real scalar column
--      instead of adding yet another expression index -- see the discussion
--      in this session's notes on when to graduate a JSONB key to a column.
CREATE OR REPLACE FUNCTION customer360.sync_domain_attribute_catalog()
RETURNS TRIGGER AS $$
DECLARE
    v_domain_code TEXT;
    v_attribute_group TEXT;
    v_key TEXT;
    v_value JSONB;
    v_data_type TEXT;
BEGIN
    IF NEW.domain_attributes IS NULL OR jsonb_typeof(NEW.domain_attributes) <> 'object' THEN
        RETURN NEW;
    END IF;

    SELECT domain_code INTO v_domain_code
    FROM customer360.sys_domain
    WHERE domain_id = NEW.domain_id;

    -- attribute_group has a fixed CHECK-constraint enum; fall back to
    -- GENERAL for any domain_code that doesn't map onto it 1:1.
    v_attribute_group := UPPER(COALESCE(v_domain_code, 'general'));
    IF v_attribute_group NOT IN (
        'SYSTEM', 'IDENTITY', 'IDENTITY_GRAPH', 'RETAIL', 'BANKING', 'REAL_ESTATE',
        'TRAVEL', 'MEDIA', 'EDUCATION', 'MARKETING', 'LINEAGE', 'LIFECYCLE',
        'LEAD_SCORING', 'CHURN_SCORING', 'CLV_SCORING', 'CX_SCORING', 'DATA_QUALITY', 'GENERAL'
    ) THEN
        v_attribute_group := 'GENERAL';
    END IF;

    FOR v_key, v_value IN SELECT * FROM jsonb_each(NEW.domain_attributes) LOOP
        v_data_type := CASE jsonb_typeof(v_value)
            WHEN 'array' THEN 'ARRAY'
            WHEN 'object' THEN 'JSONB'
            WHEN 'number' THEN 'NUMERIC'
            WHEN 'boolean' THEN 'BOOLEAN'
            ELSE 'TEXT'
        END;

        -- ON CONFLICT DO NOTHING is deliberate: this trigger only discovers
        -- brand-new keys. Once a key exists in the catalog (whether seeded
        -- or auto-discovered), a human curator may have since refined its
        -- name/description/is_pii/is_identity_resolution/is_segmentable --
        -- the trigger must never clobber that curation on a later write that
        -- merely reuses the same key.
        INSERT INTO customer360.cdp_profile_attributes (
            attribute_internal_code, master_profile_column, name, description,
            attribute_group, source_table, data_type, domain_scope,
            is_pii, status, is_segmentable, is_scoring_model, value_type, display_order
        ) VALUES (
            v_key, NULL, initcap(replace(v_key, '_', ' ')),
            'Auto-discovered from customer360.cdp_domain_profiles.domain_attributes by sync_domain_attribute_catalog().',
            v_attribute_group, 'cdp_domain_profiles', v_data_type,
            COALESCE(v_domain_code, 'all'),
            FALSE, 'ACTIVE',
            v_data_type NOT IN ('ARRAY', 'JSONB'), FALSE, 'metadata', 999
        )
        ON CONFLICT (attribute_internal_code) DO NOTHING;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION customer360.sync_domain_attribute_catalog() IS
'Auto-registers every JSONB key seen in cdp_domain_profiles.domain_attributes as a row in cdp_profile_attributes (source_table=cdp_domain_profiles), so the attribute catalog never silently drifts from what application code actually writes. Never overwrites an existing catalog row (ON CONFLICT DO NOTHING) -- curated metadata always wins over auto-discovery.';

DROP TRIGGER IF EXISTS trg_sync_domain_attribute_catalog ON customer360.cdp_domain_profiles;

CREATE TRIGGER trg_sync_domain_attribute_catalog
    AFTER INSERT OR UPDATE OF domain_attributes ON customer360.cdp_domain_profiles
    FOR EACH ROW
    EXECUTE FUNCTION customer360.sync_domain_attribute_catalog();


-- Raw profiles staging
-- Landing zone for every inbound source: Adjust (mobile attribution/install
-- events), OneSignal (engagement/push events), Web Tracking / GA4 (browser
-- events), and domain-specific sources like POS or Core Banking, for both the
-- retail and banking domains.
CREATE TABLE IF NOT EXISTS customer360.cdp_raw_profiles_stage (
    raw_profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    -- Concrete inbound connector lineage. This remains separate from
    -- source_system because several connectors can emit the same protocol.
    data_source_id UUID,
    
    -- Analytics-related data sources and metrics.
    -- Example format: {"Data Source ID": {"page_views": 123, "click_through_rate": 0.05}}
    data_source_analytics JSONB DEFAULT '{}'::JSONB,

    -- Data owner: internal sys_user who created/manages this row (nullable -- rows are
    -- normally landed by ingestion pipelines, not an interactive admin user).
    user_id UUID REFERENCES customer360.sys_user(user_id),
    -- Validated against sys_domain at the API layer, see cdp_master_profiles.domain above.
    domain TEXT NOT NULL DEFAULT 'banking',
    source_system TEXT NOT NULL,        -- 'Adjust' | 'OneSignal' | 'WebTracking' | 'CoreBanking' | 'POS' | ...
    channel TEXT,                       -- 'mobile_app' | 'web' | 'pos' | 'call_center' | ...

    -- Core identity fields as reported by the source
    profile_type TEXT CHECK (
        profile_type IN (
            'individual',
            'business',
            'organization'
        )
    ) DEFAULT 'individual',
    external_customer_id TEXT, -- Adjust customer_user_id / OneSignal unique_id / core banking CIF / loyalty_id
    full_name TEXT,
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    phone_number TEXT,
    national_id TEXT, -- banking KYC identifier (CMND/CCCD/passport)
    date_of_birth DATE, -- for probabilistic/fuzzy matching

    -- Physical address (structured for fuzzy matching on Address entity)
    address_line1 TEXT,
    address_line2 TEXT,
    city TEXT,
    state_province TEXT,
    postal_code TEXT,
    country TEXT,
    company_name TEXT,

    -- Device & marketing identity (Adjust / OneSignal / Web Tracking)
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

    -- Granular Adjust device/app identifiers and metadata (see
    -- all-data-simulator/data-dictionary/adjust-metadata.md sections 3.2/3.4).
    -- idfa/idfv/android_id/imei are the raw per-platform values that ingestion
    -- maps onto device_id/advertising_id above for CIR matching; kept here too
    -- for lineage/audit and as a fallback if the mapping needs to be redone.
    idfa TEXT, -- iOS advertising id; all-zero when ATT is not authorized (see att below)
    idfv TEXT, -- iOS vendor id
    android_id TEXT,
    imei TEXT, -- legacy Android device id, restricted on modern OS versions -- do not use as a matching key
    att TEXT, -- iOS 14+ ATT status: not_determined | denied | authorized | restricted
    device_type TEXT, -- phone | tablet | other
    os_version TEXT,
    sdk_version TEXT, -- Adjust SDK version
    app_id TEXT,
    app_name TEXT,
    bundle_id TEXT,
    operator TEXT, -- SIM MCCMNC carrier name
    carrier TEXT, -- Android carrier name (getSimCarrierIdName)
    network_type TEXT, -- e.g. wifi | cellular
    wifi BOOLEAN,
    language TEXT, -- device locale, e.g. vi-VN
    gp_broadcast_referrer TEXT,

    -- Marketing attribution (Adjust install/campaign touch + Web UTM).
    -- See adjust-metadata.md section 3.1; sub_param_1..5 and other rarely
    -- used custom link params are intentionally not broken out into columns
    -- here -- they land in event_payload instead.
    media_source TEXT,
    campaign TEXT,
    campaign_id TEXT, -- af_c_id
    campaign_type TEXT, -- UA | Organic | Retargeting | Unknown
    match_type TEXT, -- SRN | id_matching | probabilistic | deeplink | ...
    conversion_type TEXT, -- install | reinstall | re-engagement | unknown
    is_organic BOOLEAN,
    is_retargeting BOOLEAN,
    is_primary_attribution BOOLEAN,
    attributed_touch_type TEXT, -- click | impression | pre-installed
    attributed_touch_time TIMESTAMP WITH TIME ZONE,
    click_time TIMESTAMP WITH TIME ZONE,
    install_time TIMESTAMP WITH TIME ZONE,
    reattributed_touch_time TIMESTAMP WITH TIME ZONE,
    reattributed_touch_type TEXT,
    media_channel TEXT, -- af_channel traffic sub-channel (e.g. YouTube, Instagram) -- distinct from the distribution `channel` column above
    agency TEXT, -- af_prt
    adset TEXT,
    adset_id TEXT,
    ad_name TEXT,
    ad_id TEXT,
    ad_type TEXT,
    keywords TEXT,
    site_id TEXT, -- af_siteid (publisher)
    sub_site_id TEXT,
    cost_model TEXT,
    cost_value NUMERIC(12, 4),
    cost_currency CHAR(3),
    http_referrer TEXT,
    fb_campaign_id TEXT,
    fb_adset_id TEXT,
    fb_adset_name TEXT,
    fb_ad_id TEXT,
    fb_ad_name TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,

    -- Protect360 fraud signals (see adjust-metadata.md section 3.8)
    blocked_reason TEXT,
    blocked_reason_value TEXT,

    event_name TEXT,                    -- e.g. install, login, page_view, purchase
    event_time TIMESTAMP WITH TIME ZONE,
    event_payload JSONB,                -- full raw source payload / extracted attributes

    status_code SMALLINT DEFAULT 1,  -- 3: processed, 2: in-progress, 1: new, 0: inactive, -1: delete
    processed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP DEFAULT now()
);

COMMENT ON TABLE customer360.cdp_raw_profiles_stage IS 'Landing zone for every inbound source (Adjust, OneSignal, Web Tracking/GA4, POS, Core Banking, ...) before Customer Identity Resolution (CIR). Carries per-source identity + marketing attribution (including granular Adjust device/attribution/Protect360 fields, see adjust-metadata.md) and a processing-queue status_code (1 new -> 2 in-progress -> 3 processed).';

-- Links (raw → master)
CREATE TABLE IF NOT EXISTS customer360.cdp_profile_links (
    link_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    user_id UUID REFERENCES customer360.sys_user (user_id), -- data owner (nullable, pipeline-created)
    raw_profile_id UUID NOT NULL REFERENCES customer360.cdp_raw_profiles_stage (raw_profile_id),
    master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles (master_profile_id),
    match_score NUMERIC(5, 4),
    match_method TEXT,
    -- Link lifecycle state, e.g. for unmerge/profile-split scenarios.
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'HISTORICAL', 'UNLINKED', 'SUPERSEDED')),
    unlinked_at TIMESTAMP WITH TIME ZONE,
    unlinked_reason TEXT,
    unlinked_by UUID REFERENCES customer360.sys_user (user_id),
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE (tenant_id, raw_profile_id)
);

COMMENT ON TABLE customer360.cdp_profile_links IS 'Join table recording every raw_profile_id -> master_profile_id link made by CIR, with match_score/match_method. Unique per (tenant_id, raw_profile_id). status/unlinked_* track unmerge/profile-split lifecycle.';

CREATE INDEX IF NOT EXISTS idx_cdp_profile_links_status ON customer360.cdp_profile_links (tenant_id, status)
WHERE
    status = 'ACTIVE';

-- Backs GET /master-profiles/{id}/links (core/routers/identity_api.py) and the
-- reporting duplicate-master queries (core/crud/identity.py), which both
-- filter/group by master_profile_id -- without this, those lookups fall back
-- to a full sequential scan of cdp_profile_links once the table reaches
-- millions of rows (no automatic index is created for a bare FK column).
CREATE INDEX IF NOT EXISTS idx_cdp_profile_links_master ON customer360.cdp_profile_links (tenant_id, master_profile_id);


-- ============================================================================
-- CUSTOMER PERSONA RESOLUTION ("from identity matching to identity
-- understanding"): a genuine many-to-many relationship.
--
--   cdp_persona_archetypes  -- ONE shared, reusable persona definition per
--                              (tenant_id, domain, persona_code), e.g.
--                              'retail_gen_z_shopper'. Carries the LLM-
--                              assisted persona_name/persona_summary, a
--                              centroid persona_embedding + centroid
--                              component scores (the "lookalike model" for
--                              this archetype), and a denormalized
--                              matched_profile_count.
--   cdp_customer_personas   -- the versioned MATCH/assignment of ONE master
--                              profile to ONE archetype (lookalike score +
--                              that profile's own component scores). Every
--                              (re)computation inserts a NEW row
--                              (computed_version increments per tenant_id/
--                              master_profile_id/persona_archetype_id)
--                              rather than overwriting, so the full history
--                              of how a person's persona evolved is
--                              preserved; only the latest row per
--                              master_profile_id has is_active = TRUE, and
--                              cdp_master_profiles.current_persona_id always
--                              points at it.
--
-- Net effect: 1 master profile -> many persona MATCHES over time (still
-- true), AND 1 persona archetype -> many master profiles at once (lookalike
-- audience) -- both directions are now real FK-backed relationships instead
-- of the previous design, where every persona row was hard-tied to exactly
-- one master profile and "shared" personas only coincided by string reuse.
--
-- cdp_persona_features / cdp_persona_score_details / cdp_persona_history are
-- the supporting explainability tables (unchanged): the raw signals that fed
-- one match's computation, its per-component score breakdown, and an audit
-- trail of material persona changes over time, respectively -- all keyed off
-- cdp_customer_personas.persona_id (the match row), not the archetype.
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer360.cdp_persona_archetypes
(
    persona_archetype_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id               UUID NOT NULL
        REFERENCES customer360.sys_tenant(tenant_id),
    domain                  TEXT NOT NULL,

    -- Stable slug identifying this archetype, e.g. 'gen_z_shopper'.
    persona_code            VARCHAR(50) NOT NULL,
    persona_name            VARCHAR(255) NOT NULL,
    persona_category        VARCHAR(100),
    persona_summary         TEXT,

    llm_provider            VARCHAR(50),
    llm_model               VARCHAR(100),

    -- Lookalike model: centroid embedding + centroid component scores across
    -- every ACTIVE cdp_customer_personas match currently assigned to this
    -- archetype -- used to find/rank "lookalike" master profiles (nearest
    -- centroid via vector_cosine_ops) that aren't matched yet.
    persona_embedding       VECTOR(768),
    centroid_behavior_score     NUMERIC(6,2),
    centroid_engagement_score  NUMERIC(6,2),
    centroid_financial_score   NUMERIC(6,2),
    centroid_loyalty_score     NUMERIC(6,2),
    centroid_relationship_score NUMERIC(6,2),
    centroid_risk_score        NUMERIC(6,2),

    -- Denormalized COUNT(DISTINCT master_profile_id) across ACTIVE matches,
    -- maintained by trg_sync_persona_archetype_match_count below. This is
    -- the "Total Matched Profiles" figure the Persona Management admin UI
    -- must display per archetype.
    matched_profile_count   INTEGER NOT NULL DEFAULT 0,

    is_active               BOOLEAN NOT NULL DEFAULT TRUE,

    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(tenant_id, domain, persona_code)
);

COMMENT ON TABLE customer360.cdp_persona_archetypes IS 'Shared, reusable persona definition (one row per tenant+domain+persona_code) -- the LLM-assisted persona_name/persona_summary, a lookalike-model centroid persona_embedding + centroid component scores, and a denormalized matched_profile_count. Many cdp_master_profiles rows can share one archetype via cdp_customer_personas, and the Persona Management admin UI lists archetypes (not raw match rows) with their matched_profile_count.';

CREATE TABLE IF NOT EXISTS customer360.cdp_customer_personas
(
    persona_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    tenant_id               UUID NOT NULL
        REFERENCES customer360.sys_tenant(tenant_id),
    domain                  TEXT NOT NULL,

    master_profile_id        UUID NOT NULL
        REFERENCES customer360.cdp_master_profiles(master_profile_id)
        ON DELETE CASCADE,

    -- The shared archetype this profile is currently matched/assigned to --
    -- this FK (many match rows -> one archetype) is what makes the
    -- relationship many-to-many instead of the previous 1-master-profile-
    -- per-persona-row design.
    persona_archetype_id    UUID NOT NULL
        REFERENCES customer360.cdp_persona_archetypes(persona_archetype_id)
        ON DELETE CASCADE,

    -- Lookalike match quality: how well this profile fits persona_archetype_id's
    -- centroid (e.g. cosine similarity of persona_embedding vs the profile's
    -- own feature vector). Distinct from confidence_score (CIR identity
    -- confidence) and persona_score (this profile's own composite score).
    match_score             NUMERIC(5,4) DEFAULT 0,

    persona_score           NUMERIC(8,2) DEFAULT 0,

    confidence_score        NUMERIC(5,4) DEFAULT 0,

    behavior_score          NUMERIC(6,2) DEFAULT 0,

    engagement_score        NUMERIC(6,2) DEFAULT 0,

    financial_score         NUMERIC(6,2) DEFAULT 0,

    loyalty_score           NUMERIC(6,2) DEFAULT 0,

    relationship_score      NUMERIC(6,2) DEFAULT 0,

    risk_score              NUMERIC(6,2) DEFAULT 0,

    lifecycle_stage         VARCHAR(50),

    customer_value_tier     VARCHAR(50),

    risk_level              VARCHAR(30),

    next_best_action        TEXT,

    computed_version        INTEGER DEFAULT 1,

    is_active               BOOLEAN DEFAULT TRUE,

    computed_at             TIMESTAMPTZ DEFAULT NOW(),

    expires_at              TIMESTAMPTZ,

    created_at              TIMESTAMPTZ DEFAULT NOW(),

    updated_at              TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(tenant_id,
           master_profile_id,
           persona_archetype_id,
           computed_version)
);

COMMENT ON TABLE customer360.cdp_customer_personas IS 'Versioned match/assignment of ONE master profile to ONE cdp_persona_archetypes row, computed by backend-system/identity_resolution''s PersonaResolutionEngine: this profile''s own behavior/engagement/financial/loyalty/relationship/risk component scores, an overall persona_score, customer_value_tier/risk_level/next_best_action, and match_score (lookalike fit vs the archetype centroid). Each recomputation inserts a new row (computed_version); only the latest row per master_profile_id has is_active = TRUE. Many rows (across many master profiles) can reference the same persona_archetype_id -- that many-to-many fan-in is the whole point of this table.';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_cdp_master_profile_current_persona'
          AND conrelid = 'customer360.cdp_master_profiles'::regclass
    ) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I(%I) ON DELETE SET NULL',
            'customer360',
            'cdp_master_profiles',
            'fk_cdp_master_profile_current_persona',
            'current_persona_id',
            'customer360',
            'cdp_customer_personas',
            'persona_id'
        );
    END IF;
END $$;

-- Maintains cdp_persona_archetypes.matched_profile_count as a true
-- COUNT(DISTINCT master_profile_id) over ACTIVE matches, so the Persona
-- Management admin UI never has to compute it ad hoc client-side.
CREATE OR REPLACE FUNCTION customer360.sync_persona_archetype_match_count()
RETURNS TRIGGER AS $$
DECLARE
    v_archetype_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_archetype_id := OLD.persona_archetype_id;
    ELSE
        v_archetype_id := NEW.persona_archetype_id;
    END IF;

    UPDATE customer360.cdp_persona_archetypes
    SET matched_profile_count = (
        SELECT COUNT(DISTINCT master_profile_id)
        FROM customer360.cdp_customer_personas
        WHERE persona_archetype_id = v_archetype_id AND is_active = TRUE
    )
    WHERE persona_archetype_id = v_archetype_id;

    -- Recompute the OLD archetype too when a row is re-pointed at a
    -- different archetype via UPDATE.
    IF TG_OP = 'UPDATE' AND OLD.persona_archetype_id IS DISTINCT FROM NEW.persona_archetype_id THEN
        UPDATE customer360.cdp_persona_archetypes
        SET matched_profile_count = (
            SELECT COUNT(DISTINCT master_profile_id)
            FROM customer360.cdp_customer_personas
            WHERE persona_archetype_id = OLD.persona_archetype_id AND is_active = TRUE
        )
        WHERE persona_archetype_id = OLD.persona_archetype_id;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_persona_archetype_match_count ON customer360.cdp_customer_personas;

CREATE TRIGGER trg_sync_persona_archetype_match_count
    AFTER INSERT OR UPDATE OF persona_archetype_id, is_active OR DELETE ON customer360.cdp_customer_personas
    FOR EACH ROW
    EXECUTE FUNCTION customer360.sync_persona_archetype_match_count();

CREATE INDEX IF NOT EXISTS idx_cdp_mp_current_persona ON customer360.cdp_master_profiles (current_persona_id)
WHERE
    current_persona_id IS NOT NULL;

-- Primary access pattern: "all persona versions for this master profile" /
-- "the current persona for this master profile" (partial index, since most
-- queries only care about the single is_active = TRUE row).
CREATE INDEX IF NOT EXISTS idx_cdp_customer_personas_master ON customer360.cdp_customer_personas (tenant_id, master_profile_id, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_cdp_customer_personas_active ON customer360.cdp_customer_personas (tenant_id, master_profile_id)
WHERE
    is_active = TRUE;

-- Primary access pattern for the M:N fan-out: "every master profile
-- currently matched to this archetype" (Persona Management drill-down).
CREATE INDEX IF NOT EXISTS idx_cdp_customer_personas_archetype ON customer360.cdp_customer_personas (tenant_id, persona_archetype_id)
WHERE
    is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_cdp_persona_archetypes_tenant_domain ON customer360.cdp_persona_archetypes (tenant_id, domain)
WHERE
    is_active = TRUE;

-- Lookalike similarity search: nearest archetype centroid to a candidate
-- profile's own embedding.
CREATE INDEX IF NOT EXISTS idx_cdp_persona_archetypes_embedding_ivfflat ON customer360.cdp_persona_archetypes USING ivfflat (persona_embedding vector_cosine_ops)
WITH (lists = 100);

CREATE TABLE IF NOT EXISTS customer360.cdp_persona_features
(
    feature_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    persona_id          UUID NOT NULL
        REFERENCES customer360.cdp_customer_personas(persona_id)
        ON DELETE CASCADE,

    feature_code        VARCHAR(100) NOT NULL,

    feature_name        VARCHAR(255),

    feature_type        VARCHAR(50),

    numeric_value       NUMERIC,

    text_value          TEXT,

    boolean_value       BOOLEAN,

    source_system       TEXT,

    confidence_score    NUMERIC(5,4),

    computed_at         TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE customer360.cdp_persona_features IS 'Raw/derived signals (tenure, channel breadth, CLV, churn probability, KYC status, ...) that fed one cdp_customer_personas computation -- the explainability input side of the persona engine.';

CREATE INDEX IF NOT EXISTS idx_cdp_persona_features_persona ON customer360.cdp_persona_features (persona_id);

CREATE TABLE IF NOT EXISTS customer360.cdp_persona_score_details
(
    score_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    persona_id          UUID NOT NULL
        REFERENCES customer360.cdp_customer_personas(persona_id)
        ON DELETE CASCADE,

    score_type          VARCHAR(100),

    score_value         NUMERIC(8,2),

    score_weight        NUMERIC(5,2),

    score_formula       TEXT,

    explanation         TEXT,

    created_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE customer360.cdp_persona_score_details IS 'Per-component score breakdown (behavior/engagement/financial/loyalty/relationship/risk) for one cdp_customer_personas row, with the weight/formula/explanation behind each -- the explainability output side of the persona engine.';

CREATE INDEX IF NOT EXISTS idx_cdp_persona_score_details_persona ON customer360.cdp_persona_score_details (persona_id);

CREATE TABLE IF NOT EXISTS customer360.cdp_persona_history
(
    history_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    persona_id          UUID NOT NULL
        REFERENCES customer360.cdp_customer_personas(persona_id)
        ON DELETE CASCADE,

    old_persona_name    TEXT,

    new_persona_name    TEXT,

    old_score           NUMERIC(8,2),

    new_score           NUMERIC(8,2),

    change_reason       TEXT,

    model_version       VARCHAR(50),

    changed_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE customer360.cdp_persona_history IS 'Audit trail of material persona changes over time (persona_name and/or persona_score delta above PersonaResolutionEngine.HISTORY_SCORE_DELTA_THRESHOLD), one row per change, linked to the NEW cdp_customer_personas row that triggered it.';

CREATE INDEX IF NOT EXISTS idx_cdp_persona_history_persona ON customer360.cdp_persona_history (persona_id);

-- ============================================================================
-- cdp_persona_config: persona-engine scoring/config registry
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer360.cdp_persona_config
(
    config_key          VARCHAR(120) PRIMARY KEY,
    config_value        TEXT NOT NULL,
    data_type           VARCHAR(20) NOT NULL CHECK (data_type IN ('INTEGER', 'NUMERIC', 'BOOLEAN', 'VARCHAR', 'JSONB')),
    config_description  TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by          VARCHAR(100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE customer360.cdp_persona_config IS 'Typed runtime config registry for PersonaResolutionEngine constants (thresholds, weights, caps, bonuses, and history delta).';

CREATE INDEX IF NOT EXISTS idx_cdp_persona_config_active ON customer360.cdp_persona_config (is_active);

INSERT INTO customer360.cdp_persona_config (config_key, config_value, data_type, config_description, is_active, updated_by)
VALUES
    ('RISK_LEVEL_CRITICAL_THRESHOLD', '80.0', 'NUMERIC', 'Risk level threshold: critical', TRUE, 'system_seed'),
    ('RISK_LEVEL_HIGH_THRESHOLD', '60.0', 'NUMERIC', 'Risk level threshold: high', TRUE, 'system_seed'),
    ('RISK_LEVEL_MEDIUM_THRESHOLD', '40.0', 'NUMERIC', 'Risk level threshold: medium', TRUE, 'system_seed'),

    ('LIFECYCLE_BEHAVIOR_PROSPECT_BASE', '20.0', 'NUMERIC', 'Behavior base score for prospect', TRUE, 'system_seed'),
    ('LIFECYCLE_BEHAVIOR_LEAD_BASE', '40.0', 'NUMERIC', 'Behavior base score for lead', TRUE, 'system_seed'),
    ('LIFECYCLE_BEHAVIOR_CUSTOMER_BASE', '65.0', 'NUMERIC', 'Behavior base score for customer', TRUE, 'system_seed'),
    ('LIFECYCLE_BEHAVIOR_VIP_BASE', '95.0', 'NUMERIC', 'Behavior base score for VIP', TRUE, 'system_seed'),
    ('LIFECYCLE_BEHAVIOR_DORMANT_BASE', '30.0', 'NUMERIC', 'Behavior base score for dormant', TRUE, 'system_seed'),
    ('LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE', '35.0', 'NUMERIC', 'Behavior base score for churn_risk', TRUE, 'system_seed'),
    ('LIFECYCLE_BEHAVIOR_DEFAULT_BASE', '30.0', 'NUMERIC', 'Behavior base score default fallback', TRUE, 'system_seed'),

    ('ENGAGEMENT_RECENCY_UNKNOWN_SCORE', '30.0', 'NUMERIC', 'Engagement recency score when unknown', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_RECENT_7D_SCORE', '100.0', 'NUMERIC', 'Engagement recency score <= 7 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_RECENT_30D_SCORE', '80.0', 'NUMERIC', 'Engagement recency score <= 30 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_RECENT_90D_SCORE', '50.0', 'NUMERIC', 'Engagement recency score <= 90 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_RECENT_180D_SCORE', '25.0', 'NUMERIC', 'Engagement recency score <= 180 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_STALE_SCORE', '10.0', 'NUMERIC', 'Engagement recency score stale', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_THRESHOLD_7D', '7', 'INTEGER', 'Engagement recency threshold 7 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_THRESHOLD_30D', '30', 'INTEGER', 'Engagement recency threshold 30 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_THRESHOLD_90D', '90', 'INTEGER', 'Engagement recency threshold 90 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_THRESHOLD_180D', '180', 'INTEGER', 'Engagement recency threshold 180 days', TRUE, 'system_seed'),
    ('ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM', '10.0', 'NUMERIC', 'Engagement bonus per source system', TRUE, 'system_seed'),
    ('ENGAGEMENT_CHANNEL_BONUS_CAP', '30.0', 'NUMERIC', 'Engagement channel bonus cap', TRUE, 'system_seed'),
    ('ENGAGEMENT_RECENCY_WEIGHT', '0.7', 'NUMERIC', 'Engagement recency blend weight', TRUE, 'system_seed'),

    ('FINANCIAL_CLV_REFERENCE_DEFAULT', '5000.0', 'NUMERIC', 'Financial score CLV reference', TRUE, 'system_seed'),
    ('FINANCIAL_SCORE_MULTIPLIER', '100.0', 'NUMERIC', 'Financial score multiplier', TRUE, 'system_seed'),

    ('LOYALTY_TIER_PLATINUM_BASE', '100.0', 'NUMERIC', 'Loyalty tier base platinum', TRUE, 'system_seed'),
    ('LOYALTY_TIER_GOLD_BASE', '80.0', 'NUMERIC', 'Loyalty tier base gold', TRUE, 'system_seed'),
    ('LOYALTY_TIER_SILVER_BASE', '60.0', 'NUMERIC', 'Loyalty tier base silver', TRUE, 'system_seed'),
    ('LOYALTY_TIER_BRONZE_BASE', '40.0', 'NUMERIC', 'Loyalty tier base bronze', TRUE, 'system_seed'),
    ('LOYALTY_TIER_DEFAULT_BASE', '20.0', 'NUMERIC', 'Loyalty tier base default', TRUE, 'system_seed'),
    ('LOYALTY_TENURE_WEIGHT', '0.8', 'NUMERIC', 'Loyalty tier blend weight', TRUE, 'system_seed'),
    ('LOYALTY_TENURE_BONUS_PER_YEAR', '20.0', 'NUMERIC', 'Loyalty tenure bonus per year', TRUE, 'system_seed'),
    ('LOYALTY_TENURE_BONUS_CAP', '20.0', 'NUMERIC', 'Loyalty tenure bonus cap', TRUE, 'system_seed'),
    ('LOYALTY_TENURE_REFERENCE_DAYS', '365.0', 'NUMERIC', 'Loyalty tenure days reference', TRUE, 'system_seed'),

    ('RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM', '20.0', 'NUMERIC', 'Relationship bonus per source system', TRUE, 'system_seed'),
    ('RELATIONSHIP_CHANNEL_BONUS_CAP', '60.0', 'NUMERIC', 'Relationship channel bonus cap', TRUE, 'system_seed'),
    ('RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT', '10.0', 'NUMERIC', 'Relationship bonus per contact', TRUE, 'system_seed'),
    ('RELATIONSHIP_CONTACT_BONUS_CAP', '40.0', 'NUMERIC', 'Relationship contact bonus cap', TRUE, 'system_seed'),

    ('RISK_SCORE_CHURN_MULTIPLIER', '100.0', 'NUMERIC', 'Risk scoring multiplier for churn probability', TRUE, 'system_seed'),
    ('RISK_SCORE_DEFAULT_CHURN_BASE', '20.0', 'NUMERIC', 'Risk scoring default base if churn is missing', TRUE, 'system_seed'),
    ('RISK_SEGMENT_BONUS_LOW', '0.0', 'NUMERIC', 'Risk segment bonus low', TRUE, 'system_seed'),
    ('RISK_SEGMENT_BONUS_MEDIUM', '15.0', 'NUMERIC', 'Risk segment bonus medium', TRUE, 'system_seed'),
    ('RISK_SEGMENT_BONUS_HIGH', '30.0', 'NUMERIC', 'Risk segment bonus high', TRUE, 'system_seed'),
    ('RISK_SEGMENT_BONUS_CRITICAL', '45.0', 'NUMERIC', 'Risk segment bonus critical', TRUE, 'system_seed'),
    ('KYC_STATUS_BONUS_VERIFIED', '0.0', 'NUMERIC', 'KYC status bonus verified', TRUE, 'system_seed'),
    ('KYC_STATUS_BONUS_PENDING', '10.0', 'NUMERIC', 'KYC status bonus pending', TRUE, 'system_seed'),
    ('KYC_STATUS_BONUS_UNVERIFIED', '20.0', 'NUMERIC', 'KYC status bonus unverified', TRUE, 'system_seed'),
    ('KYC_STATUS_BONUS_REJECTED', '40.0', 'NUMERIC', 'KYC status bonus rejected', TRUE, 'system_seed'),

    ('VALUE_TIER_CHAMPION_THRESHOLD', '80.0', 'NUMERIC', 'Customer value tier threshold champion', TRUE, 'system_seed'),
    ('VALUE_TIER_HIGH_VALUE_THRESHOLD', '60.0', 'NUMERIC', 'Customer value tier threshold high_value', TRUE, 'system_seed'),
    ('VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD', '35.0', 'NUMERIC', 'Customer value tier threshold growth_potential', TRUE, 'system_seed'),

    ('SCORE_WEIGHT_BEHAVIOR', '0.20', 'NUMERIC', 'Persona score weight behavior', TRUE, 'system_seed'),
    ('SCORE_WEIGHT_ENGAGEMENT', '0.20', 'NUMERIC', 'Persona score weight engagement', TRUE, 'system_seed'),
    ('SCORE_WEIGHT_FINANCIAL', '0.20', 'NUMERIC', 'Persona score weight financial', TRUE, 'system_seed'),
    ('SCORE_WEIGHT_LOYALTY', '0.15', 'NUMERIC', 'Persona score weight loyalty', TRUE, 'system_seed'),
    ('SCORE_WEIGHT_RELATIONSHIP', '0.10', 'NUMERIC', 'Persona score weight relationship', TRUE, 'system_seed'),
    ('SCORE_WEIGHT_RISK', '0.15', 'NUMERIC', 'Persona score weight risk inverse component', TRUE, 'system_seed'),
    ('SCORE_WEIGHTS_POSITIVE_SUM', '0.85', 'NUMERIC', 'Sanity helper for positive score weights', TRUE, 'system_seed'),

    ('PERSONA_HISTORY_SCORE_DELTA_THRESHOLD', '5.0', 'NUMERIC', 'Minimum absolute score delta for history record', TRUE, 'system_seed')
ON CONFLICT (config_key) DO NOTHING;

---------------------------------------------------
-- EVENT CATALOG (governed cross-domain event vocabulary)
---------------------------------------------------
CREATE TABLE IF NOT EXISTS customer360.cdp_event_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    -- Validated against sys_domain ('all' + active domain codes) at the API
    -- layer, see cdp_master_profiles.domain above.
    domain_scope TEXT NOT NULL DEFAULT 'all',
    description TEXT,
    is_conversion_default BOOLEAN NOT NULL DEFAULT FALSE,
    -- Conceptual name of the payload key that should be promoted to the
    -- canonical S3 event envelope's event_value (documentation aid only).
    value_field TEXT,
    display_order INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE customer360.cdp_event_catalog IS 'Governed vocabulary of event_category/event_name pairs (seeded below) across GENERAL/FEEDBACK/COMMERCE/FINANCE/STOCK_TRADING/TRAVEL/REAL_ESTATE. The catalog is not FK-enforced by the S3 event lake, so ingestion is never blocked by a missing catalog row; it exists for discoverability/governance.';

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
-- Uses CREATE TABLE IF NOT EXISTS so the schema is defined in one place.
-- ============================================================================
CREATE TABLE IF NOT EXISTS customer360.cdp_profile_attributes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

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
    -- Validated against sys_domain ('all' + active domain codes) at the API
    -- layer, see cdp_master_profiles.domain above.
    domain_scope VARCHAR(20) NOT NULL DEFAULT 'all',
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

    -- Rank hierarchy used during limit demotion (1 = highest priority, e.g. user_id).
    priority_rank INTEGER NOT NULL DEFAULT 99,
    -- Maximum allowed unique values on a single master profile for this identifier.
    value_limit INTEGER NOT NULL DEFAULT 5,
    -- Window for limit enforcement: 1_ever, 5_weekly, 5_monthly, 5_annually.
    limit_timeframe VARCHAR(50) NOT NULL DEFAULT '5_annually',
    -- Exact string values blocked from being promoted to external identifiers.
    blocked_values JSONB NOT NULL DEFAULT '["null", "-1", "anonymous", "void", "abc123"]'::JSONB,
    -- Regex patterns blocked from being promoted to external identifiers.
    blocked_patterns TEXT[] NOT NULL DEFAULT ARRAY['^[0-]*$'],

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

COMMENT ON TABLE customer360.cdp_profile_attributes IS 'Metadata-driven attribute catalog for cdp_master_profiles schema columns used by identity-resolution engine (CIR). One row per master-profile column (email, phone_number, device_id, etc.) with consolidation rules, matching strategies, and schema hints. Domain-specific attributes (national_id, kyc_status, loyalty_id, etc.) must be included; they live as JSONB keys in cdp_domain_profiles.domain_attributes.';

-- ==========================================================
-- Scoring Models Registry
-- ==========================================================
-- This table acts as the central dictionary for all AI, ML, 
-- and rule-based models that output computed fields (like Churn, CLV, 
-- or Lead Scores) into the customer profiles.

CREATE TABLE IF NOT EXISTS customer360.cdp_scoring_models (
    -- The user-requested primary key. This exact string must match 
    -- the 'scoring_model_name' in cdp_profile_attributes.
    scoring_model_name VARCHAR(100) PRIMARY KEY,
    
    -- Display and organizational metadata
    display_name VARCHAR(255) NOT NULL,
    description TEXT,
    
    -- Identifies the algorithmic approach
    model_type VARCHAR(50) NOT NULL CHECK (
        model_type IN (
            'classification', 
            'regression', 
            'clustering', 
            'rules_engine', 
            'generative_llm'
        )
    ),
    
    -- Execution and orchestration parameters
    status VARCHAR(20) DEFAULT 'ACTIVE' CHECK (
        status IN ('ACTIVE', 'INACTIVE', 'TRAINING', 'DEPRECATED', 'FAILED')
    ),
    -- E.g., '0 0 * * *' for a daily midnight batch run
    schedule_definition VARCHAR(100), 
    
    -- Model lineage and configurations
    -- Tracks which profile attributes are fed into this model as training/inference features
    input_features TEXT[] DEFAULT ARRAY[]::TEXT[], 
    -- Stores dynamic model configurations, thresholds, or LLM prompts (e.g., LangGraph agent configs)
    hyperparameters JSONB DEFAULT '{}'::jsonb,
    
    -- Audit fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

COMMENT ON TABLE customer360.cdp_scoring_models IS 'Central registry for all ML and rule-based models. Acts as the parent table for cdp_profile_attributes where is_scoring_model = true.';

-- ----------------------------------------------------------------------------
-- Foreign Key Enforcement
-- ----------------------------------------------------------------------------
-- This constraint ensures that any computed attribute claiming to be generated 
-- by a model actually references a valid model in the registry.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_cdp_pa_scoring_model'
          AND conrelid = 'customer360.cdp_profile_attributes'::regclass
    ) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I(%I) ON DELETE RESTRICT',
            'customer360',
            'cdp_profile_attributes',
            'fk_cdp_pa_scoring_model',
            'scoring_model_name',
            'customer360',
            'cdp_scoring_models',
            'scoring_model_name'
        );
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------
-- Optimizes queries filtering for active models in the admin UI
CREATE INDEX IF NOT EXISTS idx_cdp_scoring_models_status ON customer360.cdp_scoring_models (status);


-- ============================================================================
-- cdp_identity_index: flattened O(1) point-lookup index for identifiers
-- ============================================================================
-- Unified lookup table mapping (tenant_id, identifier_type, normalized value)
-- master_profile_id, avoiding JSONB/array scans on cdp_master_profiles
-- (external_ids/device_ids/cookie_ids/advertising_ids) during high-throughput
-- streaming CIR match resolution.
CREATE TABLE IF NOT EXISTS customer360.cdp_identity_index (
    identity_index_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),
    master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles (master_profile_id) ON DELETE CASCADE,

    -- Identifier classification, e.g. 'user_id', 'email', 'phone', 'device_id', 'cookie_id', 'advertising_id'.
    identifier_type VARCHAR(100) NOT NULL,
    identifier_value TEXT NOT NULL,
    -- Normalized / lowercased value used for exact-match lookups.
    identifier_value_normalized TEXT NOT NULL,

    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

    CONSTRAINT uq_cdp_identity_index UNIQUE (tenant_id, identifier_type, identifier_value_normalized)
);

COMMENT ON TABLE customer360.cdp_identity_index IS 'Flattened O(1) lookup table for cross-channel identifier matching during streaming ingestion, keyed by (tenant_id, identifier_type, identifier_value_normalized).';

CREATE INDEX IF NOT EXISTS idx_cdp_identity_lookup ON customer360.cdp_identity_index (tenant_id, identifier_type, identifier_value_normalized)
WHERE
    is_blocked = FALSE;

CREATE INDEX IF NOT EXISTS idx_cdp_identity_master ON customer360.cdp_identity_index (master_profile_id);


-- ==========================================================
-- Data Source / Connectors Table
-- ==========================================================
-- This table stores metadata, access tokens, and configurations 
-- for external data sources and ingestion connectors.
CREATE TABLE IF NOT EXISTS customer360.sys_data_source (
    data_source_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Multi-tenant isolation (mandatory for all sys/cdp tables)
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    
    -- Core identification
    name TEXT NOT NULL,
    slug VARCHAR(255) NOT NULL,
    
    -- Configuration status and type identifiers
    source_type SMALLINT NOT NULL DEFAULT 2, -- Maps to "type" (e.g., 2)
    status SMALLINT NOT NULL DEFAULT 1,      -- 1: active, 0: inactive
    
    -- Endpoints and URLs
    data_source_url TEXT,
    thumbnail_url TEXT,
    
    -- Data collection flags
    collect_directly BOOLEAN DEFAULT true,
    first_party_data BOOLEAN DEFAULT true,
    
    -- Journey mapping configuration
    journey_level SMALLINT DEFAULT 3,
    journey_map_id VARCHAR(255),
    touchpoint_hub_id VARCHAR(255),
    
    -- Security and volume metrics
    security_code TEXT,
    total_tracked_event BIGINT DEFAULT 0,
    avg_daily_event BIGINT DEFAULT 0,
    avg_events_per_profile NUMERIC(10, 2) DEFAULT 0.0,
    
    -- JSON and Array configurations
    -- Stores dynamic mapping like "1hgb91dmV1BhyoW9YMEKnb": "1148041_..."
    access_tokens JSONB DEFAULT '{}'::jsonb,
    -- List of allowed hosts for the connector
    data_source_hosts TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Stored JS tags for web tracking integration
    javascript_tags TEXT[] DEFAULT ARRAY[]::TEXT[],
    -- Landing page and tracking URLs for offline-to-online bridging
    qr_code_data JSONB DEFAULT '{}'::jsonb,
    
    -- Audit timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    
    -- Ensure slugs are unique per workspace/tenant
    CONSTRAINT uq_sys_data_source_slug UNIQUE (tenant_id, slug),

    -- Allowed source types:
    -- 1 Web JavaScript Code, 2 Data Connector API, 3 Data Webhook API,
    -- 4 S3 File Connector, 5 Mobile SDK Code
    CONSTRAINT ck_sys_data_source_source_type CHECK (source_type IN (1, 2, 3, 4, 5))
);

COMMENT ON TABLE customer360.sys_data_source IS 'Stores metadata and configuration for Data Sources/Connectors (e.g., access tokens, QR code data, webhook configs, journey routing) for data ingestion pipelines.';

-- ----------------------------------------------------------------------------
-- INDEXES & ROW LEVEL SECURITY
-- ----------------------------------------------------------------------------

-- Fast tenant-level lookup index
CREATE INDEX IF NOT EXISTS idx_sys_data_source_tenant ON customer360.sys_data_source(tenant_id);

-- Optimized index for filtering active data sources in the UI
CREATE INDEX IF NOT EXISTS idx_sys_data_source_status ON customer360.sys_data_source(tenant_id, status);

-- ============================================================================
-- cdp_profile_merge_history: audit trail of master-to-master profile merges
-- ============================================================================
-- Records every time one cdp_master_profiles row is merged/tombstoned into
-- another, storing full JSONB snapshots of both sides so a bad merge can be
-- unmerged/rolled back later.
CREATE TABLE IF NOT EXISTS customer360.cdp_profile_merge_history (
    merge_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant (tenant_id),

    target_master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles (master_profile_id), -- retained profile
    source_master_profile_id UUID NOT NULL, -- merged/tombstoned profile id (no FK: row no longer exists after merge)

    merge_reason TEXT NOT NULL, -- e.g. 'Deterministic email match', 'Manual admin merge'
    matched_identifier_type VARCHAR(100),
    matched_identifier_value TEXT,
    match_score NUMERIC(5, 4),

    source_profile_snapshot JSONB NOT NULL,
    target_profile_snapshot JSONB NOT NULL,

    merged_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    merged_by UUID REFERENCES customer360.sys_user (user_id) -- NULL if automated system process
);

COMMENT ON TABLE customer360.cdp_profile_merge_history IS 'Audit log of master-to-master profile merges storing JSONB profile snapshots to enable profile unmerging/splitting.';

CREATE INDEX IF NOT EXISTS idx_cdp_merge_history_target ON customer360.cdp_profile_merge_history (tenant_id, target_master_profile_id);

CREATE INDEX IF NOT EXISTS idx_cdp_merge_history_source ON customer360.cdp_profile_merge_history (tenant_id, source_master_profile_id);

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

    -- Nullable + no hard NOT NULL, matching the asynchronous identity-linking
    -- pattern used by the S3 event lake:
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

COMMENT ON TABLE customer360.crm_transactions IS 'Source-agnostic transaction fact (retail purchase, banking transfer, travel booking, ...). master_profile_id is nullable and backfilled asynchronously by CIR, matching the S3 event lake pattern, so ingestion is never blocked waiting for identity resolution.';

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
    -- Validated against sys_domain ('all' + active domain codes) at the API
    -- layer, see cdp_master_profiles.domain above.
    domain TEXT NOT NULL DEFAULT 'all',
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

CREATE INDEX IF NOT EXISTS idx_cdp_content_items_domain_type ON customer360.cdp_content_items (domain, item_type);
CREATE INDEX IF NOT EXISTS idx_cdp_content_items_tags ON customer360.cdp_content_items USING GIN (segment_tags);

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
    -- Validated against sys_domain ('all' + active domain codes) at the API
    -- layer, see cdp_master_profiles.domain above.
    domain TEXT NOT NULL DEFAULT 'all',

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

CREATE INDEX IF NOT EXISTS idx_cdp_segments_tenant ON customer360.cdp_segments (tenant_id);
CREATE INDEX IF NOT EXISTS idx_cdp_segments_json_rules ON customer360.cdp_segments USING GIN (json_rules);

-- ============================================================================
-- AI Campaign Draft Review (specs/002-ai-campaign-draft-creation)
-- ============================================================================
-- Records reviewer approve/reject decisions on a campaign draft. The
-- crm_campaign segment/template/approval_status columns and the
-- crm_campaign_content_items relation table are defined later in this file
-- (Agentic CRM Messaging Schema section), after crm_message_templates exists.
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_reviews (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    reviewer_id UUID NOT NULL REFERENCES customer360.sys_user(user_id),
    decision VARCHAR(10) NOT NULL CHECK (decision IN ('approve', 'reject')),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE customer360.crm_campaign_reviews IS 'One row per reviewer approve/reject decision on a campaign draft -- approval_status may only ever change via a row inserted here or the automatic edit-after-approval/-rejection reversion in campaign_draft_repository (FR-010).';

CREATE INDEX IF NOT EXISTS idx_crm_campaign_reviews_campaign ON customer360.crm_campaign_reviews (campaign_id);

---------------------------------------------------
-- GRAPH EDGES (Partitioned by Relation)
---------------------------------------------------

-- Parent
CREATE TABLE IF NOT EXISTS customer360.graph_edges (
    edge_id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
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
CREATE TABLE IF NOT EXISTS customer360.graph_edges_belongs_to PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('belongs_to');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_comes_from PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('comes_from');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_converted PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('converted');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_follows PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('follows');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_is_part_of PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_part_of');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_is_active_as PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_active_as');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_is_connected_to PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_connected_to');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_is_from PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_from');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_created_by PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('created_by');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_is_driven_by PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_driven_by');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_has_role PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('has_role');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_has PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('has');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_is_for_the PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('is_for_the');

CREATE TABLE IF NOT EXISTS customer360.graph_edges_belongs_to_industry PARTITION OF customer360.graph_edges FOR
VALUES
    IN ('belongs_to_industry');

-- Catch-all
CREATE TABLE IF NOT EXISTS customer360.graph_edges_other PARTITION OF customer360.graph_edges DEFAULT;

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
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_mp_tenant_email ON customer360.cdp_master_profiles (tenant_id, email)
WHERE
    email IS NOT NULL;

-- Phone is unique per workspace. Ignored if NULL (e.g., web-only users).
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_mp_tenant_phone ON customer360.cdp_master_profiles (tenant_id, phone_number)
WHERE
    phone_number IS NOT NULL;

-- Core banking/retail identifiers are now domain-scoped in
-- cdp_domain_profiles.domain_attributes and should be indexed/looked up via
-- cdp_identity_index for normalized, cross-source matching.

-- -------------------------------------------------------------------------
-- 2. ML, SCORING & SEGMENTATION INDEXES (B-TREE)
-- Improved by leading with tenant_id. Since segmentation queries always
-- happen within a specific tenant, this massively speeds up campaign lookups.
-- -------------------------------------------------------------------------

-- Fast retrieval for churn prevention campaigns (Partial index saves space)
CREATE INDEX IF NOT EXISTS idx_cdp_mp_churn_tier ON customer360.cdp_master_profiles (tenant_id, churn_risk_tier)
WHERE
    churn_risk_tier IN ('high', 'critical');

-- Fast retrieval for high-value customer targeting (Whales)
CREATE INDEX IF NOT EXISTS idx_cdp_mp_pred_clv ON customer360.cdp_master_profiles (
    tenant_id,
    predictive_clv DESC NULLS LAST
);

-- Fast routing of high-probability leads to sales/CRM
CREATE INDEX IF NOT EXISTS idx_cdp_mp_lead_prob ON customer360.cdp_master_profiles (
    tenant_id,
    lead_conversion_probability DESC NULLS LAST
);

-- Analytics lookup for profiles needing data enrichment
CREATE INDEX IF NOT EXISTS idx_cdp_mp_data_quality ON customer360.cdp_master_profiles (
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

-- Deterministic external IDs (e.g., {"adjust_id": "...", "ga_client_id": "..."})
CREATE INDEX IF NOT EXISTS idx_cdp_mp_external_ids ON customer360.cdp_master_profiles USING GIN (external_ids);

-- Secondary contacts
CREATE INDEX IF NOT EXISTS idx_cdp_mp_sec_emails ON customer360.cdp_master_profiles USING GIN (secondary_emails);

CREATE INDEX IF NOT EXISTS idx_cdp_mp_sec_phones ON customer360.cdp_master_profiles USING GIN (secondary_phones);

-- Device & Ad Graph (Arrays)
CREATE INDEX IF NOT EXISTS idx_cdp_mp_device_ids ON customer360.cdp_master_profiles USING GIN (device_ids);

CREATE INDEX IF NOT EXISTS idx_cdp_mp_advertising_ids ON customer360.cdp_master_profiles USING GIN (advertising_ids);

CREATE INDEX IF NOT EXISTS idx_cdp_mp_cookie_ids ON customer360.cdp_master_profiles USING GIN (cookie_ids);

-- Trigram index backing the fuzzy_trgm matching_rule's similarity(full_name, ...)
-- lookup in resolver.py -- without it, enabling fuzzy_trgm on full_name would
-- force a sequential scan (computing similarity() per row) at any real scale.
CREATE INDEX IF NOT EXISTS idx_cdp_mp_full_name_trgm ON customer360.cdp_master_profiles USING GIN (full_name gin_trgm_ops);

-- Raw staging indexes: identity fields used for matching, plus the
-- processing-queue lookup (tenant_id, status_code).
CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_tenant_status ON customer360.cdp_raw_profiles_stage (tenant_id, status_code);

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_email ON customer360.cdp_raw_profiles_stage (email)
WHERE
    email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_phone ON customer360.cdp_raw_profiles_stage (phone_number)
WHERE
    phone_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_external_customer_id ON customer360.cdp_raw_profiles_stage (external_customer_id)
WHERE
    external_customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_device_id ON customer360.cdp_raw_profiles_stage (device_id)
WHERE
    device_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_advertising_id ON customer360.cdp_raw_profiles_stage (advertising_id)
WHERE
    advertising_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_cookie_id ON customer360.cdp_raw_profiles_stage (cookie_id)
WHERE
    cookie_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_national_id ON customer360.cdp_raw_profiles_stage (national_id)
WHERE
    national_id IS NOT NULL;

-- Granular Adjust device identifiers (fallback lookups / lineage; not
-- active CIR matching keys -- see device_id/advertising_id above for those).
CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_idfa ON customer360.cdp_raw_profiles_stage (idfa)
WHERE
    idfa IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_idfv ON customer360.cdp_raw_profiles_stage (idfv)
WHERE
    idfv IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_android_id ON customer360.cdp_raw_profiles_stage (android_id)
WHERE
    android_id IS NOT NULL;

-- Attribution reporting: rollups of installs/events by campaign.
CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_campaign_id ON customer360.cdp_raw_profiles_stage (tenant_id, media_source, campaign_id)
WHERE
    campaign_id IS NOT NULL;

-- Trigram indexes for fuzzy matching on name/company/address during CIR
CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_first_name_trgm ON customer360.cdp_raw_profiles_stage USING GIN (first_name gin_trgm_ops)
WHERE
    first_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_last_name_trgm ON customer360.cdp_raw_profiles_stage USING GIN (last_name gin_trgm_ops)
WHERE
    last_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_company_name_trgm ON customer360.cdp_raw_profiles_stage USING GIN (company_name gin_trgm_ops)
WHERE
    company_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_address_trgm ON customer360.cdp_raw_profiles_stage USING GIN (address_line1 gin_trgm_ops)
WHERE
    address_line1 IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_profiles_stage_city ON customer360.cdp_raw_profiles_stage (city)
WHERE
    city IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_date ON customer360.crm_customer_contacts (contact_date);

-- crm_transactions indexes: tenant timeline, resolved-profile timeline, generic
-- entity lookups, and idempotent re-ingestion protection.
CREATE INDEX IF NOT EXISTS idx_crm_transactions_tenant_time ON customer360.crm_transactions (
    tenant_id,
    transaction_time DESC
);

CREATE INDEX IF NOT EXISTS idx_crm_transactions_master_profile ON customer360.crm_transactions (
    master_profile_id,
    transaction_time DESC
)
WHERE
    master_profile_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_crm_transactions_entity ON customer360.crm_transactions (entity_type, entity_id)
WHERE
    entity_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_transactions_tenant_source ON customer360.crm_transactions (
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

CREATE INDEX IF NOT EXISTS idx_graph_edges_belongs_to_industry_created_at ON customer360.graph_edges_belongs_to_industry (created_at);

-- ==========================================================
-- Agentic Email Marketing Schema
-- ==========================================================
-- Relational structure behind the outbound CRM messaging flow
-- (segment -> CRM sync -> AI message template draft -> AI campaign draft -> human
-- approval -> dispatch). Placed after all referenced tables (sys_user,
-- crm_campaign, crm_lead, crm_lead_source, cdp_segments, cdp_content_items)
-- so foreign keys resolve. See docs/action-plans/AGENTIC-EMAIL-MARKETING-FLOW.md.

-- Reusable message template library for email, SMS, and WhatsApp, authored by
-- people or AI agents and gated by human review.
CREATE TABLE IF NOT EXISTS customer360.crm_message_templates (
    template_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    name TEXT NOT NULL,
    -- Platform key, intentionally open-ended for future messaging channels
    -- (e.g. email, web_chat, zalo_oa, whatsapp, telegram).
    message_type TEXT NOT NULL DEFAULT 'EMAIL',
    -- Shared persona archetype used by an AI agent when generating or revising
    -- this template. Hand-authored templates may leave it unset.
    persona_id UUID REFERENCES customer360.cdp_persona_archetypes(persona_archetype_id) ON DELETE SET NULL,
    -- Structured generation context, such as product, offer, tone, or locale.
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Channel-neutral body used by SMS/WhatsApp and as the plain-text email body.
    message_body TEXT,
    subject TEXT,
    html_body TEXT,
    -- Retained for compatibility with existing email rendering integrations.
    text_body TEXT,
    -- Declared placeholders/merge variables (e.g. unsubscribe_url, first_name).
    variables JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Draft -> InReview -> Approved / Rejected review lifecycle.
    status VARCHAR(50) NOT NULL DEFAULT 'Draft',
    created_by UUID REFERENCES customer360.sys_user(user_id),
    approved_by UUID REFERENCES customer360.sys_user(user_id),
    approved_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_crm_message_templates_message_type_not_blank
        CHECK (btrim(message_type) <> ''),
    CONSTRAINT chk_crm_message_templates_context_object
        CHECK (jsonb_typeof(context) = 'object'),
    CONSTRAINT chk_crm_message_templates_variables_object
        CHECK (jsonb_typeof(variables) = 'object'),
    CONSTRAINT chk_crm_message_templates_status
        CHECK (status IN ('Draft', 'InReview', 'Approved', 'Rejected'))
);

COMMENT ON TABLE customer360.crm_message_templates IS 'Reusable CRM message templates for EMAIL, SMS, and WHATSAPP. Stores an optional AI persona reference, structured generation context, merge variables, channel-neutral content, and email-specific subject/HTML content through the Draft -> InReview -> Approved/Rejected lifecycle.';

CREATE INDEX IF NOT EXISTS idx_crm_message_templates_tenant ON customer360.crm_message_templates (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_message_templates_tenant_status ON customer360.crm_message_templates (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_crm_message_templates_tenant_type ON customer360.crm_message_templates (tenant_id, message_type);
CREATE INDEX IF NOT EXISTS idx_crm_message_templates_persona ON customer360.crm_message_templates (persona_id)
    WHERE persona_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_campaign_segment' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I(%I) ON DELETE SET NULL',
            'customer360', 'crm_campaign', 'fk_crm_campaign_segment',
            'segment_id', 'customer360', 'cdp_segments', 'segment_id'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_campaign_template' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I(%I) ON DELETE SET NULL',
            'customer360', 'crm_campaign', 'fk_crm_campaign_template',
            'template_id', 'customer360', 'crm_message_templates', 'template_id'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_campaign_approved_by' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I(%I)',
            'customer360', 'crm_campaign', 'fk_crm_campaign_approved_by',
            'approved_by', 'customer360', 'sys_user', 'user_id'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_crm_campaign_approval_status' AND conrelid = 'customer360.crm_campaign'::regclass) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I CHECK (approval_status IN (''Draft'', ''InReview'', ''Approved'', ''Rejected''))',
            'customer360', 'crm_campaign', 'chk_crm_campaign_approval_status'
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_crm_campaign_segment ON customer360.crm_campaign (segment_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_template ON customer360.crm_campaign (template_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_crm_lead_lead_source' AND conrelid = 'customer360.crm_lead'::regclass) THEN
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I.%I(%I) ON DELETE SET NULL',
            'customer360', 'crm_lead', 'fk_crm_lead_lead_source',
            'lead_source_id', 'customer360', 'crm_lead_source', 'lead_source_id'
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_crm_lead_lead_source ON customer360.crm_lead (lead_source_id);

-- Campaign <-> content-item relation: which cdp_content_items a campaign uses,
-- in what order (position) and role (e.g. hero, body, footer).
CREATE TABLE IF NOT EXISTS customer360.crm_campaign_content_items (
    campaign_content_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    content_item_id UUID NOT NULL REFERENCES customer360.cdp_content_items(content_item_id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    role VARCHAR(50),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    -- A content item appears at most once per campaign (idempotent linking).
    CONSTRAINT uq_crm_campaign_content UNIQUE (campaign_id, content_item_id)
);

COMMENT ON TABLE customer360.crm_campaign_content_items IS 'Relation table linking a crm_campaign to the cdp_content_items it uses, with ordering (position) and role; unique per (campaign_id, content_item_id) so re-planning a campaign cannot duplicate content links.';

CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_tenant ON customer360.crm_campaign_content_items (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_campaign ON customer360.crm_campaign_content_items (campaign_id);
CREATE INDEX IF NOT EXISTS idx_crm_campaign_content_item ON customer360.crm_campaign_content_items (content_item_id);

-- Audit trail: one row per segment-sync execution with per-routing-bucket
-- counts (customer / lead / contact), for idempotency evidence and dashboards.
CREATE TABLE IF NOT EXISTS customer360.crm_segment_sync_runs (
    sync_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    segment_id UUID NOT NULL REFERENCES customer360.cdp_segments(segment_id) ON DELETE CASCADE,
    triggered_by UUID REFERENCES customer360.sys_user(user_id),
    status VARCHAR(50) NOT NULL DEFAULT 'Pending',
    -- dry_run = true means counts were computed but no crm_* rows were written.
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    -- Frozen membership size for this run, then per routing bucket.
    matched_count INTEGER NOT NULL DEFAULT 0,
    customer_count INTEGER NOT NULL DEFAULT 0,
    lead_count INTEGER NOT NULL DEFAULT 0,
    contact_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    finished_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB,
    CONSTRAINT chk_crm_segment_sync_runs_status
        CHECK (status IN ('Pending', 'Running', 'Completed', 'Failed'))
);

COMMENT ON TABLE customer360.crm_segment_sync_runs IS 'Audit record for each segment -> CRM sync execution: routing-bucket counts (customer/lead/contact), skipped/error counts, dry-run flag, and timing, produced by the sync engine.';

CREATE INDEX IF NOT EXISTS idx_crm_segment_sync_runs_tenant ON customer360.crm_segment_sync_runs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_segment_sync_runs_segment ON customer360.crm_segment_sync_runs (segment_id);
CREATE INDEX IF NOT EXISTS idx_crm_segment_sync_runs_tenant_started ON customer360.crm_segment_sync_runs (tenant_id, started_at DESC);

-- --- Per-recipient email send ledger --------------------------
CREATE TABLE IF NOT EXISTS customer360.cdp_campaign_dispatch_logs (
    dispatch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    campaign_id UUID NOT NULL REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles(master_profile_id) ON DELETE CASCADE,
    template_id UUID REFERENCES customer360.crm_message_templates(template_id) ON DELETE SET NULL,
    recipient_email TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'Pending',
    provider VARCHAR(100),
    provider_message_id TEXT,
    rendered_subject TEXT,
    error_message TEXT,
    run_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    dispatched_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_cdp_campaign_dispatch_status
        CHECK (status IN ('Pending', 'Sent', 'Failed', 'Skipped', 'Suppressed')),
    CONSTRAINT uq_cdp_campaign_dispatch_recipient UNIQUE (campaign_id, master_profile_id)
);

COMMENT ON TABLE customer360.cdp_campaign_dispatch_logs IS 'Per-recipient email send ledger written by the email_engine Dagster job: one row per (campaign_id, master_profile_id) with send status, provider message id, and rendered subject. UNIQUE(campaign_id, master_profile_id) makes re-runs idempotent (ON CONFLICT DO UPDATE).';

CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_tenant ON customer360.cdp_campaign_dispatch_logs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_campaign ON customer360.cdp_campaign_dispatch_logs (campaign_id);
CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_campaign_status ON customer360.cdp_campaign_dispatch_logs (campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_cdp_campaign_dispatch_profile ON customer360.cdp_campaign_dispatch_logs (master_profile_id);

-- --- Generic outbound activation connector config -----------------
CREATE TABLE IF NOT EXISTS customer360.crm_connector_config (
    connector_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    user_id UUID REFERENCES customer360.sys_user(user_id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT 'default',
    connector_type VARCHAR(50) NOT NULL,
    provider VARCHAR(100) NOT NULL,
    direction VARCHAR(20) NOT NULL DEFAULT 'OUTBOUND',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    endpoint_url TEXT,
    region VARCHAR(100),
    auth_type VARCHAR(50),
    credentials_ref TEXT,
    credentials JSONB NOT NULL DEFAULT '{}'::jsonb,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    capabilities JSONB DEFAULT '{}'::jsonb,
    last_tested_at TIMESTAMP WITH TIME ZONE,
    last_test_status VARCHAR(20),
    last_error TEXT,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_crm_connector_name UNIQUE (tenant_id, name),
    CONSTRAINT chk_crm_connector_type CHECK (connector_type IN ('EMAIL', 'SMS', 'PUSH', 'CHAT', 'ADS', 'WEBHOOK')),
    CONSTRAINT chk_crm_connector_direction CHECK (direction IN ('INBOUND', 'OUTBOUND', 'BIDIRECTIONAL')),
    CONSTRAINT chk_crm_connector_status CHECK (status IN ('ACTIVE', 'INACTIVE', 'ERROR', 'TESTING')),
    CONSTRAINT chk_crm_connector_last_test_status CHECK (
        last_test_status IS NULL OR last_test_status IN ('SUCCESS', 'FAILED')
    ),
    CONSTRAINT chk_crm_connector_credentials_object CHECK (jsonb_typeof(credentials) = 'object'),
    CONSTRAINT chk_crm_connector_config_object CHECK (jsonb_typeof(config) = 'object'),
    CONSTRAINT chk_crm_connector_capabilities_object CHECK (
        capabilities IS NULL OR jsonb_typeof(capabilities) = 'object'
    )
);

COMMENT ON TABLE customer360.crm_connector_config IS 'Outbound CRM connector configuration for activation and communication channels such as email, SMS, push, chat, ads and webhooks. Inbound data collection remains modeled by sys_data_source.';
COMMENT ON COLUMN customer360.crm_connector_config.credentials IS 'Secret connector material. For CHAT/ZALO this contains app_id, app_secret, oa_id, access_token, refresh_token, token_expires_at, and webhook_signing_secret.';
COMMENT ON COLUMN customer360.crm_connector_config.config IS 'Non-secret connector settings. For CHAT/ZALO this contains oa_api_base_url, oauth_authorize_url, oa_token_url, oauth_redirect_uri, token_refresh_cron, dispatch_adapter, zns_api_base_url, batch_size, optout_projection_cron, and optout_lookback_hours.';

CREATE INDEX IF NOT EXISTS idx_crm_connector_tenant ON customer360.crm_connector_config (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crm_connector_channel ON customer360.crm_connector_config (tenant_id, connector_type);
CREATE INDEX IF NOT EXISTS idx_crm_connector_status ON customer360.crm_connector_config (tenant_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_connector_default
    ON customer360.crm_connector_config (tenant_id, connector_type)
    WHERE is_default = TRUE AND is_active = TRUE;

-- --- Omnichannel CRM suppression list --------------------------
CREATE TABLE IF NOT EXISTS customer360.crm_suppression_list (
    suppression_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id) ON DELETE CASCADE,
    master_profile_id UUID REFERENCES customer360.cdp_master_profiles(master_profile_id) ON DELETE SET NULL,
    channel VARCHAR(30) NOT NULL,
    identifier_type VARCHAR(30) NOT NULL,
    identifier TEXT NOT NULL,
    reason VARCHAR(50) NOT NULL,
    scope VARCHAR(20) NOT NULL DEFAULT 'GLOBAL',
    campaign_id UUID REFERENCES customer360.crm_campaign(campaign_id) ON DELETE CASCADE,
    source VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    expires_at TIMESTAMP WITH TIME ZONE,
    removed_at TIMESTAMP WITH TIME ZONE,
    removed_by UUID REFERENCES customer360.sys_user(user_id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT chk_crm_suppression_list_channel CHECK (
        channel IN ('EMAIL', 'SMS', 'PUSH', 'WHATSAPP', 'ZALO', 'VOICE', 'GLOBAL')
    ),
    CONSTRAINT chk_crm_suppression_list_identifier_type CHECK (
        identifier_type IN ('EMAIL', 'PHONE', 'PUSH_TOKEN', 'DEVICE_ID', 'PROFILE_ID')
    ),
    CONSTRAINT chk_crm_suppression_list_reason CHECK (
        reason IN (
            'HARD_BOUNCE', 'SOFT_BOUNCE', 'COMPLAINT', 'UNSUBSCRIBE', 'MANUAL',
            'INVALID_DESTINATION', 'POLICY', 'LEGAL'
        )
    ),
    CONSTRAINT chk_crm_suppression_list_scope CHECK (scope IN ('GLOBAL', 'CAMPAIGN')),
    CONSTRAINT chk_crm_suppression_list_status CHECK (status IN ('ACTIVE', 'REMOVED')),
    CONSTRAINT chk_crm_suppression_list_campaign_scope CHECK (
        (scope = 'GLOBAL' AND campaign_id IS NULL)
        OR (scope = 'CAMPAIGN' AND campaign_id IS NOT NULL)
    ),
    CONSTRAINT chk_crm_suppression_list_removed CHECK (
        (status = 'ACTIVE' AND removed_at IS NULL)
        OR (status = 'REMOVED' AND removed_at IS NOT NULL)
    ),
    CONSTRAINT chk_crm_suppression_list_identifier_not_blank CHECK (btrim(identifier) <> ''),
    CONSTRAINT chk_crm_suppression_list_email_lower CHECK (
        identifier_type <> 'EMAIL' OR identifier = lower(identifier)
    ),
    CONSTRAINT chk_crm_suppression_list_profile_identifier CHECK (
        (identifier_type = 'PROFILE_ID' AND channel = 'GLOBAL')
        OR (identifier_type <> 'PROFILE_ID' AND channel <> 'GLOBAL')
    ),
    CONSTRAINT chk_crm_suppression_list_metadata CHECK (jsonb_typeof(metadata) = 'object')
);

COMMENT ON TABLE customer360.crm_suppression_list IS 'Omnichannel CRM suppression registry used by activation services before message dispatch. Supports global and campaign-scoped suppression across Email, SMS, Push, WhatsApp, Zalo, Voice and profile-level suppression.';
COMMENT ON COLUMN customer360.crm_suppression_list.identifier IS 'Canonical normalized destination identifier. Email must be lowercase; phone numbers should use E.164; provider/device identifiers must use the canonical representation defined by the connector.';
COMMENT ON COLUMN customer360.crm_suppression_list.scope IS 'GLOBAL blocks activation across campaigns; CAMPAIGN blocks activation only for campaign_id.';

CREATE INDEX IF NOT EXISTS idx_crm_suppression_tenant_status
    ON customer360.crm_suppression_list (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_crm_suppression_lookup
    ON customer360.crm_suppression_list (
        tenant_id, channel, identifier_type, identifier, status, expires_at
    );
CREATE INDEX IF NOT EXISTS idx_crm_suppression_profile
    ON customer360.crm_suppression_list (tenant_id, master_profile_id)
    WHERE master_profile_id IS NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'crm_suppression_list'
          AND c.conname = 'ex_crm_suppression_global_window'
    ) THEN
        ALTER TABLE customer360.crm_suppression_list
            ADD CONSTRAINT ex_crm_suppression_global_window
            EXCLUDE USING gist (
                tenant_id WITH =,
                channel WITH =,
                identifier_type WITH =,
                identifier WITH =,
                tstzrange(
                    created_at,
                    COALESCE(expires_at, 'infinity'::timestamptz),
                    '[)'
                ) WITH &&
            )
            WHERE (status = 'ACTIVE' AND scope = 'GLOBAL');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'customer360'
          AND r.relname = 'crm_suppression_list'
          AND c.conname = 'ex_crm_suppression_campaign_window'
    ) THEN
        ALTER TABLE customer360.crm_suppression_list
            ADD CONSTRAINT ex_crm_suppression_campaign_window
            EXCLUDE USING gist (
                tenant_id WITH =,
                channel WITH =,
                identifier_type WITH =,
                identifier WITH =,
                campaign_id WITH =,
                tstzrange(
                    created_at,
                    COALESCE(expires_at, 'infinity'::timestamptz),
                    '[)'
                ) WITH &&
            )
            WHERE (status = 'ACTIVE' AND scope = 'CAMPAIGN');
    END IF;
END;
$$;

-- ==========================================================
-- Tenant-consistent foreign keys
-- ==========================================================
-- Every relationship between tenant-owned tables must carry the child tenant
-- alongside the referenced ID. The single-column FKs above remain for
-- compatibility with existing clients; these composite FKs are the boundary
-- that prevents a row from tenant A referencing an object owned by tenant B.
CREATE UNIQUE INDEX IF NOT EXISTS ux_sys_organization_tenant_id
    ON customer360.sys_organization (tenant_id, organization_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sys_user_tenant_id
    ON customer360.sys_user (tenant_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sys_role_tenant_id
    ON customer360.sys_role (tenant_id, role_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_campaign_tenant_id
    ON customer360.crm_campaign (tenant_id, campaign_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_lead_source_tenant_id
    ON customer360.crm_lead_source (tenant_id, lead_source_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_contact_tenant_id
    ON customer360.crm_contact (tenant_id, contact_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_account_tenant_id
    ON customer360.crm_account (tenant_id, account_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_industry_tenant_id
    ON customer360.crm_industry (tenant_id, industry_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_master_profiles_tenant_id
    ON customer360.cdp_master_profiles (tenant_id, master_profile_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_raw_profiles_stage_tenant_id
    ON customer360.cdp_raw_profiles_stage (tenant_id, raw_profile_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_persona_archetypes_tenant_id
    ON customer360.cdp_persona_archetypes (tenant_id, persona_archetype_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_customer_personas_tenant_id
    ON customer360.cdp_customer_personas (tenant_id, persona_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_segments_tenant_id
    ON customer360.cdp_segments (tenant_id, segment_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cdp_content_items_tenant_id
    ON customer360.cdp_content_items (tenant_id, content_item_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_message_templates_tenant_id
    ON customer360.crm_message_templates (tenant_id, template_id);

DO $$
DECLARE
    fk RECORD;
BEGIN
    FOR fk IN
        SELECT *
        FROM jsonb_to_recordset($tenant_fk$
        [
            {"child_table":"sys_user","constraint_name":"fk_sys_user_tenant_organization","child_columns":"tenant_id, organization_id","parent_table":"sys_organization","parent_columns":"tenant_id, organization_id","on_delete":"SET NULL (organization_id)"},
            {"child_table":"sys_userinfo","constraint_name":"fk_sys_userinfo_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"CASCADE"},
            {"child_table":"sys_user_role","constraint_name":"fk_sys_user_role_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"CASCADE"},
            {"child_table":"sys_user_role","constraint_name":"fk_sys_user_role_tenant_role","child_columns":"tenant_id, role_id","parent_table":"sys_role","parent_columns":"tenant_id, role_id","on_delete":"CASCADE"},
            {"child_table":"sys_audit_log","constraint_name":"fk_sys_audit_log_tenant_organization","child_columns":"tenant_id, organization_id","parent_table":"sys_organization","parent_columns":"tenant_id, organization_id","on_delete":"SET NULL (organization_id)"},
            {"child_table":"sys_audit_log","constraint_name":"fk_sys_audit_log_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_campaign_performance_daily","constraint_name":"fk_crm_campaign_perf_tenant_campaign","child_columns":"tenant_id, campaign_id","parent_table":"crm_campaign","parent_columns":"tenant_id, campaign_id","on_delete":"CASCADE"},
            {"child_table":"crm_campaign_member","constraint_name":"fk_crm_campaign_member_tenant_campaign","child_columns":"tenant_id, campaign_id","parent_table":"crm_campaign","parent_columns":"tenant_id, campaign_id","on_delete":"CASCADE"},
            {"child_table":"crm_campaign_member","constraint_name":"fk_crm_campaign_member_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_campaign_member","constraint_name":"fk_crm_campaign_member_tenant_contact","child_columns":"tenant_id, contact_id","parent_table":"crm_contact","parent_columns":"tenant_id, contact_id","on_delete":"SET NULL (contact_id)"},
            {"child_table":"crm_lead","constraint_name":"fk_crm_lead_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_lead","constraint_name":"fk_crm_lead_tenant_source","child_columns":"tenant_id, lead_source_id","parent_table":"crm_lead_source","parent_columns":"tenant_id, lead_source_id","on_delete":"SET NULL (lead_source_id)"},
            {"child_table":"crm_contact","constraint_name":"fk_crm_contact_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_contact","constraint_name":"fk_crm_contact_tenant_account","child_columns":"tenant_id, account_id","parent_table":"crm_account","parent_columns":"tenant_id, account_id","on_delete":"SET NULL (account_id)"},
            {"child_table":"crm_account","constraint_name":"fk_crm_account_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_account","constraint_name":"fk_crm_account_tenant_industry","child_columns":"tenant_id, industry_id","parent_table":"crm_industry","parent_columns":"tenant_id, industry_id","on_delete":"SET NULL (industry_id)"},
            {"child_table":"crm_opportunity","constraint_name":"fk_crm_opportunity_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_opportunity","constraint_name":"fk_crm_opportunity_tenant_account","child_columns":"tenant_id, account_id","parent_table":"crm_account","parent_columns":"tenant_id, account_id","on_delete":"SET NULL (account_id)"},
            {"child_table":"crm_industry","constraint_name":"fk_crm_industry_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"cdp_master_profiles","constraint_name":"fk_cdp_master_profiles_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"cdp_master_profiles","constraint_name":"fk_cdp_master_profiles_tenant_persona","child_columns":"tenant_id, current_persona_id","parent_table":"cdp_customer_personas","parent_columns":"tenant_id, persona_id","on_delete":"SET NULL (current_persona_id)"},
            {"child_table":"cdp_domain_profiles","constraint_name":"fk_cdp_domain_profiles_tenant_master","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_raw_profiles_stage","constraint_name":"fk_cdp_raw_profiles_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"cdp_profile_links","constraint_name":"fk_cdp_profile_links_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"cdp_profile_links","constraint_name":"fk_cdp_profile_links_tenant_raw","child_columns":"tenant_id, raw_profile_id","parent_table":"cdp_raw_profiles_stage","parent_columns":"tenant_id, raw_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_profile_links","constraint_name":"fk_cdp_profile_links_tenant_master","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_profile_links","constraint_name":"fk_cdp_profile_links_tenant_unlinked_by","child_columns":"tenant_id, unlinked_by","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (unlinked_by)"},
            {"child_table":"cdp_identity_index","constraint_name":"fk_cdp_identity_index_tenant_master","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_profile_merge_history","constraint_name":"fk_cdp_merge_history_tenant_target","child_columns":"tenant_id, target_master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"RESTRICT"},
            {"child_table":"cdp_profile_merge_history","constraint_name":"fk_cdp_merge_history_tenant_user","child_columns":"tenant_id, merged_by","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (merged_by)"},
            {"child_table":"cdp_relations","constraint_name":"fk_cdp_relations_tenant_source","child_columns":"tenant_id, source_master_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_relations","constraint_name":"fk_cdp_relations_tenant_target","child_columns":"tenant_id, target_master_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_relations","constraint_name":"fk_cdp_relations_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_customer_contacts","constraint_name":"fk_crm_customer_contacts_tenant_master","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"crm_customer_contacts","constraint_name":"fk_crm_customer_contacts_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_transactions","constraint_name":"fk_crm_transactions_tenant_master","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"SET NULL (master_profile_id)"},
            {"child_table":"crm_transactions","constraint_name":"fk_crm_transactions_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"cdp_segments","constraint_name":"fk_cdp_segments_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_campaign","constraint_name":"fk_crm_campaign_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_campaign","constraint_name":"fk_crm_campaign_tenant_approved_by","child_columns":"tenant_id, approved_by","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (approved_by)"},
            {"child_table":"crm_campaign","constraint_name":"fk_crm_campaign_tenant_segment","child_columns":"tenant_id, segment_id","parent_table":"cdp_segments","parent_columns":"tenant_id, segment_id","on_delete":"SET NULL (segment_id)"},
            {"child_table":"crm_campaign","constraint_name":"fk_crm_campaign_tenant_template","child_columns":"tenant_id, template_id","parent_table":"crm_message_templates","parent_columns":"tenant_id, template_id","on_delete":"SET NULL (template_id)"},
            {"child_table":"crm_campaign_reviews","constraint_name":"fk_crm_campaign_reviews_tenant_campaign","child_columns":"tenant_id, campaign_id","parent_table":"crm_campaign","parent_columns":"tenant_id, campaign_id","on_delete":"CASCADE"},
            {"child_table":"crm_campaign_reviews","constraint_name":"fk_crm_campaign_reviews_tenant_user","child_columns":"tenant_id, reviewer_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"RESTRICT"},
            {"child_table":"crm_campaign_content_items","constraint_name":"fk_crm_campaign_content_tenant_campaign","child_columns":"tenant_id, campaign_id","parent_table":"crm_campaign","parent_columns":"tenant_id, campaign_id","on_delete":"CASCADE"},
            {"child_table":"crm_campaign_content_items","constraint_name":"fk_crm_campaign_content_tenant_item","child_columns":"tenant_id, content_item_id","parent_table":"cdp_content_items","parent_columns":"tenant_id, content_item_id","on_delete":"CASCADE"},
            {"child_table":"crm_segment_sync_runs","constraint_name":"fk_crm_segment_sync_tenant_segment","child_columns":"tenant_id, segment_id","parent_table":"cdp_segments","parent_columns":"tenant_id, segment_id","on_delete":"CASCADE"},
            {"child_table":"crm_segment_sync_runs","constraint_name":"fk_crm_segment_sync_tenant_user","child_columns":"tenant_id, triggered_by","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (triggered_by)"},
            {"child_table":"cdp_campaign_dispatch_logs","constraint_name":"fk_cdp_dispatch_tenant_campaign","child_columns":"tenant_id, campaign_id","parent_table":"crm_campaign","parent_columns":"tenant_id, campaign_id","on_delete":"CASCADE"},
            {"child_table":"cdp_campaign_dispatch_logs","constraint_name":"fk_cdp_dispatch_tenant_profile","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_campaign_dispatch_logs","constraint_name":"fk_cdp_dispatch_tenant_template","child_columns":"tenant_id, template_id","parent_table":"crm_message_templates","parent_columns":"tenant_id, template_id","on_delete":"SET NULL (template_id)"},
            {"child_table":"crm_message_templates","constraint_name":"fk_crm_message_templates_tenant_user","child_columns":"tenant_id, created_by","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (created_by)"},
            {"child_table":"crm_message_templates","constraint_name":"fk_crm_message_templates_tenant_approved_by","child_columns":"tenant_id, approved_by","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (approved_by)"},
            {"child_table":"crm_message_templates","constraint_name":"fk_crm_message_templates_tenant_persona","child_columns":"tenant_id, persona_id","parent_table":"cdp_persona_archetypes","parent_columns":"tenant_id, persona_archetype_id","on_delete":"SET NULL (persona_id)"},
            {"child_table":"crm_connector_config","constraint_name":"fk_crm_connector_tenant_user","child_columns":"tenant_id, user_id","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (user_id)"},
            {"child_table":"crm_suppression_list","constraint_name":"fk_crm_suppression_tenant_profile","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"SET NULL (master_profile_id)"},
            {"child_table":"crm_suppression_list","constraint_name":"fk_crm_suppression_tenant_campaign","child_columns":"tenant_id, campaign_id","parent_table":"crm_campaign","parent_columns":"tenant_id, campaign_id","on_delete":"CASCADE"},
            {"child_table":"crm_suppression_list","constraint_name":"fk_crm_suppression_tenant_user","child_columns":"tenant_id, removed_by","parent_table":"sys_user","parent_columns":"tenant_id, user_id","on_delete":"SET NULL (removed_by)"},
            {"child_table":"cdp_customer_personas","constraint_name":"fk_cdp_personas_tenant_master","child_columns":"tenant_id, master_profile_id","parent_table":"cdp_master_profiles","parent_columns":"tenant_id, master_profile_id","on_delete":"CASCADE"},
            {"child_table":"cdp_customer_personas","constraint_name":"fk_cdp_personas_tenant_archetype","child_columns":"tenant_id, persona_archetype_id","parent_table":"cdp_persona_archetypes","parent_columns":"tenant_id, persona_archetype_id","on_delete":"CASCADE"}
        ]$tenant_fk$::jsonb) AS item(
            child_table TEXT,
            constraint_name TEXT,
            child_columns TEXT,
            parent_table TEXT,
            parent_columns TEXT,
            on_delete TEXT
        )
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_class r ON r.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = r.relnamespace
            WHERE n.nspname = 'customer360'
              AND r.relname = fk.child_table
              AND a.attname = 'tenant_id'
              AND a.attnum > 0
              AND NOT a.attisdropped
        ) THEN
            CONTINUE;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class r ON r.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = r.relnamespace
            WHERE c.conname = fk.constraint_name
              AND n.nspname = 'customer360'
        ) THEN
            EXECUTE format(
                'ALTER TABLE customer360.%I ADD CONSTRAINT %I FOREIGN KEY (%s) REFERENCES customer360.%I (%s) ON DELETE %s',
                fk.child_table,
                fk.constraint_name,
                fk.child_columns,
                fk.parent_table,
                fk.parent_columns,
                fk.on_delete
            );
        END IF;
    END LOOP;
END;
$$;

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
        'sys_tenant_domain',
        'sys_user',
        'sys_userinfo',
        'sys_role',
        'sys_audit_log',
        'crm_campaign',
        'crm_campaign_performance_daily',
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
        'cdp_identity_index',
        'cdp_profile_merge_history',
        'cdp_relations',
        'cdp_domain_profiles',
        'cdp_segments',
        'cdp_content_items',
        'cdp_customer_personas',
        'cdp_persona_archetypes',
        'crm_message_templates',
        'crm_campaign_content_items',
        'crm_campaign_reviews',
        'crm_segment_sync_runs',
        'cdp_campaign_dispatch_logs',
        'crm_connector_config',
        'crm_suppression_list',
        'sys_data_source',
        'sys_user_role',
        'graph_edges'
    ];
BEGIN
    FOREACH t IN ARRAY tenant_tables LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_class r ON r.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = r.relnamespace
            WHERE n.nspname = 'customer360'
              AND r.relname = t
              AND a.attname = 'tenant_id'
              AND a.attnum > 0
              AND NOT a.attisdropped
        ) THEN
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY;', 'customer360', t);
        EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY;', 'customer360', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_policy ON customer360.%I;', t);
        EXECUTE format(
            'CREATE POLICY tenant_policy ON customer360.%I
                USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
                WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid);',
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
--     NULLIF(btrim(current_setting('app.tenant_id')), '')::uuid
-- );