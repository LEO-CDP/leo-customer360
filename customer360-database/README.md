# Customer 360 Database

This directory contains the PostgreSQL schema, seed data, materialized views,
and forward migrations for the Customer 360 platform.

The canonical application DDL is [database-schema.sql](database-schema.sql).
It creates the `customer360` schema, required extensions, tenant-scoped tables,
foreign keys, indexes, triggers, graph partitions, connector configuration,
and row-level security policies. Keep application queries and DAO models
aligned with this file; do not infer table or column names from older planning
documents or backups.

## Database Requirements

- PostgreSQL 16 or newer.
- A database role with permission to create extensions, schema objects, and
	policies during bootstrap.
- The runtime application role should be a dedicated non-superuser without
	`BYPASSRLS`.

`database-schema.sql` enables these extensions:

- `uuid-ossp` and `pgcrypto` for UUID generation and cryptographic helpers.
- `vector` for embedding columns and similarity indexes.
- `postgis` for geospatial domain events and location data.
- `pg_trgm` and `fuzzystrmatch` for fuzzy identity matching.
- `btree_gist` for exclusion constraints on suppression windows.

## Installation Order

For a fresh database, run the SQL files in this order:

1. `database-schema.sql`
2. `init-core-database.sql`
3. `init-prompt-store-seed.sql`
4. `data-view-for-llm.sql`
5. `migrations/*.sql`, in filename order

The deployment helper [run-sql.sh](../deployments/postgres/run-sql.sh) applies
that order automatically after the repository's PostgreSQL bootstrap scripts.
It runs `psql` with `ON_ERROR_STOP=1`, so the first failed script stops the
deployment.

Example for a local database:

```bash
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/database-schema.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/init-core-database.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/init-prompt-store-seed.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/data-view-for-llm.sql
for migration in customer360-database/migrations/*.sql; do
	psql -v ON_ERROR_STOP=1 -d customer360 -f "$migration"
done
```

`database-schema.sql` is idempotent for normal object creation, but it is not
a replacement for migrations on an existing volume. Review migration impact
before running against production data. Several migrations deliberately fail
when existing data cannot be safely backfilled or constrained.

## Initialization And Seeds

`init-core-database.sql` is the seed/setup script. It:

- creates the default tenant `11111111-1111-1111-1111-111111111111`;
- seeds system domains and default tenant-domain assignments;
- seeds the governed event catalog and profile attribute metadata;
- seeds persona scoring configuration and other reference data; and
- prepares the tenant context used by RLS-protected inserts.

`data-view-for-llm.sql` creates the LLM-oriented materialized views after the
base tables exist. It currently creates:

- `customer360.mv_customer_transactions`;
- `customer360.mv_customer_engagements`; and
- `customer360.mv_customer_360_overview`.

These are derived projections, not the source of truth. Refresh them after
large profile, transaction, or interaction loads. Use `REFRESH MATERIALIZED
VIEW CONCURRENTLY` only when the required unique indexes and deployment
conditions are present.

## Schema Areas

### System, tenants, and access control

- `sys_tenant`, `sys_domain`, and `sys_tenant_domain` define tenants and the
	system business-domain vocabulary.
- `sys_organization` stores tenant organization trees.
- `sys_user` stores internal application users. SSO identities and local
	credentials are separated into `sys_userinfo`.
- `sys_role`, `sys_permission`, `sys_role_permission`, and `sys_user_role`
	implement tenant RBAC with a global permission dictionary.
- `sys_audit_log` stores action, authentication, request, before/after, and
	result information for compliance reporting.
- `sys_data_source` stores inbound data-source and ingestion metadata. Its
	`access_tokens` JSONB belongs to inbound collection connectors.

### CRM and outbound activation

- `crm_campaign` and `crm_campaign_performance_daily` store campaigns and
	daily omnichannel performance. `vw_campaign_performance_metrics` exposes
	aggregate spend, funnel rates, CPA, and ROAS.
- `crm_campaign_member`, `crm_lead`, `crm_lead_source`, `crm_contact`,
	`crm_account`, `crm_opportunity`, and `crm_industry` model CRM journey
	entities.
- `crm_customer_contacts` stores customer interactions.
- `crm_transactions` stores source-agnostic retail, banking, travel, and
	other transaction facts. `master_profile_id` is nullable because identity
	linking can happen asynchronously.
- `crm_message_templates`, `crm_campaign_content_items`,
	`crm_campaign_reviews`, and `crm_segment_sync_runs` support governed
	campaign drafting, review, content linkage, and segment synchronization.
- `cdp_campaign_dispatch_logs` is the idempotent per-recipient outbound send
	ledger.
- `crm_connector_config` is the tenant-scoped outbound connector registry for
	email, SMS, push, chat, ads, and webhooks. For Zalo, use
	`connector_type = 'CHAT'` and `provider = 'ZALO'`; store secret material in
	`credentials` and non-secret runtime settings in `config`.
- `crm_suppression_list` stores omnichannel global or campaign-scoped
	suppression records. PostgreSQL exclusion constraints prevent overlapping
	active suppression windows.

### Customer 360 profiles and identity resolution

- `cdp_master_profiles` is the golden profile per tenant and domain. It holds
	identity, demographics, channel identifiers, consent, lifecycle state,
	persona labels, lineage, and ML/CX scores.
- `cdp_domain_profiles` stores domain-specific attributes and analytics as
	validated JSONB objects, one row per master profile and domain.
- `cdp_raw_profiles_stage` is the inbound landing area before Customer
	Identity Resolution (CIR).
