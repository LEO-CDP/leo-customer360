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
2. `init-cdp-ai-agents.sql`
3. `init-core-database.sql`
4. `data-view-for-llm.sql`
5. `migrations/*.sql`, in filename order

The deployment helper [run-sql.sh](../deployments/postgres/run-sql.sh) applies
that order automatically after the repository's PostgreSQL bootstrap scripts.
It runs `psql` with `ON_ERROR_STOP=1`, so the first failed script stops the
deployment.

Example for a local database:

```bash
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/database-schema.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/init-cdp-ai-agents.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/init-core-database.sql
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

`init-cdp-ai-agents.sql` is the unified AI-agent registry seed. It creates
12 core templates (one per execution type), seven compatibility identities for
attribute ownership, and the notification/segment prompts consumed by the agent
service. New entries are `INACTIVE`. Run it before
`init-core-database.sql`, whose profile-attribute seed references those agents.

The seed is atomic and insert-only on conflicts: rerunning it preserves deployed
configuration, activation state, feature definitions and append-only prompt
history. It does **not** repair existing rows. Upgrade existing databases using
reviewed migrations and the versioned prompt publishing API.

### Model and feature readiness

`model_name` identifies a documented estimator class or hosted model, not an
invented trained-artifact name:

| Core type | Seed identifier |
| --- | --- |
| Classification | `xgboost.XGBClassifier` |
| Regression / forecasting | `lightgbm.LGBMRegressor` |
| Clustering | `sklearn.cluster.MiniBatchKMeans` |
| Ranking | `lightgbm.LGBMRanker` |
| Anomaly detection | `sklearn.ensemble.IsolationForest` |
| Uplift | `econml.metalearners.XLearner` |
| Embedding | `text-embedding-3-small` (1536 dimensions) |
| Graph ML | `torch_geometric.nn.models.GraphSAGE` |
| Optimization / rules | `NULL` until an engine is selected |
| Generative LLM | `openai/gpt-4.1-mini-2025-04-14` |

Estimator identifiers are documentation for an allow-listed implementation;
never dynamically import code from registry values. No new runtime dependency
is installed by the seed. Hyperparameters mix estimator settings and adapter
metadata; do not pass the entire JSON object to an estimator constructor.
Before activating a template, provision its executor,
pin library versions, register its trained artifact and preprocessing contract,
and validate tenant-isolated outputs. The scoring service is currently a
scaffold, not a trained-model pipeline.

In particular, LightGBM forecasting needs lag/seasonal features, time-based
validation and a separate prediction-interval estimator; IsolationForest does
not natively return a probability in `[0, 1]`; GraphSAGE needs graph topology
(`edge_index`) and trained weights in addition to node features; XLearner needs
pre-treatment covariates, explicit treatment/outcome labels and validated causal
assumptions. Prompts do not implement these algorithms.

The embedding model must use the embeddings endpoint, not chat completion.
Respect provider input limits and redact/authorize customer text before sending
it externally. The pinned GPT model and its JSON-object response configuration
are compatible with LiteLLM chat completion. The current agent service resolves
models from request overrides / `LLM_MODEL`, not from this registry; seed
activation is not an endpoint access-control mechanism.

The feature catalog contains reviewed derivation **guidance**, not an executable
feature builder. Its contract requires tenant-scoped, UTC, as-of source snapshots,
independent aggregation before joins, and missing-value/spine normalization.
Do not use present-day profile or campaign state to backfill training history.
Suppression must come from the activation resolver, including identifier-only
records and campaign/channel scope. Only literal JSON `true` grants consent.

Official model references:
[OpenAI GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini),
[OpenAI embeddings](https://developers.openai.com/api/docs/guides/embeddings),
[XGBoost](https://xgboost.readthedocs.io/en/stable/python/python_api.html),
[LightGBM](https://lightgbm.readthedocs.io/en/stable/Python-API.html),
[scikit-learn clustering](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html),
[scikit-learn anomalies](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html),
[EconML](https://www.pywhy.org/EconML/_autosummary/econml.metalearners.XLearner.html),
[GraphSAGE](https://pytorch-geometric.readthedocs.io/en/latest/generated/torch_geometric.nn.models.GraphSAGE.html).

Regression checks:

```bash
python -m unittest discover -s customer360-database/tests -p 'test_ai_agent_seed_contract.py'
# Only against a disposable database, after schema + AI seed + core seed:
psql -v ON_ERROR_STOP=1 -d seed_test -f customer360-database/tests/ai_agent_seed.sql
```

The SQL regression checks execute every catalog SQL fragment, test consent and
tenant/time boundaries, and verify that rerunning the seed preserves deployed
configuration and prompt history.

`init-core-database.sql` is the remaining seed/setup script. It:

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
- `crm_campaign_experiments` stores campaign-level experiment status, date
	windows, primary metric, and winning variant. Its
	`crm_campaign_experiment_variants` rows define each treatment/control
	variant's segment, optional message template, allocation percentage, and
	lifecycle status; daily performance can be attributed to a variant.
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
- `cdp_ai_feature_catalog` is the allow-listed contract behind
	`cdp_ai_agents.input_features`. Each feature has reviewed SQL and pandas
	derivation guidance over tenant-scoped profile, S3 event, transaction,
	contact, graph, candidate, or runtime data.
- `cdp_event_catalog` defines the cross-domain event vocabulary.
- `cdp_content_items` stores personalized content candidates.
- `cdp_segments` stores audience rules, generated SQL, processing source, and
	computed membership counts.
- `cdp_agent_workflow` links the global `cdp_ai_agents` registry to tenant-owned
	segments in a many-to-many relationship. Each row configures one agent for
	one segment, with a positive `execution_order`, `is_active`, JSONB
	`configuration`, and a `candidate_content_item_ids` list referencing
	`cdp_content_items` (including products). Candidate IDs must be distinct,
	exist, and belong to the same tenant; referenced items cannot be deleted or
	have their tenant/ID changed until removed from all workflow lists.

### Segment agent workflows

An agent can be reused across any number of segments; each segment can have
any number of different agents. Agent membership and queue positions are
unique per tenant/segment. For example, configure Churn Risk Intelligence at
position 1, Offer & Incentive Optimization at 2, and 1-to-1 Email Personalizer
at 3. Recommendation agents can have different candidate lists and ranking
configuration in each segment.

Load a segment's enabled steps in ascending order:

```sql
SELECT agent_workflow_id, agent_code, execution_order,
       candidate_content_item_ids, configuration
