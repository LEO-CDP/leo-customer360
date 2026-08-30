-- SQLBook: Code
-- =========================================================
-- Persona360 Platform
-- PostgreSQL 15+
-- PostGIS + pgvector
--
-- Platform boundary:
--   Persona360 owns human/persona identity, enrichment and provenance.
--   Customer/profile platforms consume this data through an API, export or
--   event contract. They do not share tables, tenant IDs or foreign keys with
--   Persona360. `source_system` and `external_id` are opaque integration keys.
--
-- Core flow:
--
--      DATA
--        ↓
--    IDENTITY
--        ↓
--     CONTEXT
--    ┌───┼───┐
--    ↓   ↓   ↓
--   WHO WHERE WHEN
--    └───┼───┘
--        ↓
--   EXPERIENCE
--        ↓
--      ACTION
--
-- Persona360 is an independent platform. LEO CDP is an optional experience
-- and enrichment consumer, not a database dependency of this schema.
-- =========================================================


-- =========================================================
-- Extensions
-- =========================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;

-- PostGIS for location / geography / spatial search
CREATE EXTENSION IF NOT EXISTS postgis;

-- Fuzzy matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;


-- =========================================================
-- Schema
-- =========================================================

CREATE SCHEMA IF NOT EXISTS persona360;


-- =========================================================
-- Utility Functions
-- =========================================================

CREATE OR REPLACE FUNCTION persona360.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


-- =========================================================
-- TENANT
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.sys_tenant (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_code VARCHAR(50) UNIQUE NOT NULL,
    tenant_name TEXT NOT NULL,
    company_name TEXT,
    business_type TEXT,

    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE persona360.sys_tenant IS
'Top-level Persona360 account/workspace. This tenant is owned by Persona360 and is intentionally independent from any consumer platform tenant.';


CREATE INDEX IF NOT EXISTS idx_sys_tenant_status
    ON persona360.sys_tenant(status);


DROP TRIGGER IF EXISTS trg_sys_tenant_updated_at
ON persona360.sys_tenant;

CREATE TRIGGER trg_sys_tenant_updated_at
BEFORE UPDATE ON persona360.sys_tenant
FOR EACH ROW
EXECUTE FUNCTION persona360.set_updated_at();


-- =========================================================
-- PERSONA
-- Core identity/entity of Persona360
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.personas (
    persona_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    -- historical_person / living_person / customer / end_user / public_figure
    -- other human persona
    persona_type VARCHAR(50) NOT NULL
        CHECK (persona_type IN (
            'HISTORICAL_PERSON',
            'LIVING_PERSON',
            'CUSTOMER',
            'END_USER',
            'PUBLIC_FIGURE',
            'OTHER'
        )),

    canonical_name TEXT NOT NULL
        CHECK (btrim(canonical_name) <> ''),

    display_name TEXT,

    given_name TEXT,
    middle_name TEXT,
    family_name TEXT,

    gender TEXT,
    nationality TEXT,

    birth_date DATE,
    death_date DATE,

    birth_place_id UUID,
    death_place_id UUID,

    -- Main persona description
    summary TEXT,

    -- Stable semantic profile
    persona_embedding vector(1536),

    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN (
            'ACTIVE',
            'INACTIVE',
            'ARCHIVED',
            'UNKNOWN'
        )),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_persona_dates
        CHECK (
            death_date IS NULL
            OR birth_date IS NULL
            OR death_date >= birth_date
        )
);

COMMENT ON TABLE persona360.personas IS
'Core human Persona360 entity. A persona represents a historical person, living person, customer, end user, public figure or other human profile.';

CREATE INDEX IF NOT EXISTS idx_personas_tenant
    ON persona360.personas(tenant_id);

CREATE INDEX IF NOT EXISTS idx_personas_type
    ON persona360.personas(tenant_id, persona_type);

CREATE INDEX IF NOT EXISTS idx_personas_name
    ON persona360.personas
    USING gin (canonical_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_personas_embedding
    ON persona360.personas
    USING hnsw (persona_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE persona_embedding IS NOT NULL
      AND status <> 'ARCHIVED';

CREATE INDEX IF NOT EXISTS idx_personas_tenant_name
    ON persona360.personas(tenant_id, canonical_name);

CREATE INDEX IF NOT EXISTS idx_personas_tenant_status_id
    ON persona360.personas(tenant_id, status, persona_id);


DROP TRIGGER IF EXISTS trg_personas_updated_at
ON persona360.personas;

CREATE TRIGGER trg_personas_updated_at
BEFORE UPDATE ON persona360.personas
FOR EACH ROW
EXECUTE FUNCTION persona360.set_updated_at();


-- =========================================================
-- PERSONA IDENTITIES
-- External identity resolution and platform integration boundary
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.persona_identities (
    identity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    source_system VARCHAR(100) NOT NULL
        CHECK (btrim(source_system) <> ''),

    identity_type VARCHAR(100) NOT NULL
        CHECK (btrim(identity_type) <> ''),

    external_id TEXT NOT NULL
        CHECK (btrim(external_id) <> ''),

    normalized_value TEXT,

    confidence NUMERIC(5,4),

    is_primary BOOLEAN NOT NULL DEFAULT FALSE,

    verified_at TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (
        tenant_id,
        source_system,
        identity_type,
        external_id
    ),

    CONSTRAINT chk_identity_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
        )
);

COMMENT ON TABLE persona360.persona_identities IS
'Opaque external identity keys used to resolve a human persona. source_system and external_id are integration data only; they do not reference a consumer platform table or tenant.';


CREATE INDEX IF NOT EXISTS idx_persona_identity_persona
    ON persona360.persona_identities(
        tenant_id,
        persona_id
    );

CREATE INDEX IF NOT EXISTS idx_persona_identity_lookup
    ON persona360.persona_identities(
        tenant_id,
        source_system,
        identity_type,
        external_id
    );


-- =========================================================
-- PERSONA ATTRIBUTES
-- Dynamic enrichment attributes
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.persona_attributes (
    attribute_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    attribute_key TEXT NOT NULL,

    attribute_value JSONB NOT NULL,

    source_type VARCHAR(50),

    confidence NUMERIC(5,4),

    observed_at TIMESTAMPTZ,

    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_attribute_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
        ),

    CONSTRAINT chk_attribute_dates
        CHECK (
            valid_to IS NULL
            OR valid_from IS NULL
            OR valid_to >= valid_from
        )
);

