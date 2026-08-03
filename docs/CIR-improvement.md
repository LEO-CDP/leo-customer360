## Summary Improvement for Customer Identity Resolution (CIR)

Your PostgreSQL schema for **Customer Identity Resolution (CIR)** and Customer 360 is **architecturally solid** and well-structured for an enterprise multi-tenant CDP.

### Key Strengths

* **Separation of Concerns:** Clean progression from landing/staging (`cdp_raw_profiles_stage`), to golden master profiles (`cdp_master_profiles`), linked via a dedicated join table (`cdp_profile_links`).
* **Non-Blocking Event Ingestion:** `cdp_raw_events` carries raw identity parameters directly (`device_id`, `cookie_id`, `external_customer_id`), allowing high-throughput ingestion without waiting for real-time CIR linking.
* **Metadata-Driven Matching & Consolidation:** `cdp_profile_attributes` provides a clean registry for specifying `is_identity_resolution`, `matching_rule`, `matching_threshold`, and `consolidation_rule`.
* **Multi-Tenant Security:** Strict tenant isolation enforced at the database level via `tenant_id` and PostgreSQL Row-Level Security (RLS).

However, to support **production-grade Identity Graph rules** (such as priority ranking, sliding limits, junk value blocking, unmerging/profile splitting, and high-speed identifier point lookups), a few critical fields and auxiliary tables are missing.

---

## 1. Schema Gap Analysis for CIR

### 1.1 Missing Rule Fields in `cdp_profile_attributes`

While `cdp_profile_attributes` includes `matching_rule` and `consolidation_rule`, it lacks fields to configure rule constraints directly in the metadata registry:

| Missing Field | Recommended Type | Purpose in CIR Strategy |
| --- | --- | --- |
| `priority_rank` | `INTEGER` | Defines priority hierarchy when resolving limits (e.g., Rank 1 for `user_id`, Rank 2 for `email`, Rank 3 for `device_id`). |
| `value_limit` | `INTEGER` | Maximum allowed values per profile (e.g., `1` for `user_id`, `5` for `email`/`device_id`). |
| `limit_timeframe` | `VARCHAR(50)` | Time window for the limit (e.g., `1_ever`, `5_weekly`, `5_annually`). |
| `blocked_values` | `JSONB` / `TEXT[]` | Specific strings or regex patterns to block from identity promotion (e.g., `["null", "-1", "anonymous"]`, `^[0-]*$`). |

---

### 1.2 Missing Lineage & State Fields in `cdp_profile_links`

`cdp_profile_links` records which `raw_profile_id` mapped to which `master_profile_id`. However, in real-world CIR, profiles often need to be **split, unlinked, or re-stitched** when an incorrect merge occurs (e.g., shared device scenario).

| Missing Field | Recommended Type | Purpose in CIR Strategy |
| --- | --- | --- |
| `status` | `VARCHAR(20)` | Tracks link state (`ACTIVE`, `HISTORICAL`, `UNLINKED`, `SUPERSEDED`). |
| `unlinked_at` | `TIMESTAMP WITH TIME ZONE` | Timestamp when a link was severed during a profile split. |
| `unlinked_reason` | `TEXT` | Audit trail for manual or automated unlinking (e.g., "Limit exceeded demotion", "Manual split by admin"). |

---

### 1.3 Missing High-Performance Identifier Index Table

In `cdp_master_profiles`, external IDs and device identifiers are stored inside JSONB (`external_ids`) or TEXT arrays (`device_ids`, `cookie_ids`, `advertising_ids`).

While GIN indexes are provided, performing $O(1)$ point lookups across millions of records during streaming event resolution on composite JSON/array types creates unnecessary query overhead.

**Missing Component:** A dedicated, flattened **Identifier Lookup Index Table** (`cdp_identity_index`) that maps `(tenant_id, identifier_type, normalized_identifier_value) -> master_profile_id`.

---

### 1.4 Missing Master-to-Master Merge Audit Table

When two `cdp_master_profiles` merge (e.g., Profile A with `email` matches Profile B when `user_id` is supplied later), one master profile is tombstoned or merged into another.

The current schema lacks a dedicated table to track **Master Profile Merges** and preserve snapshot history for data lineage and unmerge operations.

---

## 2. Recommended SQL Enhancements & DDL Updates

Below are the exact DDL statements required to update your schema for complete CIR coverage.

### Update 1: Extend `cdp_profile_attributes` with CIR Rule Engine Metadata

```sql
-- Add explicit priority, value limits, and blocked value configs to attribute registry
ALTER TABLE customer360.cdp_profile_attributes
    ADD COLUMN IF NOT EXISTS priority_rank INTEGER DEFAULT 99,
    ADD COLUMN IF NOT EXISTS value_limit INTEGER DEFAULT 5,
    ADD COLUMN IF NOT EXISTS limit_timeframe VARCHAR(50) DEFAULT '5_annually',
    ADD COLUMN IF NOT EXISTS blocked_values JSONB DEFAULT '["null", "-1", "anonymous", "void", "abc123"]'::jsonb,
    ADD COLUMN IF NOT EXISTS blocked_patterns TEXT[] DEFAULT ARRAY['^[0-]*$'];

COMMENT ON COLUMN customer360.cdp_profile_attributes.priority_rank IS 'Rank hierarchy used during limit demotion (1 = highest priority, e.g. user_id).';
COMMENT ON COLUMN customer360.cdp_profile_attributes.value_limit IS 'Maximum allowed unique values on a single master profile for this identifier.';
COMMENT ON COLUMN customer360.cdp_profile_attributes.limit_timeframe IS 'Window for limit enforcement: 1_ever, 5_weekly, 5_monthly, 5_annually.';
COMMENT ON COLUMN customer360.cdp_profile_attributes.blocked_values IS 'Exact string values blocked from being promoted to external identifiers.';

```