- `cdp_profile_links` records raw-to-master matches, scores, methods, and
	unmerge lifecycle state.
- `cdp_identity_index` provides normalized O(1) identifier lookup for
	streaming matching.
- `cdp_profile_merge_history` preserves source and target snapshots for
	master-profile merges and later unmerge analysis.
- `cdp_relations` and `cdp_relation_types` model typed profile-to-profile
	relationships.

### Personas, metadata, and scoring

- `cdp_persona_archetypes` stores reusable tenant/domain persona definitions,
	embeddings, centroids, and matched-profile counts.
- `cdp_customer_personas` stores versioned profile-to-archetype assignments.
- `cdp_persona_features`, `cdp_persona_score_details`, and
	`cdp_persona_history` provide explainability and assignment history.
- `cdp_persona_config` stores typed runtime scoring thresholds and weights.
- `cdp_profile_attributes` is the governed attribute catalog used by CIR,
	segmentation, and scoring metadata.
- `cdp_ai_agents` is the single registry for scoring models, rule engines, and
	task-oriented agents. It also stores current prompt instructions and the
	append-only `prompt_versions` JSONB history used by `customer360-agent`.
- `cdp_event_catalog` defines the cross-domain event vocabulary.
- `cdp_content_items` stores personalized content candidates.
- `cdp_segments` stores audience rules, generated SQL, processing source, and
	computed membership counts.

### Graph storage

`graph_edges` is a general-purpose tenant-scoped graph edge table partitioned
by `relation`. Known relation partitions include `belongs_to`, `comes_from`,
`converted`, `follows`, `is_part_of`, `is_active_as`, `is_connected_to`,
`is_from`, `created_by`, `is_driven_by`, `has_role`, `has`,
`is_for_the`, and `belongs_to_industry`. `graph_edges_other` is the default
partition for new relation values.

## Tenant Isolation

Tenant isolation has two layers:

1. Tenant-owned tables carry a non-null `tenant_id` foreign key to
	 `sys_tenant`.
2. Composite tenant-aware foreign keys are added near the end of
	 `database-schema.sql` so a child row cannot reference a parent from another
	 tenant. The original single-column foreign keys remain for compatibility.

RLS is enabled and forced for tenant-owned tables. Every pooled connection
used by a tenant-facing service must set the tenant before querying:

```sql
SELECT set_config('app.tenant_id', '<tenant-uuid>', true);
```

The policy uses `current_setting('app.tenant_id', true)`. An unset or blank
tenant context becomes `NULL`, so reads and writes fail closed. PostgreSQL
superusers and roles with `BYPASSRLS` bypass RLS even when `FORCE ROW LEVEL
SECURITY` is enabled; do not use such a role for tenant-facing application
traffic.

The identity-resolution batch can process multiple tenants on one connection,
so it must set the tenant context before each tenant-scoped operation.

## Important Database Behaviors

- Profile probability, score, persona, domain-attribute, and lifecycle checks
	are enforced in PostgreSQL, not only in API validation.
- `sync_domain_attribute_catalog()` registers new
	`cdp_domain_profiles.domain_attributes` keys in `cdp_profile_attributes`
	without overwriting curated metadata.
- `sync_persona_archetype_match_count()` maintains the active distinct-profile
	count shown by persona administration screens.
- Vector columns use different dimensions by purpose. Campaign and graph
	embeddings use `vector(1536)`; persona archetype embeddings use
	`vector(768)`.
- `crm_connector_config.credentials` and `sys_data_source.access_tokens` may
	contain secrets. Restrict database access, avoid selecting them in general
	reporting queries, and never expose them through read APIs.
- `crm_transactions.master_profile_id` is intentionally nullable until CIR
	resolves the transaction identity.
- `graph_edges` and `sys_user_role` may require the tenant-consistency
	migration when upgrading older volumes.

## Forward Migrations

Run these files in numeric order after the base schema and seed/materialized
view scripts:

| Migration | Purpose |
| --- | --- |
| `001_harden_tenant_rls_policies.sql` | Rebuild fail-closed tenant policies on existing tables. |
| `002_crm_connector_config.sql` | Replace the legacy email-only connector table with the generic outbound connector registry. |
| `003_crm_suppression_list.sql` | Introduce the omnichannel suppression registry and migrate legacy email suppression data. |
| `004_tenant_consistency_and_graph_rls.sql` | Backfill tenant ownership for graph edges and role assignments, then add tenant-safe constraints and RLS. |
| `005_profile_quality_and_tenant_domain_rls.sql` | Harden profile score ranges, hash state, timestamps, domain assignments, and related tenant constraints. |
| `006_suppression_expiry_exclusion.sql` | Enforce non-overlapping active suppression windows while allowing expired records to be replaced. |
| `007_zalo_connector_config.sql` | Move legacy Zalo OA settings and tokens from `sys_data_source` into `crm_connector_config`. |

Review data-changing migrations before production execution. In particular,
migration 004 stops if existing graph edges cannot be assigned to a tenant,
and migration 005 stops if existing profile scores violate the new ranges.

## Verification Queries

After initialization, verify the schema and RLS context with a non-superuser:

```sql
SELECT current_schema();
SELECT to_regclass('customer360.cdp_master_profiles');
SELECT to_regclass('customer360.crm_connector_config');
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relnamespace = 'customer360'::regnamespace
	AND relname IN ('cdp_master_profiles', 'crm_connector_config', 'graph_edges');
SELECT current_setting('app.tenant_id', true);
```

For tenant-facing application queries, set `app.tenant_id` in the same
transaction as the query. Do not rely on a process-global tenant value when
using pooled connections.