CREATE INDEX IF NOT EXISTS idx_persona_attributes_persona
    ON persona360.persona_attributes(
        tenant_id,
        persona_id
    );

CREATE INDEX IF NOT EXISTS idx_persona_attributes_key
    ON persona360.persona_attributes(
        tenant_id,
        attribute_key
    );

CREATE INDEX IF NOT EXISTS idx_persona_attributes_jsonb
    ON persona360.persona_attributes
    USING gin(attribute_value);


-- =========================================================
-- PERSONA BELIEFS / VIEWS
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.beliefs (
    belief_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    topic TEXT NOT NULL,

    belief_type VARCHAR(50)
        CHECK (belief_type IN (
            'SCIENTIFIC',
            'PHILOSOPHICAL',
            'RELIGIOUS',
            'POLITICAL',
            'ETHICAL',
            'SOCIAL',
            'PERSONAL',
            'OTHER'
        )),

    statement TEXT NOT NULL,

    confidence NUMERIC(5,4),

    -- historical validity of the belief
    valid_from DATE,
    valid_to DATE,

    source_document_id UUID,

    evidence_level VARCHAR(30)
        CHECK (evidence_level IN (
            'PRIMARY',
            'SECONDARY',
            'INFERRED',
            'TRADITION',
            'SPECULATIVE'
        )),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_belief_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
        )
);

CREATE INDEX IF NOT EXISTS idx_beliefs_persona
    ON persona360.beliefs(
        tenant_id,
        persona_id
    );

CREATE INDEX IF NOT EXISTS idx_beliefs_topic
    ON persona360.beliefs(
        tenant_id,
        topic
    );

CREATE INDEX IF NOT EXISTS idx_beliefs_validity
    ON persona360.beliefs(
        tenant_id,
        valid_from,
        valid_to
    );


-- =========================================================
-- PERSONA RELATIONSHIPS
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.persona_relationships (
    relationship_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    related_persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    relationship_type VARCHAR(100) NOT NULL,

    description TEXT,

    valid_from DATE,
    valid_to DATE,

    confidence NUMERIC(5,4),

    source_document_id UUID,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_relationship_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
        ),

    CONSTRAINT chk_relationship_dates
        CHECK (
            valid_to IS NULL
            OR valid_from IS NULL
            OR valid_to >= valid_from
        ),

    CONSTRAINT chk_relationship_self
        CHECK (persona_id <> related_persona_id)
);

CREATE INDEX IF NOT EXISTS idx_relationship_persona
    ON persona360.persona_relationships(
        tenant_id,
        persona_id
    );

CREATE INDEX IF NOT EXISTS idx_relationship_related
    ON persona360.persona_relationships(
        tenant_id,
        related_persona_id
    );


-- =========================================================
-- HISTORICAL PLACES
-- PostGIS = GEO context
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.historical_places (
    place_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    name TEXT NOT NULL,

    place_type VARCHAR(100),

    description TEXT,

    address TEXT,

    city TEXT,
    region TEXT,
    country TEXT,

    -- Current known location
    location GEOGRAPHY(Point, 4326),

    -- Optional historical geometry
    historical_geometry GEOGRAPHY(Geometry, 4326),

    valid_from DATE,
    valid_to DATE,

    source_document_id UUID,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_place_dates
        CHECK (
            valid_to IS NULL
            OR valid_from IS NULL
            OR valid_to >= valid_from
        )
);

CREATE INDEX IF NOT EXISTS idx_places_tenant
    ON persona360.historical_places(tenant_id);

CREATE INDEX IF NOT EXISTS idx_places_location
    ON persona360.historical_places
    USING gist(location);

CREATE INDEX IF NOT EXISTS idx_places_historical_geometry
    ON persona360.historical_places
    USING gist(historical_geometry);

CREATE INDEX IF NOT EXISTS idx_places_city_country
    ON persona360.historical_places(
        tenant_id,
        city,
        country
    );


DROP TRIGGER IF EXISTS trg_historical_places_updated_at
ON persona360.historical_places;

CREATE TRIGGER trg_historical_places_updated_at
BEFORE UPDATE ON persona360.historical_places
FOR EACH ROW
EXECUTE FUNCTION persona360.set_updated_at();


-- =========================================================
-- HISTORICAL EVENTS
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.historical_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    event_type VARCHAR(100),

    title TEXT NOT NULL,

    description TEXT,

    start_date DATE,
    end_date DATE,

    -- event date precision:
    -- DAY / MONTH / YEAR / APPROXIMATE / UNKNOWN
    temporal_precision VARCHAR(30) NOT NULL DEFAULT 'DAY'
        CHECK (temporal_precision IN (
            'DAY',
            'MONTH',
            'YEAR',
            'APPROXIMATE',
            'UNKNOWN'
        )),

    place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    source_document_id UUID,

    evidence_level VARCHAR(30)
        CHECK (evidence_level IN (
            'PRIMARY',
            'SECONDARY',
            'INFERRED',
            'TRADITION',
            'SPECULATIVE'
        )),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_event_dates
        CHECK (
            end_date IS NULL
            OR start_date IS NULL
            OR end_date >= start_date
        ),

    CONSTRAINT chk_event_title
        CHECK (btrim(title) <> '')
);

CREATE INDEX IF NOT EXISTS idx_events_tenant
    ON persona360.historical_events(tenant_id);

CREATE INDEX IF NOT EXISTS idx_events_dates
    ON persona360.historical_events(
        tenant_id,
        start_date,
        end_date
    );

CREATE INDEX IF NOT EXISTS idx_events_place
    ON persona360.historical_events(
        tenant_id,
        place_id
    );


DROP TRIGGER IF EXISTS trg_historical_events_updated_at
ON persona360.historical_events;

CREATE TRIGGER trg_historical_events_updated_at
BEFORE UPDATE ON persona360.historical_events
FOR EACH ROW
EXECUTE FUNCTION persona360.set_updated_at();