---

### Update 2: Extend `cdp_profile_links` for Unmerge & Split Lifecycle

```sql
-- Add lifecycle status and unlinking metadata
ALTER TABLE customer360.cdp_profile_links
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'HISTORICAL', 'UNLINKED', 'SUPERSEDED')),
    ADD COLUMN IF NOT EXISTS unlinked_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS unlinked_reason TEXT,
    ADD COLUMN IF NOT EXISTS unlinked_by UUID REFERENCES customer360.sys_user(user_id);

CREATE INDEX IF NOT EXISTS idx_cdp_profile_links_status 
    ON customer360.cdp_profile_links (tenant_id, status) 
    WHERE status = 'ACTIVE';

```

---

### Update 3: Create Flattened Identifier Index Table (`cdp_identity_index`)

```sql
-- Unified point-lookup index for ultra-fast streaming match resolution
CREATE TABLE IF NOT EXISTS customer360.cdp_identity_index (
    identity_index_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles(master_profile_id) ON DELETE CASCADE,
    
    -- Identifier Classification
    identifier_type VARCHAR(100) NOT NULL, -- e.g., 'user_id', 'email', 'phone', 'device_id', 'cookie_id', 'appsflyer_id'
    identifier_value TEXT NOT NULL,         -- Canonical raw value
    identifier_value_normalized TEXT NOT NULL, -- Normalized / lowercased value for exact matching
    
    -- Status & Governance
    is_primary BOOLEAN DEFAULT FALSE,
    is_blocked BOOLEAN DEFAULT FALSE,
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    
    CONSTRAINT uq_cdp_identity_index UNIQUE (tenant_id, identifier_type, identifier_value_normalized)
);

COMMENT ON TABLE customer360.cdp_identity_index IS 'Flattened O(1) B-Tree lookup table for cross-channel identifier matching during streaming ingestion.';

-- Fast lookup index for incoming identifier queries
CREATE INDEX IF NOT EXISTS idx_cdp_identity_lookup 
    ON customer360.cdp_identity_index (tenant_id, identifier_type, identifier_value_normalized) 
    WHERE is_blocked = FALSE;

CREATE INDEX IF NOT EXISTS idx_cdp_identity_master 
    ON customer360.cdp_identity_index (master_profile_id);

-- Enable RLS for identity index
ALTER TABLE customer360.cdp_identity_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_identity_index FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_identity_index;
CREATE POLICY tenant_policy ON customer360.cdp_identity_index
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

```

---

### Update 4: Create Master Profile Merge History Table (`cdp_profile_merge_history`)

```sql
-- Audit table recording master-to-master profile consolidation
CREATE TABLE IF NOT EXISTS customer360.cdp_profile_merge_history (
    merge_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES customer360.sys_tenant(tenant_id),
    
    -- Merge Lineage
    target_master_profile_id UUID NOT NULL REFERENCES customer360.cdp_master_profiles(master_profile_id), -- Retained Profile
    source_master_profile_id UUID NOT NULL, -- Merged / Tombstoned Profile ID
    
    -- Match Details
    merge_reason TEXT NOT NULL, -- e.g., 'Deterministic email match', 'Manual admin merge'
    matched_identifier_type VARCHAR(100),
    matched_identifier_value TEXT,
    match_score NUMERIC(5, 4),
    
    -- State Snapshots for Unmerge Rollback
    source_profile_snapshot JSONB NOT NULL, -- Full snapshot of source profile before deletion
    target_profile_snapshot JSONB NOT NULL, -- Full snapshot of target profile before merge
    
    merged_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    merged_by UUID REFERENCES customer360.sys_user(user_id) -- Nullable if automated system process
);

COMMENT ON TABLE customer360.cdp_profile_merge_history IS 'Audit log of master-to-master merges storing JSONB profile snapshots to enable profile unmerging/splitting.';

CREATE INDEX IF NOT EXISTS idx_cdp_merge_history_target 
    ON customer360.cdp_profile_merge_history (tenant_id, target_master_profile_id);

CREATE INDEX IF NOT EXISTS idx_cdp_merge_history_source 
    ON customer360.cdp_profile_merge_history (tenant_id, source_master_profile_id);

-- Enable RLS for merge history
ALTER TABLE customer360.cdp_profile_merge_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer360.cdp_profile_merge_history FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_policy ON customer360.cdp_profile_merge_history;
CREATE POLICY tenant_policy ON customer360.cdp_profile_merge_history
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

```

---

## 3. Summary Compliance Check Matrix

| Feature / Capability | Initial Schema Status | Proposed Resolution |
| --- | --- | --- |
| **Staging & Master Isolation** | ✅ Ready | `cdp_raw_profiles_stage` & `cdp_master_profiles` exist. |
| **Async Raw Event Ingestion** | ✅ Ready | `cdp_raw_events` carries identity parameters directly. |
| **Multi-Tenant Security** | ✅ Ready | `tenant_id` on all tables with explicit PostgreSQL RLS. |
| **Identifier Priority Ranks** | ⚠️ Missing | Added `priority_rank` to `cdp_profile_attributes`. |
| **Identifier Limits & Windows** | ⚠️ Missing | Added `value_limit` and `limit_timeframe` to `cdp_profile_attributes`. |
| **Blocked Junk Value Filter** | ⚠️ Missing | Added `blocked_values` & `blocked_patterns` to `cdp_profile_attributes`. |
| **Point Lookup Performance** | ⚠️ Suboptimal | Created `cdp_identity_index` table with B-Tree lookup indexes. |
| **Unmerge & Profile Splits** | ⚠️ Missing | Extended `cdp_profile_links` status and created `cdp_profile_merge_history`. |