FROM customer360.cdp_agent_workflow
WHERE tenant_id = :tenant_id AND segment_id = :segment_id AND is_active
ORDER BY execution_order;
```

This table defines workflow configuration, not a job queue or execution
ledger. The workflow runner must serialize runs of the same segment, await
each step's completion before starting the next, and pass earlier results
to later steps. Separate segments may run concurrently. The schema does not
implement a runner, retries, or failure policy. Empty candidate lists mean
no explicit candidates, not permission to rank the entire catalog.

Set `app.tenant_id` within the transaction before accessing workflow data.
Use `SET CONSTRAINTS customer360.uq_cdp_agent_workflow_execution_order DEFERRED`
within a transaction to swap queue positions without transient uniqueness
conflicts. Writers should update `updated_at` when editing configuration.

Candidate references use a UUID array to keep this feature in one new table.
Validation triggers lock content rows while linking them and restrict
deletion/key changes while referenced. Use the normal `READ COMMITTED`
isolation level for these writes; the triggers are not native array foreign
keys, so candidate-list writes and content deletion/key changes explicitly
reject `REPEATABLE READ` rather than permit stale-snapshot integrity checks.
For stronger transaction isolation, use `SERIALIZABLE` and retry serialization
failures. The invoker-rights validation trigger needs `SELECT` and `UPDATE`
privileges on `cdp_content_items` to acquire row locks; tenant RLS still applies.

### Graph storage

`graph_edges` is a general-purpose tenant-scoped graph edge table partitioned
by `relation`. Known relation partitions include `belongs_to`, `comes_from`,
`converted`, `follows`, `is_part_of`, `is_active_as`, `is_connected_to`,
`is_from`, `created_by`, `is_driven_by`, `has_role`, `has`,
`is_for_the`, and `belongs_to_industry`. `graph_edges_other` is the default
partition for new relation values. The 15 physical child tables are
`graph_edges_belongs_to`, `graph_edges_comes_from`,
`graph_edges_converted`, `graph_edges_follows`, `graph_edges_is_part_of`,
`graph_edges_is_active_as`, `graph_edges_is_connected_to`,
`graph_edges_is_from`, `graph_edges_created_by`,
`graph_edges_is_driven_by`, `graph_edges_has_role`, `graph_edges_has`,
`graph_edges_is_for_the`, `graph_edges_belongs_to_industry`, and
`graph_edges_other`. Query and manage edges through the partitioned parent
`graph_edges` unless a partition-specific operation is required.

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

Forward migrations are incremental upgrades for databases that already have the
base schema. Apply them **once, in filename order**, after
`database-schema.sql`, the required seeds, and materialized-view setup. The
repository's [`run-sql.sh`](../deployments/postgres/run-sql.sh) runs the base
scripts first, then applies sorted `migrations/*.sql` files with
`ON_ERROR_STOP=1`; it excludes `*.down.sql` rollback scripts. When using the
manual loop in Installation Order, shell glob order is the migration order.

| Migration | Purpose |
| --- | --- |
| `001_harden_tenant_rls_policies.sql` | Rebuild fail-closed tenant RLS policies on existing tenant-owned tables. |
| `002_profile_indexes.sql` | Add tenant-leading profile, contact, and transaction indexes for common activity queries. |
| `003_profile_tracking_identifier_filters.sql` | Backfill anonymous/device identifiers from staged event payloads and index those identifiers by tenant. |
| `004_campaign_experiments.sql` | Create campaign experiment/variant tables with tenant-aware references, constraints, indexes, and RLS. |
| `005_campaign_content_items.sql` | Restore the campaign-to-content relation with tenant-safe foreign keys and RLS. |
| `006_cdp_agent_workflow.sql` | Add the tenant/segment/agent workflow queue, scheduling override, ordered execution constraints, candidate integrity triggers, indexes, and forced tenant RLS. |

`006_cdp_agent_workflow.sql` is for existing installations. A fresh database
already receives `cdp_agent_workflow` from `database-schema.sql`; the migration
is intentionally safe to reapply after that canonical schema. It creates an
empty workflow table and does not seed workflow steps or activate agents.

The candidate-validation triggers verify that referenced content items exist
for the workflow tenant and prevent deletion or tenant/key changes while an
item is referenced. Because the trigger functions run with invoker privileges,
the runtime role needs `SELECT` and `UPDATE` privileges on
`cdp_content_items` to acquire the `FOR SHARE` row locks, plus `SELECT`
privileges on `cdp_agent_workflow`; tenant RLS must remain enabled. Set
`app.tenant_id` for the transaction. Candidate writes
and content deletion/key changes reject `REPEATABLE READ` to avoid stale-snapshot
integrity checks; use `READ COMMITTED` or `SERIALIZABLE` (retry serialization
failures).

Run the workflow regression checks against a **disposable PostgreSQL 16
database** after applying the base schema and AI-agent seed, or after applying
the migration to an existing test database with its required tables and seed
agents:

```bash
psql -v ON_ERROR_STOP=1 -d customer360_test \
    -f customer360-database/tests/cdp_agent_workflow.sql
```

The checks create fixtures and a temporary non-superuser role, exercise
ordering, tenant isolation, candidate protection, and transaction-isolation
guards, then roll back their fixtures. Run them as a test database
administrator; they are not production migration steps.

Review each migration against the target database before production execution.
These migrations can lock tables, build indexes, or alter security policy.
Schedule and monitor production rollout accordingly. Do not rerun the complete
bootstrap sequence against a production database as a substitute for its
ordered migrations.

## Verification Queries

After initialization, verify the schema and RLS context with a non-superuser:

```sql
SELECT current_schema();
SELECT to_regclass('customer360.cdp_master_profiles');
SELECT to_regclass('customer360.crm_connector_config');
SELECT to_regclass('customer360.cdp_agent_workflow');
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relnamespace = 'customer360'::regnamespace
	AND relname IN (
		'cdp_master_profiles',
		'crm_connector_config',
		'cdp_agent_workflow',
		'graph_edges'
	);
SELECT current_setting('app.tenant_id', true);
```

For tenant-facing application queries, set `app.tenant_id` in the same
transaction as the query. Do not rely on a process-global tenant value when
using pooled connections.