-- =========================================================
-- PERSONA EVENTS
-- Many-to-many:
-- Persona ↔ Historical Event
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.persona_events (
    persona_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    event_id UUID NOT NULL
        REFERENCES persona360.historical_events(event_id)
        ON DELETE CASCADE,

    role VARCHAR(100),

    significance TEXT,

    confidence NUMERIC(5,4),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE NULLS NOT DISTINCT (
        tenant_id,
        persona_id,
        event_id,
        role
    )
);

CREATE INDEX IF NOT EXISTS idx_persona_events_persona
    ON persona360.persona_events(
        tenant_id,
        persona_id
    );

CREATE INDEX IF NOT EXISTS idx_persona_events_event
    ON persona360.persona_events(
        tenant_id,
        event_id
    );


-- =========================================================
-- TIMELINES
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.timelines (
    timeline_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    title TEXT NOT NULL,

    description TEXT,

    start_date DATE,
    end_date DATE,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_timeline_dates
        CHECK (
            end_date IS NULL
            OR start_date IS NULL
            OR end_date >= start_date
        ),

    CONSTRAINT chk_timeline_title
        CHECK (btrim(title) <> '')
);

CREATE TABLE IF NOT EXISTS persona360.timeline_items (
    timeline_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    timeline_id UUID NOT NULL
        REFERENCES persona360.timelines(timeline_id)
        ON DELETE CASCADE,

    event_id UUID
        REFERENCES persona360.historical_events(event_id)
        ON DELETE SET NULL,

    place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    title TEXT NOT NULL,

    description TEXT,

    event_date DATE,

    sequence_no INTEGER
        CHECK (sequence_no IS NULL OR sequence_no >= 0),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_timeline_item_title
        CHECK (btrim(title) <> '')
);

CREATE INDEX IF NOT EXISTS idx_timelines_persona
    ON persona360.timelines(
        tenant_id,
        persona_id
    );

CREATE INDEX IF NOT EXISTS idx_timeline_items_timeline
    ON persona360.timeline_items(
        tenant_id,
        timeline_id,
        sequence_no
    );


-- =========================================================
-- DOCUMENTS
-- Historical source corpus
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.documents (
    document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    title TEXT NOT NULL
        CHECK (btrim(title) <> ''),

    document_type VARCHAR(100),

    language_code VARCHAR(20),

    author_persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    publication_date DATE,

    source_name TEXT,

    source_url TEXT,

    source_reference TEXT,

    -- PRIMARY / SECONDARY / TERTIARY
    source_level VARCHAR(30),

    evidence_level VARCHAR(30)
        CHECK (evidence_level IN (
            'PRIMARY',
            'SECONDARY',
            'TERTIARY',
            'TRADITION',
            'SPECULATIVE'
        )),

    content TEXT,

    checksum TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_persona
    ON persona360.documents(
        tenant_id,
        author_persona_id
    );

CREATE INDEX IF NOT EXISTS idx_documents_type
    ON persona360.documents(
        tenant_id,
        document_type
    );

CREATE INDEX IF NOT EXISTS idx_documents_publication_date
    ON persona360.documents(
        tenant_id,
        publication_date
    );


DROP TRIGGER IF EXISTS trg_documents_updated_at
ON persona360.documents;

CREATE TRIGGER trg_documents_updated_at
BEFORE UPDATE ON persona360.documents
FOR EACH ROW
EXECUTE FUNCTION persona360.set_updated_at();


-- =========================================================
-- DOCUMENT CHUNKS
-- RAG retrieval unit
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.document_chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    document_id UUID NOT NULL
        REFERENCES persona360.documents(document_id)
        ON DELETE CASCADE,

    chunk_index INTEGER NOT NULL
        CHECK (chunk_index >= 0),

    content TEXT NOT NULL,

    token_count INTEGER
        CHECK (token_count IS NULL OR token_count > 0),

    page_number INTEGER
        CHECK (page_number IS NULL OR page_number > 0),

    section_title TEXT,

    -- RAG embedding
    embedding vector(1536),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (
        document_id,
        chunk_index
    )
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_document
    ON persona360.document_chunks(
        tenant_id,
        document_id,
        chunk_index
    );

CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
    ON persona360.document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding IS NOT NULL;


-- =========================================================
-- QUOTES
-- High-value source-grounded statements
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.quotes (
    quote_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    document_id UUID
        REFERENCES persona360.documents(document_id)
        ON DELETE SET NULL,

    chunk_id UUID
        REFERENCES persona360.document_chunks(chunk_id)
        ON DELETE SET NULL,

    quote_text TEXT NOT NULL,

    language_code VARCHAR(20),

    quote_date DATE,

    topic TEXT,

    evidence_level VARCHAR(30)
        CHECK (evidence_level IN (
            'PRIMARY',
            'SECONDARY',
            'TRADITION',
            'SPECULATIVE'
        )),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quotes_persona
    ON persona360.quotes(
        tenant_id,
        persona_id
    );


-- =========================================================
-- GENERIC EMBEDDINGS
-- Allows semantic representations beyond documents
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.embeddings (
    embedding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    entity_type VARCHAR(100) NOT NULL,

    entity_id UUID NOT NULL,

    embedding_type VARCHAR(100) NOT NULL,

    model_name VARCHAR(200) NOT NULL,

    dimensions INTEGER NOT NULL
        CHECK (dimensions = 1536),

    embedding vector(1536) NOT NULL,

    content TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_embeddings_entity
    ON persona360.embeddings(
        tenant_id,
        entity_type,
        entity_id
    );

CREATE INDEX IF NOT EXISTS idx_embeddings_vector
    ON persona360.embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding IS NOT NULL;


-- =========================================================
-- CONTEXT SNAPSHOTS
--
-- Central LEO context object:
--
-- WHO   = subject_persona_id
-- WHERE = place_id / latitude / longitude
-- WHEN  = observed_at + historical_at
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.context_snapshots (
    context_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    subject_persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    target_persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    event_id UUID
        REFERENCES persona360.historical_events(event_id)
        ON DELETE SET NULL,

    timeline_id UUID
        REFERENCES persona360.timelines(timeline_id)
        ON DELETE SET NULL,

    -- Current user observation time
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Historical time being reconstructed / experienced
    historical_at DATE,

    -- User's current physical position
    current_location GEOGRAPHY(Point, 4326),

    -- Search radius in meters
    geo_radius_meters NUMERIC,

    device_context JSONB NOT NULL DEFAULT '{}'::jsonb,

    environment_context JSONB NOT NULL DEFAULT '{}'::jsonb,

    session_context JSONB NOT NULL DEFAULT '{}'::jsonb,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_context_subject
    ON persona360.context_snapshots(
        tenant_id,
        subject_persona_id,
        observed_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_context_target
    ON persona360.context_snapshots(
        tenant_id,
        target_persona_id,
        observed_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_context_current_location
    ON persona360.context_snapshots
    USING gist(current_location);

CREATE INDEX IF NOT EXISTS idx_context_historical_at
    ON persona360.context_snapshots(
        tenant_id,
        historical_at
    );


-- =========================================================
-- EXPERIENCE
--
-- A user doesn't only "chat".
-- They can visit, walk, explore, learn and interact.
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.experience_sessions (
    experience_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    participant_persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    target_persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    experience_type VARCHAR(100) NOT NULL,

    title TEXT,

    description TEXT,

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,

    entry_place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    current_place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    current_context_id UUID
        REFERENCES persona360.context_snapshots(context_id)
        ON DELETE SET NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN (
            'ACTIVE',
            'PAUSED',
            'COMPLETED',
            'ABANDONED'
        )),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_experience_dates
        CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_experience_participant
    ON persona360.experience_sessions(
        tenant_id,
        participant_persona_id,
        started_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_experience_target
    ON persona360.experience_sessions(
        tenant_id,
        target_persona_id,
        started_at DESC
    );


DROP TRIGGER IF EXISTS trg_experience_updated_at
ON persona360.experience_sessions;

CREATE TRIGGER trg_experience_updated_at
BEFORE UPDATE ON persona360.experience_sessions
FOR EACH ROW
EXECUTE FUNCTION persona360.set_updated_at();


-- =========================================================
-- EXPERIENCE EVENTS
-- Fine-grained user experience telemetry
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.experience_events (
    experience_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    experience_id UUID NOT NULL
        REFERENCES persona360.experience_sessions(experience_id)
        ON DELETE CASCADE,

    context_id UUID
        REFERENCES persona360.context_snapshots(context_id)
        ON DELETE SET NULL,

    event_type VARCHAR(100) NOT NULL,

    event_name VARCHAR(200),

    place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    event_id UUID
        REFERENCES persona360.historical_events(event_id)
        ON DELETE SET NULL,

    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    duration_seconds INTEGER,

    payload JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_experience_event_duration
        CHECK (duration_seconds IS NULL OR duration_seconds >= 0)
);

CREATE INDEX IF NOT EXISTS idx_experience_events_session
    ON persona360.experience_events(
        tenant_id,
        experience_id,
        occurred_at
    );

CREATE INDEX IF NOT EXISTS idx_experience_events_place
    ON persona360.experience_events(
        tenant_id,
        place_id
    );


-- =========================================================
-- EXPERIENCE ACTIONS
--
-- ACTION in the LEO model
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.experience_actions (
    action_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    experience_id UUID
        REFERENCES persona360.experience_sessions(experience_id)
        ON DELETE SET NULL,

    subject_persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    action_type VARCHAR(100) NOT NULL,

    action_name VARCHAR(200),

    action_status VARCHAR(30) NOT NULL DEFAULT 'COMPLETED'
        CHECK (action_status IN (
            'PLANNED',
            'STARTED',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        )),

    place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    context_id UUID
        REFERENCES persona360.context_snapshots(context_id)
        ON DELETE SET NULL,

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    result JSONB NOT NULL DEFAULT '{}'::jsonb,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_action_dates
        CHECK (
            completed_at IS NULL
            OR started_at IS NULL
            OR completed_at >= started_at
        )
);

CREATE INDEX IF NOT EXISTS idx_actions_subject
    ON persona360.experience_actions(
        tenant_id,
        subject_persona_id,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_actions_experience
    ON persona360.experience_actions(
        tenant_id,
        experience_id,
        created_at DESC
    );


-- =========================================================
-- ROUTES
-- Supports "walk with Newton", historical tours, etc.
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.experience_routes (
    route_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    target_persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    name TEXT NOT NULL,

    description TEXT,

    route_type VARCHAR(100),

    route_geometry GEOGRAPHY(LineString, 4326),

    distance_meters NUMERIC,

    estimated_duration_seconds INTEGER,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_route_measurements
        CHECK (
            (distance_meters IS NULL OR distance_meters >= 0)
            AND (
                estimated_duration_seconds IS NULL
                OR estimated_duration_seconds >= 0
            )
        )
);

CREATE TABLE IF NOT EXISTS persona360.experience_route_stops (
    route_stop_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    route_id UUID NOT NULL
        REFERENCES persona360.experience_routes(route_id)
        ON DELETE CASCADE,

    place_id UUID
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL,

    event_id UUID
        REFERENCES persona360.historical_events(event_id)
        ON DELETE SET NULL,

    stop_order INTEGER NOT NULL,

    title TEXT NOT NULL,

    narrative TEXT,

    historical_at DATE,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    UNIQUE(route_id, stop_order)
);

CREATE INDEX IF NOT EXISTS idx_routes_geometry
    ON persona360.experience_routes
    USING gist(route_geometry);

CREATE INDEX IF NOT EXISTS idx_route_stops_route
    ON persona360.experience_route_stops(
        tenant_id,
        route_id,
        stop_order
    );


-- =========================================================
-- PERSONA OBSERVATIONS
--
-- LEO enrichment:
-- observed facts from customer/platform/experience data
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.persona_observations (
    observation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID NOT NULL
        REFERENCES persona360.personas(persona_id)
        ON DELETE CASCADE,

    observation_type VARCHAR(100) NOT NULL,

    observation_key TEXT NOT NULL,

    observation_value JSONB NOT NULL,

    source_system VARCHAR(100),

    source_event_id TEXT,

    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    location GEOGRAPHY(Point, 4326),

    confidence NUMERIC(5,4),

    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT chk_observation_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
        ),

    CONSTRAINT chk_observation_dates
        CHECK (
            valid_to IS NULL
            OR valid_from IS NULL
            OR valid_to >= valid_from
        )
);

CREATE INDEX IF NOT EXISTS idx_observations_persona
    ON persona360.persona_observations(
        tenant_id,
        persona_id,
        observed_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_observations_type
    ON persona360.persona_observations(
        tenant_id,
        observation_type,
        observation_key
    );

CREATE INDEX IF NOT EXISTS idx_observations_location
    ON persona360.persona_observations
    USING gist(location);


-- =========================================================
-- AGENT MEMORY
--
-- Context retained for AI agent / persona interaction.
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.agent_memory (
    memory_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    experience_id UUID
        REFERENCES persona360.experience_sessions(experience_id)
        ON DELETE SET NULL,

    memory_type VARCHAR(100) NOT NULL,

    memory_text TEXT NOT NULL,

    importance NUMERIC(5,4),

    embedding vector(1536),

    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_memory_importance
        CHECK (
            importance IS NULL
            OR (importance >= 0 AND importance <= 1)
        ),

    CONSTRAINT chk_memory_dates
        CHECK (
            valid_to IS NULL
            OR valid_from IS NULL
            OR valid_to >= valid_from
        )
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_persona
    ON persona360.agent_memory(
        tenant_id,
        persona_id,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_agent_memory_embedding
    ON persona360.agent_memory
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding IS NOT NULL;


-- =========================================================
-- SOURCE PROVENANCE
--
-- Critical for historical persona integrity.
-- Every generated claim should be traceable.
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.source_provenance (
    provenance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    entity_type VARCHAR(100) NOT NULL,

    entity_id UUID NOT NULL,

    document_id UUID
        REFERENCES persona360.documents(document_id)
        ON DELETE SET NULL,

    chunk_id UUID
        REFERENCES persona360.document_chunks(chunk_id)
        ON DELETE SET NULL,

    evidence_level VARCHAR(30)
        CHECK (evidence_level IN (
            'PRIMARY',
            'SECONDARY',
            'TERTIARY',
            'TRADITION',
            'INFERRED',
            'SPECULATIVE'
        )),

    citation_text TEXT,

    confidence NUMERIC(5,4),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_provenance_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
        )
);

CREATE INDEX IF NOT EXISTS idx_provenance_entity
    ON persona360.source_provenance(
        tenant_id,
        entity_type,
        entity_id
    );

CREATE INDEX IF NOT EXISTS idx_provenance_document
    ON persona360.source_provenance(
        tenant_id,
        document_id
    );


-- =========================================================
-- RAG QUERY LOG
-- Useful for quality / observability
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.rag_queries (
    rag_query_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    experience_id UUID
        REFERENCES persona360.experience_sessions(experience_id)
        ON DELETE SET NULL,

    context_id UUID
        REFERENCES persona360.context_snapshots(context_id)
        ON DELETE SET NULL,

    query_text TEXT NOT NULL,

    query_embedding vector(1536),

    query_type VARCHAR(100),

    retrieved_chunk_ids UUID[],

    retrieved_place_ids UUID[],

    retrieved_event_ids UUID[],

    top_k INTEGER,

    latency_ms INTEGER,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rag_queries_persona
    ON persona360.rag_queries(
        tenant_id,
        persona_id,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_rag_queries_embedding
    ON persona360.rag_queries
    USING hnsw(query_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE query_embedding IS NOT NULL;


-- =========================================================
-- PERSONA EVALUATIONS
--
-- Evaluation:
-- historical accuracy
-- source grounding
-- temporal accuracy
-- spatial accuracy
-- persona consistency
-- hallucination
-- =========================================================

CREATE TABLE IF NOT EXISTS persona360.persona_evaluations (
    evaluation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL
        REFERENCES persona360.sys_tenant(tenant_id)
        ON DELETE CASCADE,

    persona_id UUID
        REFERENCES persona360.personas(persona_id)
        ON DELETE SET NULL,

    experience_id UUID
        REFERENCES persona360.experience_sessions(experience_id)
        ON DELETE SET NULL,

    response_id UUID,

    historical_accuracy NUMERIC(5,4),
    source_grounding NUMERIC(5,4),
    temporal_accuracy NUMERIC(5,4),
    spatial_accuracy NUMERIC(5,4),
    persona_consistency NUMERIC(5,4),
    hallucination_score NUMERIC(5,4),

    evaluator_type VARCHAR(50),

    feedback TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_eval_historical
        CHECK (
            historical_accuracy IS NULL
            OR historical_accuracy BETWEEN 0 AND 1
        ),

    CONSTRAINT chk_eval_grounding
        CHECK (
            source_grounding IS NULL
            OR source_grounding BETWEEN 0 AND 1
        ),

    CONSTRAINT chk_eval_temporal
        CHECK (
            temporal_accuracy IS NULL
            OR temporal_accuracy BETWEEN 0 AND 1
        ),

    CONSTRAINT chk_eval_spatial
        CHECK (
            spatial_accuracy IS NULL
            OR spatial_accuracy BETWEEN 0 AND 1
        ),

    CONSTRAINT chk_eval_persona
        CHECK (
            persona_consistency IS NULL
            OR persona_consistency BETWEEN 0 AND 1
        ),

    CONSTRAINT chk_eval_hallucination
        CHECK (
            hallucination_score IS NULL
            OR hallucination_score BETWEEN 0 AND 1
        )
);

CREATE INDEX IF NOT EXISTS idx_persona_evaluations_persona
    ON persona360.persona_evaluations(
        tenant_id,
        persona_id,
        created_at DESC
    );


-- =========================================================
-- FULL TEXT SEARCH
-- =========================================================

ALTER TABLE persona360.documents
ADD COLUMN IF NOT EXISTS search_vector tsvector;

UPDATE persona360.documents
SET search_vector =
    to_tsvector(
        'simple',
        coalesce(title, '') || ' ' ||
        coalesce(content, '')
    )
WHERE search_vector IS NULL;

CREATE INDEX IF NOT EXISTS idx_documents_search_vector
    ON persona360.documents
    USING gin(search_vector);


-- Trigger to maintain full-text vector

CREATE OR REPLACE FUNCTION persona360.update_document_search_vector()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.search_vector :=
        to_tsvector(
            'simple',
            coalesce(NEW.title, '') || ' ' ||
            coalesce(NEW.content, '')
        );

    RETURN NEW;
END;
$$;


DROP TRIGGER IF EXISTS trg_documents_search_vector
ON persona360.documents;

CREATE TRIGGER trg_documents_search_vector
BEFORE INSERT OR UPDATE OF title, content
ON persona360.documents
FOR EACH ROW
EXECUTE FUNCTION persona360.update_document_search_vector();


-- =========================================================
-- FOREIGN KEYS ADDED AFTER TABLE CREATION
-- because historical_places references personas
-- =========================================================

DO $$
BEGIN

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_persona_birth_place'
    ) THEN

        ALTER TABLE persona360.personas
        ADD CONSTRAINT fk_persona_birth_place
        FOREIGN KEY (birth_place_id)
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL;

    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_persona_death_place'
    ) THEN

        ALTER TABLE persona360.personas
        ADD CONSTRAINT fk_persona_death_place
        FOREIGN KEY (death_place_id)
        REFERENCES persona360.historical_places(place_id)
        ON DELETE SET NULL;

    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_belief_source_document'
    ) THEN

        ALTER TABLE persona360.beliefs
        ADD CONSTRAINT fk_belief_source_document
        FOREIGN KEY (source_document_id)
        REFERENCES persona360.documents(document_id)
        ON DELETE SET NULL;

    END IF;


    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_relationship_source_document'
    ) THEN

        ALTER TABLE persona360.persona_relationships
        ADD CONSTRAINT fk_relationship_source_document
        FOREIGN KEY (source_document_id)
        REFERENCES persona360.documents(document_id)
        ON DELETE SET NULL;

    END IF;

END $$;


-- =========================================================
-- TENANT INTEGRITY AND ISOLATION
--
-- Every entity keeps tenant_id beside its UUID.  The composite foreign keys
-- below prevent a valid UUID from being linked across tenants, which a
-- single-column foreign key cannot enforce.
-- =========================================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_personas_tenant_persona
    ON persona360.personas(tenant_id, persona_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_places_tenant_place
    ON persona360.historical_places(tenant_id, place_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_events_tenant_event
    ON persona360.historical_events(tenant_id, event_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_timelines_tenant_timeline
    ON persona360.timelines(tenant_id, timeline_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_tenant_document
    ON persona360.documents(tenant_id, document_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_chunks_tenant_chunk
    ON persona360.document_chunks(tenant_id, chunk_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_contexts_tenant_context
    ON persona360.context_snapshots(tenant_id, context_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_experiences_tenant_experience
    ON persona360.experience_sessions(tenant_id, experience_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_routes_tenant_route
    ON persona360.experience_routes(tenant_id, route_id);


CREATE OR REPLACE FUNCTION persona360.add_tenant_foreign_key(
    p_child_table TEXT,
    p_child_column TEXT,
    p_parent_table TEXT,
    p_on_delete TEXT DEFAULT 'NO ACTION'
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    constraint_name TEXT := format(
        'fk_%s_%s_same_tenant',
        p_child_table,
        p_child_column
    );
    delete_action TEXT;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = format('persona360.%I', p_child_table)::regclass
          AND conname = constraint_name
    ) THEN
        RETURN;
    END IF;

    delete_action := CASE p_on_delete
        WHEN 'CASCADE' THEN 'ON DELETE CASCADE'
        WHEN 'SET NULL' THEN format(
            'ON DELETE SET NULL (%I)',
            p_child_column
        )
        ELSE 'ON DELETE NO ACTION'
    END;

    EXECUTE format(
        'ALTER TABLE persona360.%I
         ADD CONSTRAINT %I
         FOREIGN KEY (tenant_id, %I)
         REFERENCES persona360.%I (tenant_id, %I)
         %s',
        p_child_table,
        constraint_name,
        p_child_column,
        p_parent_table,
        CASE p_parent_table
            WHEN 'personas' THEN 'persona_id'
            WHEN 'historical_places' THEN 'place_id'
            WHEN 'historical_events' THEN 'event_id'
            WHEN 'timelines' THEN 'timeline_id'
            WHEN 'documents' THEN 'document_id'
            WHEN 'document_chunks' THEN 'chunk_id'
            WHEN 'context_snapshots' THEN 'context_id'
            WHEN 'experience_sessions' THEN 'experience_id'
            WHEN 'experience_routes' THEN 'route_id'
        END,
        delete_action
    );
END;
$$;


SELECT persona360.add_tenant_foreign_key('personas', 'birth_place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('personas', 'death_place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('persona_identities', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('persona_attributes', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('beliefs', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('beliefs', 'source_document_id', 'documents', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('persona_relationships', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('persona_relationships', 'related_persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('persona_relationships', 'source_document_id', 'documents', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('historical_events', 'place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('historical_events', 'source_document_id', 'documents', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('persona_events', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('persona_events', 'event_id', 'historical_events', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('timelines', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('timeline_items', 'timeline_id', 'timelines', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('timeline_items', 'event_id', 'historical_events', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('timeline_items', 'place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('documents', 'author_persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('document_chunks', 'document_id', 'documents', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('quotes', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('quotes', 'document_id', 'documents', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('quotes', 'chunk_id', 'document_chunks', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('context_snapshots', 'subject_persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('context_snapshots', 'target_persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('context_snapshots', 'place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('context_snapshots', 'event_id', 'historical_events', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('context_snapshots', 'timeline_id', 'timelines', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_sessions', 'participant_persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_sessions', 'target_persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_sessions', 'current_context_id', 'context_snapshots', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_sessions', 'entry_place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_sessions', 'current_place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_events', 'experience_id', 'experience_sessions', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('experience_events', 'context_id', 'context_snapshots', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_events', 'place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_events', 'event_id', 'historical_events', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_actions', 'experience_id', 'experience_sessions', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_actions', 'subject_persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_actions', 'place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_actions', 'context_id', 'context_snapshots', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_routes', 'target_persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_route_stops', 'route_id', 'experience_routes', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('experience_route_stops', 'place_id', 'historical_places', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('experience_route_stops', 'event_id', 'historical_events', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('persona_observations', 'persona_id', 'personas', 'CASCADE');
SELECT persona360.add_tenant_foreign_key('agent_memory', 'persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('agent_memory', 'experience_id', 'experience_sessions', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('source_provenance', 'document_id', 'documents', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('source_provenance', 'chunk_id', 'document_chunks', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('rag_queries', 'persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('rag_queries', 'experience_id', 'experience_sessions', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('rag_queries', 'context_id', 'context_snapshots', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('persona_evaluations', 'persona_id', 'personas', 'SET NULL');
SELECT persona360.add_tenant_foreign_key('persona_evaluations', 'experience_id', 'experience_sessions', 'SET NULL');

DROP FUNCTION persona360.add_tenant_foreign_key(TEXT, TEXT, TEXT, TEXT);


CREATE OR REPLACE FUNCTION persona360.enable_tenant_rls(p_table_name TEXT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    EXECUTE format('ALTER TABLE persona360.%I ENABLE ROW LEVEL SECURITY', p_table_name);
    EXECUTE format('ALTER TABLE persona360.%I FORCE ROW LEVEL SECURITY', p_table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON persona360.%I', p_table_name);
    EXECUTE format(
        'CREATE POLICY tenant_isolation_policy ON persona360.%I
         USING (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)
         WITH CHECK (tenant_id = NULLIF(btrim(current_setting(''app.tenant_id'', true)), '''')::uuid)',
        p_table_name
    );
END;
$$;


SELECT persona360.enable_tenant_rls(table_name)
FROM unnest(ARRAY[
    'sys_tenant',
    'personas',
    'persona_identities',
    'persona_attributes',
    'beliefs',
    'persona_relationships',
    'historical_places',
    'historical_events',
    'persona_events',
    'timelines',
    'timeline_items',
    'documents',
    'document_chunks',
    'quotes',
    'embeddings',
    'context_snapshots',
    'experience_sessions',
    'experience_events',
    'experience_actions',
    'experience_routes',
    'experience_route_stops',
    'persona_observations',
    'agent_memory',
    'source_provenance',
    'rag_queries',
    'persona_evaluations'
]) AS table_name;

DROP FUNCTION persona360.enable_tenant_rls(TEXT);


-- =========================================================
-- COMMON VIEWS
-- =========================================================


-- =========================================================
-- External Persona Enrichment Contract
--
-- One row per external identity. A consuming platform can resolve its own
-- customer/user reference here without sharing its schema or tenant ID.
-- In production this projection should be exposed by a Persona360 API,
-- export or event consumer rather than by granting cross-platform DB access.
-- =========================================================

CREATE OR REPLACE VIEW persona360.v_persona_enrichment AS

SELECT
    i.source_system,
    i.identity_type,
    i.external_id,

    p.persona_id,
    p.persona_type,
    p.canonical_name,
    p.display_name,
    p.given_name,
    p.middle_name,
    p.family_name,
    p.gender,
    p.nationality,
    p.birth_date,
    p.death_date,
    p.summary,
    p.updated_at AS persona_updated_at,

    COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'attribute_key', a.attribute_key,
                'attribute_value', a.attribute_value,
                'source_type', a.source_type,
                'confidence', a.confidence,
                'observed_at', a.observed_at,
                'valid_from', a.valid_from,
                'valid_to', a.valid_to
            )
            ORDER BY a.attribute_key, a.observed_at DESC NULLS LAST, a.attribute_id
        ) FILTER (WHERE a.attribute_id IS NOT NULL),
        '[]'::jsonb
    ) AS enrichment_attributes

FROM persona360.persona_identities i

JOIN persona360.personas p
    ON p.tenant_id = i.tenant_id
   AND p.persona_id = i.persona_id

LEFT JOIN persona360.persona_attributes a
    ON a.tenant_id = p.tenant_id
   AND a.persona_id = p.persona_id

GROUP BY
    i.source_system,
    i.identity_type,
    i.external_id,
    p.persona_id,
    p.persona_type,
    p.canonical_name,
    p.display_name,
    p.given_name,
    p.middle_name,
    p.family_name,
    p.gender,
    p.nationality,
    p.birth_date,
    p.death_date,
    p.summary,
    p.updated_at;

ALTER VIEW persona360.v_persona_enrichment
    SET (security_invoker = true);

COMMENT ON VIEW persona360.v_persona_enrichment IS
'Consumer-neutral Persona360 enrichment projection. External systems resolve opaque source_system/identity_type/external_id values here without a shared database schema or tenant identifier.';


-- =========================================================
-- Persona 360 Context View
--
-- WHO + WHERE + WHEN
-- =========================================================

CREATE OR REPLACE VIEW persona360.v_persona_context AS

SELECT
    c.context_id,
    c.tenant_id,

    c.subject_persona_id,
    subject.canonical_name AS subject_name,

    c.target_persona_id,
    target.canonical_name AS target_name,

    c.place_id,
    p.name AS place_name,
    p.city,
    p.country,

    c.event_id,
    e.title AS historical_event,

    c.observed_at,
    c.historical_at,

    c.current_location,

    c.device_context,
    c.environment_context,
    c.session_context,

    c.created_at

FROM persona360.context_snapshots c

LEFT JOIN persona360.personas subject
    ON subject.tenant_id = c.tenant_id
   AND subject.persona_id = c.subject_persona_id

LEFT JOIN persona360.personas target
    ON target.tenant_id = c.tenant_id
   AND target.persona_id = c.target_persona_id

LEFT JOIN persona360.historical_places p
    ON p.tenant_id = c.tenant_id
   AND p.place_id = c.place_id

LEFT JOIN persona360.historical_events e
    ON e.tenant_id = c.tenant_id
   AND e.event_id = c.event_id;

ALTER VIEW persona360.v_persona_context
    SET (security_invoker = true);


-- =========================================================
-- Persona 360 Knowledge View
-- =========================================================

CREATE OR REPLACE VIEW persona360.v_persona_knowledge AS

WITH document_counts AS (
    SELECT tenant_id, author_persona_id AS persona_id, COUNT(*) AS document_count
    FROM persona360.documents
    WHERE author_persona_id IS NOT NULL
    GROUP BY tenant_id, author_persona_id
), quote_counts AS (
    SELECT tenant_id, persona_id, COUNT(*) AS quote_count
    FROM persona360.quotes
    GROUP BY tenant_id, persona_id
), belief_counts AS (
    SELECT tenant_id, persona_id, COUNT(*) AS belief_count
    FROM persona360.beliefs
    GROUP BY tenant_id, persona_id
), relationship_counts AS (
    SELECT tenant_id, persona_id, COUNT(*) AS relationship_count
    FROM persona360.persona_relationships
    GROUP BY tenant_id, persona_id
), event_counts AS (
    SELECT tenant_id, persona_id, COUNT(*) AS event_count
    FROM persona360.persona_events
    GROUP BY tenant_id, persona_id
), timeline_counts AS (
    SELECT tenant_id, persona_id, COUNT(*) AS timeline_count
    FROM persona360.timelines
    GROUP BY tenant_id, persona_id
)
SELECT
    p.persona_id,
    p.tenant_id,
    p.canonical_name,
    p.persona_type,
    COALESCE(d.document_count, 0) AS document_count,
    COALESCE(q.quote_count, 0) AS quote_count,
    COALESCE(b.belief_count, 0) AS belief_count,
    COALESCE(r.relationship_count, 0) AS relationship_count,
    COALESCE(pe.event_count, 0) AS event_count,
    COALESCE(t.timeline_count, 0) AS timeline_count
FROM persona360.personas p
LEFT JOIN document_counts d
    ON d.tenant_id = p.tenant_id
   AND d.persona_id = p.persona_id
LEFT JOIN quote_counts q
    ON q.tenant_id = p.tenant_id
   AND q.persona_id = p.persona_id
LEFT JOIN belief_counts b
    ON b.tenant_id = p.tenant_id
   AND b.persona_id = p.persona_id
LEFT JOIN relationship_counts r
    ON r.tenant_id = p.tenant_id
   AND r.persona_id = p.persona_id
LEFT JOIN event_counts pe
    ON pe.tenant_id = p.tenant_id
   AND pe.persona_id = p.persona_id
LEFT JOIN timeline_counts t
    ON t.tenant_id = p.tenant_id
   AND t.persona_id = p.persona_id;

ALTER VIEW persona360.v_persona_knowledge
    SET (security_invoker = true);


-- =========================================================
-- Example GEO RAG helper function
--
-- Find historical places near the user's GPS position.
-- =========================================================

CREATE OR REPLACE FUNCTION persona360.find_nearby_places(
    p_tenant_id UUID,
    p_latitude DOUBLE PRECISION,
    p_longitude DOUBLE PRECISION,
    p_radius_meters DOUBLE PRECISION DEFAULT 5000
)
RETURNS TABLE (
    place_id UUID,
    name TEXT,
    place_type VARCHAR,
    city TEXT,
    country TEXT,
    distance_meters DOUBLE PRECISION
)
LANGUAGE SQL
STABLE
AS $$

    SELECT
        p.place_id,
        p.name,
        p.place_type,
        p.city,
        p.country,

        ST_Distance(
            p.location,
            ST_SetSRID(
                ST_MakePoint(
                    p_longitude,
                    p_latitude
                ),
                4326
            )::geography
        ) AS distance_meters

    FROM persona360.historical_places p

    WHERE p.tenant_id = p_tenant_id

      AND p.location IS NOT NULL

      AND ST_DWithin(
            p.location,
            ST_SetSRID(
                ST_MakePoint(
                    p_longitude,
                    p_latitude
                ),
                4326
            )::geography,
            p_radius_meters
      )

    ORDER BY distance_meters;

$$;


-- =========================================================
-- Example semantic RAG helper
--
-- Requires a 1536-dimensional query embedding.
-- =========================================================

CREATE OR REPLACE FUNCTION persona360.search_document_chunks(
    p_tenant_id UUID,
    p_query_embedding vector(1536),
    p_limit INTEGER DEFAULT 10
)
RETURNS TABLE (
    chunk_id UUID,
    document_id UUID,
    content TEXT,
    similarity DOUBLE PRECISION
)
LANGUAGE SQL
STABLE
AS $$

    SELECT
        c.chunk_id,
        c.document_id,
        c.content,

        1 - (
            c.embedding <=> p_query_embedding
        ) AS similarity

    FROM persona360.document_chunks c

    WHERE c.tenant_id = p_tenant_id

      AND c.embedding IS NOT NULL

    ORDER BY
        c.embedding <=> p_query_embedding

    LIMIT p_limit;

$$;


-- =========================================================
-- Recommended COMMENTs
-- =========================================================

COMMENT ON TABLE persona360.context_snapshots IS
'LEO Context object: WHO + WHERE + WHEN + environment + session state.';

COMMENT ON TABLE persona360.experience_sessions IS
'LEO Experience layer: turns Persona360 data into a real user journey.';

COMMENT ON TABLE persona360.experience_actions IS
'LEO Action layer: records actions performed or triggered by users and AI agents.';

COMMENT ON TABLE persona360.persona_observations IS
'LEO enrichment layer: behavioral, platform, location and contextual observations attached to a persona.';

COMMENT ON TABLE persona360.historical_places IS
'PostGIS geographic knowledge for real-world and historical locations.';

COMMENT ON TABLE persona360.document_chunks IS
'pgvector-backed semantic retrieval units for RAG.';

COMMENT ON TABLE persona360.source_provenance IS
'Traceability layer used to distinguish documented facts from inference, tradition and speculation.';


-- =========================================================
-- END
-- =========================================================