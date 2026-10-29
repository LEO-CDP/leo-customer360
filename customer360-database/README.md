# Customer 360 Database

This directory contains the PostgreSQL schema, seed data, materialized views,
and consolidated legacy upgrade for the Customer 360 platform.

The canonical application DDL is [database-schema.sql](database-schema.sql).
It creates the `customer360` schema, required extensions, tenant-scoped tables,
foreign keys, indexes, triggers, graph partitions, connector configuration,
and row-level security policies. Keep application queries and DAO models
aligned with this file; do not infer table or column names from older planning
documents or backups.

## Contents

- [Schema inventory and capability summary](#database-schema-by-platform-capability)
- [Database requirements](#database-requirements) and [installation](#installation-order)
- [Initialization and seeds](#initialization-and-seeds)
- [Detailed schema notes](#detailed-schema-notes)
- [Tenant isolation](#tenant-isolation) and [database behaviors](#important-database-behaviors)
- [Legacy upgrades](#legacy-upgrades) and [verification queries](#verification-queries)

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

For an existing database, run [legacy-upgrade.sql](legacy-upgrade.sql) after
these files. The setup helpers run it automatically; fresh databases skip the
historical steps using the bootstrap marker.

The deployment helper [run-sql.sh](../deployments/postgres/run-sql.sh) applies
that order automatically after the repository's PostgreSQL bootstrap scripts.
It runs `psql` with `ON_ERROR_STOP=1`, so the first failed script stops the deployment.

Example for a local database:

```bash
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/database-schema.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/init-cdp-ai-agents.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/init-core-database.sql
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/data-view-for-llm.sql
# Required for an existing database; harmless and fast for a fresh database.
psql -v ON_ERROR_STOP=1 -d customer360 -f customer360-database/legacy-upgrade.sql
```

`database-schema.sql` is idempotent for normal object creation, but existing
volumes also need the historical data and compatibility upgrades. Back up the
database and review the [legacy upgrade](#legacy-upgrades) before production
execution; it can fail when existing data cannot be safely constrained.

The historical embedding upgrade makes content tags non-null
(converting existing nulls to empty arrays), supports 384- and 768-dimensional
recommendation vectors with size-specific HNSW indexes, adds embedding
source/version metadata, and clears unsupported vector sizes for lazy
regeneration. Product-vector columns are reserved for future direct product
ranking; current ranking uses linked content embeddings.

## Initialization And Seeds

`init-cdp-ai-agents.sql` is the unified AI-agent registry seed. It creates
10 core agents (one per execution type) and two prompt-backed agents for the
notification and segment endpoints. `product_recommendation` and
`eligibility_policy` are active because their runner handlers are implemented;
the remaining new entries are `INACTIVE`. Run it before
`init-core-database.sql`. Profile attributes without a specific registered
producer have `agent_code = NULL`; the seed does not create placeholder agents
to satisfy ownership metadata.

The seed is atomic and insert-only on conflicts: rerunning it preserves deployed
configuration, activation state, feature definitions and append-only prompt
history. It does **not** repair existing rows. Upgrade existing databases using
the consolidated upgrade and the versioned prompt publishing API. Historical
step `013` removes the retired graph agent and model
type from existing databases; it aborts if workflows or profile attributes
still reference that agent. Step `014`
activates the implemented rules-engine handler on existing inactive seed rows
without overriding other operator-managed lifecycle states. Step
`015` removes the semantic-profile seed
and model type; it aborts if workflows or profile attributes still reference
that agent. Step `016` registers `_`
(`Unclassified / Not set`), makes it the default for master/raw profile
domains, and repairs legacy null, empty, or `unknown` profile domains without
reclassifying valid business domains.

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
| Optimization / rules | `NULL` until an engine is selected |
| Generative LLM | `gpt-6-luna` |

Estimator identifiers are documentation for an allow-listed implementation;
never dynamically import code from registry values. No new runtime dependency
is installed by the seed. Hyperparameters mix estimator settings and adapter
metadata; do not pass the entire JSON object to an estimator constructor.
Before activating a template, provision its executor,
pin library versions, register its trained artifact and preprocessing contract,
and validate tenant-isolated outputs. `ai_agents_runners` currently orchestrates
workflow selection; it is not a trained-model inference pipeline.

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

The `eligibility_policy` rules engine evaluates explicit channel consent and
resolved suppressions for active profiles in its configured segment workflow.
It writes a versioned per-channel result to
`communication_preferences.eligibility_policy` without altering the original
opt-in fields. Email and advertising-audience senders independently require
literal-true consent at dispatch time; the policy result is not a substitute
for that final gate and is not a determination of legal compliance. Advertising
requires its own `meta_ads_opt_in`, `google_ads_opt_in`, or `tiktok_ads_opt_in`
boolean; consent for email or another provider is never inferred.

`init-core-database.sql` is the remaining seed/setup script. It:

- creates the default tenant `11111111-1111-1111-1111-111111111111`;
- seeds system domains and default tenant-domain assignments;
- seeds the governed event catalog and profile attribute metadata;
- seeds persona scoring configuration and other reference data; and
- prepares the tenant context used by RLS-protected inserts.

`data-view-for-llm.sql` creates two LLM-oriented materialized views after the
base tables exist:

- `customer360.mv_customer_transactions`;
- `customer360.mv_customer_engagements`.

These are derived projections, not the source of truth. Refresh them after
large profile, transaction, or interaction loads. Use `REFRESH MATERIALIZED
VIEW CONCURRENTLY` only when the required unique indexes and deployment
conditions are present. The combined `mv_customer_360_overview` is mentioned
only as an optional idea in the script; it is not created by it. Materialized
views persist query results and do not inherit source-table RLS at read time:
restrict direct access and enforce tenant isolation in the consuming service.

## Database schema by platform capability

This inventory documents [database-schema.sql](database-schema.sql). It
counts a partitioned parent as one logical table and counts its partitions
separately. Counts describe the checked-in definitions, not the state of a
particular deployed database, which can differ because of migrations.

| Scope | Logical tables | Child partitions | Ordinary views | Materialized views |
| --- | ---: | ---: | ---: | ---: |
| Canonical `customer360` DDL | **71** | **15** | **3** | **0** |
| Separate [`data-view-for-llm.sql`](data-view-for-llm.sql) | 0 | 0 | 0 | 2 |
| Separate [`leo_ads` DDL](../customer360-promotions/sql-scripts/db-schema-init.sql) | 22 | 2 | 1 | 0 |
| **Combined, if all scripts are applied** | **93** | **17** | **4** | **2** |

The canonical DDL therefore declares 86 table relations when the 15 physical
partitions are included. Views, materialized views, indexes, and functions
are not counted as tables.

The following capability counts cover **distinct `customer360` logical tables
explicitly listed in that capability's inventory**, not every table the
capability may use. A table shared by multiple groups is counted once in each
group but once in the 71-table unique total. Consequently, the 75 table
references in the capability rows are not additive. The promotions group
has no tables in the canonical DDL; its independently defined 22 `leo_ads`
tables are shown separately below.

| Capability | `customer360` tables | `customer360` views |
| --- | ---: | ---: |
| [Customer 360](#customer-360) | 11 | 0 |
| [Identity Resolution (CIR)](#identity-resolution-cir) | 5 | 0 |
| [Persona Resolution Engine](#persona-resolution-engine) | 6 | 0 |
| [Identity Graph](#identity-graph) | 3 | 0 |
| [Event Tracking & Web SDK](#event-tracking--web-sdk) | 3 | 0 |
| [Segmentation & IAB Tags](#segmentation--iab-tags) | 4 | 0 |
| [Analytics](#analytics) | 4 | 2 |
| [REST & MCP APIs](#rest--mcp-apis) | 0 | 0 |
| [Multi-tenancy & Security](#multi-tenancy--security) | 6 | 0 |
| [Authentication & RBAC](#authentication--rbac) | 6 | 0 |
| [Admin UI](#admin-ui) | 5 | 1 |
| [Campaign Workflow Activation](#campaign-workflow-activation) | 11 | 0 |
| [Ad Audience Synchronization](#ad-audience-synchronization) | 1 | 0 |
| [AI Campaign Planning](#ai-campaign-planning) | 2 | 0 |
| [Promotions API](#promotions-api) | 0 | 0 |
| [Vector Search & Embeddings](#vector-search--embeddings) | 4 | 0 |
| [Customer Scoring Catalog](#customer-scoring-catalog) | 4 | 0 |

All objects below are in the `customer360` schema unless explicitly marked
otherwise. Object names link to their definitions; consult the SQL for
**all columns, types, defaults, keys, checks, indexes, triggers, and subsequent
`ALTER TABLE` statements**. Constraints and indexes can appear elsewhere in
the file, including the tenant-aware foreign-key and RLS blocks near its end.
The [schema diagrams](full-schema-diagram.md) provide supplementary relationship
visualizations; use the SQL for the current inventory. Product capability
status does not imply that every feature is implemented in PostgreSQL.

### Customer 360

**Inventory: 11 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_master_profiles`](database-schema.sql#L765) | Golden customer record with identity, demographics, consent, lifecycle, tags, and scores. Partial unique indexes enforce per-tenant non-null email and phone uniqueness. |
| [`cdp_domain_profiles`](database-schema.sql#L1020) | Domain-specific attributes and analytics linked to a master profile; the domain-attribute catalog trigger registers new keys. |
| [`cdp_customer_valuation`](database-schema.sql#L4847) | Tenant-, profile-, domain-, date-, and model-scoped customer valuation snapshots. |
| [`crm_customer_contacts`](database-schema.sql#L2636) | Customer interactions associated with master profiles. |
| [`crm_transactions`](database-schema.sql#L2650) | Cross-domain transaction facts; `master_profile_id` can be null until identity resolution. |
| [`crm_lead`](database-schema.sql#L630), [`crm_lead_source`](database-schema.sql#L650) | Leads and their sources. |
| [`crm_contact`](database-schema.sql#L665), [`crm_account`](database-schema.sql#L685), [`crm_opportunity`](database-schema.sql#L702), [`crm_industry`](database-schema.sql#L722) | CRM contacts, accounts, opportunities, and industries. |

The customer timeline is assembled from profiles, interactions, transactions,
and event data; there is no dedicated timeline table in this DDL.

### Identity Resolution (CIR)

**Inventory: 5 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_raw_profiles_stage`](database-schema.sql#L1309) | Inbound raw identities and event-derived data awaiting processing. |
| [`cdp_profile_links`](database-schema.sql#L1449) | Raw-to-master links with matching and unmerge lifecycle metadata. |
| [`cdp_identity_index`](database-schema.sql#L2471) | Normalized identifier lookup for matching. |
| [`cdp_profile_merge_history`](database-schema.sql#L2578) | Source and target snapshots and history of merges. |
| [`cdp_profile_attributes`](database-schema.sql#L2272) | Attribute and matching metadata shared with segmentation and scoring. |

The matching engine and S3 event projections are implemented outside this SQL;
the DDL also enables `pg_trgm` and `fuzzystrmatch` for fuzzy matching.

### Persona Resolution Engine

**Inventory: 6 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_persona_archetypes`](database-schema.sql#L1517) | Tenant/domain archetypes, 768-dimensional centroids, component scores, and matched-profile count. |
| [`cdp_customer_personas`](database-schema.sql#L1562) | Versioned profile-to-archetype assignments and scores. |
| [`cdp_persona_features`](database-schema.sql#L1726) | Input signals for a persona assignment. |
| [`cdp_persona_score_details`](database-schema.sql#L1757) | Component scores, weights, formulas, and explanations. |
| [`cdp_persona_history`](database-schema.sql#L1782) | Persona change history. |
| [`cdp_persona_config`](database-schema.sql#L1812) | Global typed configuration for persona scoring. |

The three persona detail tables are linked through `persona_id` and do not
themselves carry `tenant_id`; access must be scoped through the owning
tenant's persona rather than treated as independently tenant-filtered tables.
The archetype count is maintained by a trigger on persona assignments.

### Identity Graph

**Inventory: 3 logical tables; 15 child partitions; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_relation_types`](database-schema.sql#L2608) | Global vocabulary of profile relationship types. |
| [`cdp_relations`](database-schema.sql#L2617) | Typed relations between master profiles. |
| [`graph_edges`](database-schema.sql#L3260) | General-purpose tenant-scoped edges between typed IDs, list-partitioned by `relation`; the 15 physical partitions are listed under [Graph storage](#graph-storage). |

Raw-to-master identity links also appear in `cdp_profile_links`; not every
graph edge is a declared foreign-key relationship. Query through the parent
table and apply tenant context.

### Event Tracking & Web SDK

**Inventory: 3 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_event_catalog`](database-schema.sql#L1905) | Global cross-domain event vocabulary. |
| [`sys_data_source`](database-schema.sql#L2504) | Tenant inbound source and connector configuration, including sensitive access tokens. |
| [`cdp_raw_profiles_stage`](database-schema.sql#L1309) | Staged event-derived identity and attribution fields for downstream processing. |

The Web SDK, Redis Streams handoff, and S3/MinIO event storage are not tables
in this schema. Tracking endpoints validate and sanitize input before
asynchronous handoff; they do not connect directly to PostgreSQL.

### Segmentation & IAB Tags

**Inventory: 4 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_segments`](database-schema.sql#L3042) | Tenant segment rules, generated SQL, processing metadata, counts, and audience tags. |
| [`cdp_audience_tags`](database-schema.sql#L2983) | Global IAB Audience Taxonomy 1.1 tag hierarchy. |
| [`cdp_agent_workflow`](database-schema.sql#L3102) | Ordered agent configuration and candidate content IDs per tenant and segment. |
| [`crm_segment_sync_runs`](database-schema.sql#L3805) | Segment synchronization execution records. |

The visual query builder, AI drafting, and scheduling are application/backend
features; segment rules and workflow configuration are persisted here.

The core seed creates one default-tenant baseline segment for every
`sys_domain` row, including `_`, with tag `domain_<domain_code>` and a rule
matching profiles in that domain. Re-running the core seed adds segments for
new catalog domains without overwriting existing domain segments. A tag
already assigned to a different domain causes an explicit error. These rows
are not automatically created for other tenants or when a domain is added
between seed runs. Membership counts and profile tags are updated by the
normal segment recomputation job or API, not by inserting the seed rows.
For nonstandard catalog codes, the SQL rule encodes the domain value so it
remains compatible with segment SQL safety checks; the editable JSON rule
retains the original domain code.

To check domain coverage, tenant isolation, and repeatable seeding on a
disposable database initialized with the schema and both seeds:

```bash
psql -v ON_ERROR_STOP=1 -d customer360_test \
    -f customer360-database/tests/domain_segments.sql
```

### Analytics

**Inventory: 4 tables; 2 ordinary views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`crm_campaign_performance_daily`](database-schema.sql#L510) | Daily campaign performance facts. |
| [`vw_campaign_performance_metrics`](database-schema.sql#L544) | Campaign-level spend, funnel metrics, CPA, and ROAS. |
| [`cdp_journey_metric`](database-schema.sql#L4227) | Global journey KPI definitions and business-model applicability. |
| [`cdp_journey_metric_domain`](database-schema.sql#L4301) | General and domain-specific KPI mappings. |
| [`cdp_journey_metric_daily`](database-schema.sql#L4319) | Tenant daily windowed KPI snapshots, including dimensions and currency. |
| [`vw_journey_stage_report`](database-schema.sql#L4795) | Invoker-security view comparing current and prior journey KPI windows. |

`refresh_journey_metrics_pg` refreshes supported PostgreSQL-sourced metrics;
event-lake metrics are populated separately. The two materialized
projections from [data-view-for-llm.sql](data-view-for-llm.sql) are described
above and are not defined by the canonical DDL. S3 log aggregation and
device/channel extraction are backend processing, not separate base tables.

### REST & MCP APIs

**Inventory: 0 dedicated tables; 0 dedicated views.**

There are no REST- or MCP-specific tables in `database-schema.sql`. The APIs
access the relevant tenant-scoped objects above through their services. API
authorization and tenant context remain required; a database object inventory
does not describe endpoint or tool availability.

### Multi-tenancy & Security

**Inventory: 6 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`sys_tenant`](database-schema.sql#L49) | Tenant identity, status, industry, and B2B/B2C/B2B2C business model. |
| [`sys_schema_upgrade_history`](database-schema.sql#L28) | Global operational marker recording completion of the consolidated historical upgrade. |
| [`sys_domain`](database-schema.sql#L100) | Global business-domain dictionary, including `_` for unclassified profiles. |
| [`sys_tenant_domain`](database-schema.sql#L138) | Tenant-to-domain assignments. |
| [`sys_organization`](database-schema.sql#L174) | Tenant organization hierarchy. |
| [`sys_audit_log`](database-schema.sql#L371) | Tenant audit records, actors, actions, and request/result metadata. |

The canonical DDL enables and forces RLS on its explicit list of tenant tables
and on recommendation tables; see [Tenant Isolation](#tenant-isolation) for
important exceptions and access requirements. `sys_tenant` and `sys_domain`
are not included in that policy list.

### Authentication & RBAC

**Inventory: 6 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`sys_user`](database-schema.sql#L198) | Tenant application user and optional organization membership. |
| [`sys_userinfo`](database-schema.sql#L258) | Local and external authentication identities linked to users. |
| [`sys_role`](database-schema.sql#L316) | Tenant roles. |
| [`sys_permission`](database-schema.sql#L334) | Global permission dictionary. |
| [`sys_role_permission`](database-schema.sql#L346) | Role-to-permission assignments. |
| [`sys_user_role`](database-schema.sql#L355) | Tenant-scoped user-to-role assignments. |

Keycloak itself and bearer-token issuance are external to this DDL.
`sys_role_permission` has no `tenant_id`; scope access via its owning role.

### Admin UI

**Inventory: 5 tables; 1 ordinary view.**

| Object | Purpose and key relationships |
| --- | --- |
| [`sys_assistant_message`](database-schema.sql#L5197) | Tenant-scoped assistant conversation messages and optional customer context. |
| [`crm_flow`](database-schema.sql#L5255), [`crm_flow_version`](database-schema.sql#L5268) | Workflow identity and versioned snapshots edited through the administration UI. |
| [`crm_flow_node`](database-schema.sql#L5380), [`crm_flow_edge`](database-schema.sql#L5456) | Persisted workflow graph projections. |
| [`crm_vw_flow_node`](database-schema.sql#L5539) | Readable workflow-node projection. |

Administration also uses the user, role, tenant, catalog, campaign, and
segment tables documented in their respective sections. The UI itself and
the docs assistant's search index are not created by this DDL.

### Campaign Workflow Activation

**Inventory: 11 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`crm_campaign`](database-schema.sql#L434) | Campaign definition, status, channel, journey stage, and workflow association. |
| [`crm_campaign_member`](database-schema.sql#L612) | Campaign membership. |
| [`crm_message_templates`](database-schema.sql#L3624) | Reusable outbound message templates. |
| [`crm_campaign_reviews`](database-schema.sql#L3241) | Campaign review records. |
| [`crm_campaign_experiments`](database-schema.sql#L3673), [`crm_campaign_experiment_variants`](database-schema.sql#L3692) | Experiment definitions and variants linked to campaigns, segments, and templates. |
| [`crm_campaign_content_items`](database-schema.sql#L3784) | Campaign-to-content associations. |
| [`crm_campaign_workflow_action_runs`](database-schema.sql#L3882) | Per-profile workflow action execution/idempotency records. |
| [`cdp_campaign_dispatch_logs`](database-schema.sql#L3956) | Outbound per-recipient dispatch ledger. |
| [`crm_connector_config`](database-schema.sql#L3987) | Tenant outbound provider configuration; `credentials` can contain secrets. |
| [`crm_suppression_list`](database-schema.sql#L4037) | Global or campaign-scoped suppression records and protected time windows. |

Workflow versions, nodes, and edges are listed under [Admin UI](#admin-ui).
The `crm_save_flow`, `crm_review_flow_version`, `crm_publish_flow`,
`crm_load_flow`, and `crm_node_json` routines manage workflow snapshots,
review, publication, and reconstruction. Approval and channel execution are
also enforced by application services; database objects alone do not send
messages.

### Ad Audience Synchronization

**Inventory: 1 table; 0 views.**

[`crm_ad_sync_runs`](database-schema.sql#L3835) records tenant-scoped,
idempotent audience synchronization runs. Segments, profiles, consent, and
suppression data provide the inputs; hashing and provider uploads occur in
the activation service, not in this DDL.

### AI Campaign Planning

**Inventory: 2 tables; 0 views.**

[`crm_campaign_agent`](database-schema.sql#L4194) assigns agents and
guardrails to campaigns. [`cdp_ai_agents`](database-schema.sql#L1998) holds
the shared agent registry and prompt version history; campaign reviews,
message templates, and content links are documented above. The dedicated
agent service performs planning; registry rows do not themselves execute it.

### Promotions API

**Inventory: 0 `customer360` tables; separately, 22 `leo_ads` logical
tables, 2 child partitions, and 1 ordinary view.**

The `leo_ads` schema is **not** created by `database-schema.sql`. Its separate
[promotions DDL](../customer360-promotions/sql-scripts/db-schema-init.sql)
defines the following objects:

| `leo_ads` objects | Purpose |
| --- | --- |
| [`tenant`](../customer360-promotions/sql-scripts/db-schema-init.sql#L37) | Promotions tenant and settings. |
| [`source_type`](../customer360-promotions/sql-scripts/db-schema-init.sql#L54), [`render_type`](../customer360-promotions/sql-scripts/db-schema-init.sql#L64), [`destination_type`](../customer360-promotions/sql-scripts/db-schema-init.sql#L77) | Source, render, and destination dictionaries. |
| [`advertiser`](../customer360-promotions/sql-scripts/db-schema-init.sql#L94), [`source_account`](../customer360-promotions/sql-scripts/db-schema-init.sql#L108), [`source_asset`](../customer360-promotions/sql-scripts/db-schema-init.sql#L129) | Advertisers and external source accounts and assets. |
| [`placement`](../customer360-promotions/sql-scripts/db-schema-init.sql#L153), [`placement_format`](../customer360-promotions/sql-scripts/db-schema-init.sql#L171) | Inventory placements and supported formats. |
| [`campaign`](../customer360-promotions/sql-scripts/db-schema-init.sql#L193), [`creative`](../customer360-promotions/sql-scripts/db-schema-init.sql#L224), [`creative_render`](../customer360-promotions/sql-scripts/db-schema-init.sql#L266), [`creative_item`](../customer360-promotions/sql-scripts/db-schema-init.sql#L322) | Campaigns, creative content, renders, and items. |
| [`destination`](../customer360-promotions/sql-scripts/db-schema-init.sql#L286), [`tracking_endpoint`](../customer360-promotions/sql-scripts/db-schema-init.sql#L298) | Creative destinations and tracking endpoints. |
| [`ad`](../customer360-promotions/sql-scripts/db-schema-init.sql#L349), [`targeting_rule`](../customer360-promotions/sql-scripts/db-schema-init.sql#L377), [`audience`](../customer360-promotions/sql-scripts/db-schema-init.sql#L402), [`ad_audience`](../customer360-promotions/sql-scripts/db-schema-init.sql#L419), [`placement_ad`](../customer360-promotions/sql-scripts/db-schema-init.sql#L436) | Ads, targeting, audience associations, and placement associations. |
| [`user_serving_key`](../customer360-promotions/sql-scripts/db-schema-init.sql#L457) | Serving-time user identifiers. |
| [`ad_event`](../customer360-promotions/sql-scripts/db-schema-init.sql#L475) | Partitioned ad telemetry, with [`ad_event_2026_08`](../customer360-promotions/sql-scripts/db-schema-init.sql#L498) and [`ad_event_default`](../customer360-promotions/sql-scripts/db-schema-init.sql#L502) child partitions. |
| [`v_active_ads`](../customer360-promotions/sql-scripts/db-schema-init.sql#L622) | Ordinary view of active ads. |

See the [promotions schema documentation](../customer360-promotions/sql-scripts/README.md)
for details. This schema has its own tenant model and no foreign keys into
`customer360`; do not assume its tenant IDs or RLS policies are shared with
`customer360`.

### Vector Search & Embeddings

**Inventory: 4 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_content_items`](database-schema.sql#L2708) | Tenant content candidates and 384- or 768-dimensional embeddings, with dimension-specific HNSW indexes. |
| [`cdp_product_items`](database-schema.sql#L2813) | Tenant product catalog with optional content linkage and reserved 384- or 768-dimensional embeddings. |
| [`cdp_profile_recommendation_runs`](database-schema.sql#L5009) | Tenant/segment recommendation run metadata. |
| [`cdp_profile_recommendations`](database-schema.sql#L5027) | Ranked per-profile content candidates, scores, and explanations. |

Persona archetype centroids use `vector(768)` and IVFFlat; campaign, CRM,
and graph embeddings include `vector(1536)`. Product-vector columns are
reserved for future direct product ranking; current ranking uses linked
content embeddings. Triggers invalidate stale content and product embeddings
when their source data changes.

### Customer Scoring Catalog

**Inventory: 4 tables; 0 views.**

| Object | Purpose and key relationships |
| --- | --- |
| [`cdp_ai_feature_catalog`](database-schema.sql#L1949) | Global allow-listed feature definitions and derivation guidance. |
| [`cdp_ai_agents`](database-schema.sql#L1998) | Global registry of scoring models, rule engines, and task agents. |
| [`cdp_profile_attributes`](database-schema.sql#L2272) | Governed profile attributes and producer/matching metadata. |
| [`cdp_master_profiles`](database-schema.sql#L765) | Persisted customer scores and lifecycle attributes. |

These are catalog and storage foundations, not an automated ML training
pipeline. Activation of an agent requires an implemented executor and an
appropriate model/data contract; see [Model and feature readiness](#model-and-feature-readiness).

## Detailed Schema Notes

### System, tenants, and access control

- `sys_tenant`, `sys_domain`, and `sys_tenant_domain` define tenants and the
	system business-domain vocabulary. `sys_tenant.business_type` stores the
	tenant's industry/vertical (such as `retail`), while
	`sys_tenant.business_model` stores the go-to-market model (`B2B`, `B2C`,
	or `B2B2C`, defaulting to `B2C`). `sys_domain` includes `_` for
	unclassified profiles alongside vertical domain codes.
- `sys_organization` stores tenant organization trees.
- `sys_user` stores internal application users. SSO identities and local
	credentials are separated into `sys_userinfo`.
- `sys_role`, `sys_permission`, `sys_role_permission`, and `sys_user_role`
	implement tenant RBAC with a global permission dictionary.
- `sys_audit_log` stores action, authentication, request, before/after, and
	result information for compliance reporting.
- `sys_data_source` stores inbound data-source and ingestion metadata. Its
	`access_tokens` JSONB belongs to inbound collection connectors.
- `sys_assistant_message` stores tenant-scoped conversational memory rows for
	the LEO Assistant.

### CRM, campaigns, workflows, and outbound activation

- `crm_campaign` and `crm_campaign_performance_daily` store campaigns and
	daily omnichannel performance. `crm_campaign.journey_stage` (`awareness`,
	`acquisition`, `activation`, `engagement`, `conversion`, `retention`,
	`loyalty`, `churned`) places each campaign on the customer journey map, and
	`vw_campaign_performance_metrics` exposes aggregate spend, funnel rates,
	CPA, and ROAS.
- `crm_campaign_agent` assigns AI agents (`cdp_ai_agents`) to a campaign with a
	specific `role` (`plan`, `decide`, `optimize`), `autonomy` (`suggest`,
	`approve`), optional `pinned_instruction_version`, and JSONB `guardrails`,
	enforced by a composite tenant-campaign foreign key and forced tenant RLS.
- `crm_flow`, `crm_flow_version`, `crm_flow_node`, `crm_flow_edge`, and
	`crm_vw_flow_node` store versioned, immutable customer journey workflow
	trees. `crm_flow_node` supports trigger, branching, wait, outbound, sync,
	and AI canvas nodes (`ai_decide`, `ai_generate`, `ai_recommend`) with a
	stored generated `agent_code` column (`config ->> 'agent_code'`) referencing
	`cdp_ai_agents(agent_code)`. Helper functions (`crm_save_flow`,
	`crm_review_flow_version`, `crm_publish_flow`, `crm_load_flow`, and
	`crm_node_json`) manage snapshot creation, human review, publishing, and
	JSON reconstruction.
- `crm_campaign_workflow_action_runs` is the per-profile, per-execution
	idempotency ledger for workflow action nodes.
- `crm_ad_sync_runs` records tenant-scoped, idempotent audience uploads to
	external ad platforms (`meta_ads`, `google_ads`, `tiktok_ads`).
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

### Customer 360 profiles, valuation, and identity resolution

- `cdp_master_profiles` is the golden profile per tenant and domain. It holds
	identity, demographics, channel identifiers, consent, lifecycle state,
	persona labels, standardized `audience_tags`, lineage, and ML/CX scores.
- `cdp_domain_profiles` stores domain-specific attributes and analytics as
	validated JSONB objects, one row per master profile and domain.
- `cdp_customer_valuation` stores reproducible discounted contribution-value
	and CLV snapshots per tenant, master profile, `sys_domain`, `as_of_date`,
	and model version.
- `cdp_raw_profiles_stage` is the inbound landing area before Customer
	Identity Resolution (CIR).
- `cdp_profile_links` records raw-to-master matches, scores, methods, and
	unmerge lifecycle state.
- `cdp_identity_index` provides indexed normalized identifier lookup for
	streaming matching.
- `cdp_profile_merge_history` preserves source and target snapshots for
	master-profile merges and later unmerge analysis.
- `cdp_relations` and `cdp_relation_types` model typed profile-to-profile
	relationships.

### Personas, metadata, recommendations, and scoring

- `cdp_persona_archetypes` stores reusable tenant/domain persona definitions,
	embeddings, centroids, and matched-profile counts.
- `cdp_customer_personas` stores versioned profile-to-archetype assignments.
- `cdp_persona_features`, `cdp_persona_score_details`, and
	`cdp_persona_history` provide explainability and assignment history.
- `cdp_persona_config` stores typed runtime scoring thresholds and weights.
- `cdp_profile_attributes` is the governed attribute catalog used by CIR,
	segmentation, and scoring metadata.
- `cdp_audience_tags` stores the IAB Audience Taxonomy 1.1 hierarchy used on
	profiles and segments.
- `cdp_ai_agents` is the single registry for scoring models, rule engines, and
	task-oriented agents. It stores `journey_stage` (`awareness`..`churned`, or
	`NULL` for cross-stage agents), current prompt instructions, and the
	append-only `prompt_versions` JSONB history used by `customer360-agent`.
- `cdp_ai_feature_catalog` is the allow-listed contract behind
	`cdp_ai_agents.input_features`. Each feature has reviewed SQL and pandas
	derivation guidance over tenant-scoped profile, S3 event, transaction,
	contact, graph, candidate, or runtime data.
- `cdp_event_catalog` defines the cross-domain event vocabulary.
- `cdp_content_items` and `cdp_product_items` store personalized content and
	product catalog items (including vector embeddings and tenant-scoped
	`ext_attributes`).
- `cdp_profile_recommendation_runs` and `cdp_profile_recommendations` store
	tenant-scoped, segment-aware recommendation execution metadata and per-profile
	ranked content candidates.
- `cdp_segments` stores audience rules, generated SQL, processing source, and
	computed membership counts.
- `cdp_agent_workflow` links the global `cdp_ai_agents` registry to tenant-owned
	segments in a many-to-many relationship. Each row configures one agent for
	one segment, with a positive `execution_order`, `is_active`, JSONB
	`configuration`, and a `candidate_content_item_ids` list referencing
	`cdp_content_items` (including products). Candidate IDs must be distinct,
	exist, and belong to the same tenant; referenced items cannot be deleted or
	have their tenant/ID changed until removed from all workflow lists.

### Customer journey reporting

- `cdp_journey_metric` is the global KPI catalog across the 8 journey stages
	(`awareness`, `acquisition`, `activation`, `engagement`, `conversion`,
	`retention`, `loyalty`, `churned`, or `NULL` for cross-stage campaign metrics). Each
	row declares `unit` (`count`, `percent`, `ratio`, `currency`), `source`
	(`event_lake`, `profile`, `transaction`, `campaign`),
	`applicable_business_models` (`B2B`, `B2C`, `B2B2C`), and a JSONB `rule`.
- `cdp_journey_metric_domain` maps each KPI to its allowed domain scopes:
	`domain_code IS NULL` enables the general (cross-domain) KPI, and a
	non-null `domain_code` referencing `sys_domain` enables a domain-specific
	KPI.
- `cdp_journey_metric_daily` stores tenant-scoped daily trailing-window KPI
	snapshots (`window_days` in `1`, `7`, `30`, `90`) keyed by
	`(tenant_id, report_date, window_days, business_model, domain_code, journey_stage, metric_code, dim_type, dim_value, currency)`.
	Supported `dim_type` values are `all`, `channel`, `campaign`, `segment`,
	and `agent`. Campaign-derived metrics carry an ISO 4217 `currency` (`UNK`
	when unset) so multi-currency figures are never summed together.
- `refresh_journey_metrics_pg(p_tenant, p_date)` refreshes PostgreSQL-sourced
	general and domain-scoped transaction/profile KPIs plus campaign and active
	assigned-agent (`crm_campaign_agent`) rollups for the tenant's
	`business_model` (default `B2C`), while leaving event-lake KPI snapshots
	untouched. Callers must set transaction-local `app.tenant_id` equal to
	`p_tenant`.
- `vw_journey_stage_report` (`security_invoker = true`) joins current and
	prior-window snapshots with `cdp_journey_metric` and
	`cdp_journey_metric_domain` to expose `value`, `previous_value`, `change`,
	`change_kind` (`points` for percent metrics, `percent` otherwise),
	`currency`, `business_model`, and `domain_code`.

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

Tenant isolation relies on both application authorization and database
controls:

1. Many tenant-owned tables carry a non-null `tenant_id` and have forced RLS.
	 The schema's explicit policy list and separately defined recommendation
	 policies are the authoritative coverage, not a blanket policy for every
	 object.
2. Composite tenant-aware foreign keys are added for listed relationships near
	 the end of `database-schema.sql`. Do not assume every relationship is
	 protected by a composite foreign key.

Global catalogs and tenant-related tables without `tenant_id`, including
`sys_role_permission` and the persona detail tables, are not independently
tenant-filtered by those policies. Scope access through the authorized
parent and restrict direct privileges. Materialized views likewise require
separate access controls, and direct access to graph partitions must not be
assumed to inherit the parent table's RLS policy. Ordinary view security
depends on the view definition and PostgreSQL privileges.

Every pooled connection used by a tenant-facing service must set the tenant
before querying:

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

## Legacy Upgrades

The consolidated [legacy-upgrade.sql](legacy-upgrade.sql) retains the
historical steps through `018`, in order, for existing installations.
Run it with `psql -v ON_ERROR_STOP=1` after the canonical schema, both seeds,
and materialized views. A session advisory lock serializes concurrent upgrade
runs. On success it records `legacy_through_018` in the global
`sys_schema_upgrade_history` table; subsequent runs skip the historical steps.
Fresh schemas record that marker at bootstrap because their DDL and seeds
already contain the current definitions. Restrict writes to the history table
to the database setup role.

The historical steps contain their original transaction boundaries, so a failed
run may have committed earlier steps but does not record completion. Resolve
the reported error and rerun the consolidated script. Tenant data backfills run
with explicit tenant context under forced RLS. Review and back up existing
databases first: upgrades can lock tables, build indexes, modify legacy data,
or reject incompatible data. Do not delete the marker to force a rerun without
reviewing the effects. The setup helper retains `--skip-migrations` as an alias
for `--skip-legacy-upgrade`; skipping is unsafe for an unupgraded database.

| Historical step | Purpose |
| --- | --- |
| `001_harden_tenant_rls_policies.sql` | Rebuild fail-closed tenant RLS policies on existing tenant-owned tables. |
| `002_profile_indexes.sql` | Add tenant-leading profile, contact, and transaction indexes for common activity queries. |
| `003_profile_tracking_identifier_filters.sql` | Backfill anonymous/device identifiers from staged event payloads and index those identifiers by tenant. |
| `004_campaign_experiments.sql` | Create campaign experiment/variant tables with tenant-aware references, constraints, indexes, and RLS. |
| `005_campaign_content_items.sql` | Restore the campaign-to-content relation with tenant-safe foreign keys and RLS. |
| `006_cdp_agent_workflow.sql` | Add the tenant/segment/agent workflow queue, scheduling override, ordered execution constraints, candidate integrity triggers, indexes, and forced tenant RLS. |
| `007_cdp_product_items.sql` | Add tenant-scoped product items (`cdp_product_items`) with `ext_attributes` JSONB and optional link to `cdp_content_items`. |
| `008_content_embedding_contract.sql` | Enforce non-null content tags, add embedding metadata, and support 384/768-dimensional HNSW vector indexes. |
| `009_assistant_messages.sql` | Add tenant-scoped conversational memory (`sys_assistant_message`) with composite tenant foreign keys and RLS. |
| `010_campaign_workflow_link.sql` | Link campaigns to immutable customer journey workflow versions (`crm_flow`, `crm_flow_version`, `crm_flow_node`, `crm_flow_edge`) and review/publish functions. |
| `011_cdp_audience_tags.sql` | Add the IAB Audience Taxonomy 1.1 catalog (`cdp_audience_tags`) and `audience_tags` arrays on profiles and segments. |
| `012_cdp_customer_valuation.sql` | Add tenant- and domain-scoped CLV / discounted contribution-value snapshots (`cdp_customer_valuation`) with forced RLS. |
| `013_remove_graph_ml_agent_type.sql` | Remove the retired `graph_ml` model type and `identity_graph_intelligence` seed if unreferenced. |
| `014_activate_eligibility_policy.sql` | Activate the `eligibility_policy` rules-engine seed where still in its default inactive state. |
| `015_remove_semantic_embedding_agent_type.sql` | Remove the retired `semantic_embedding` model type and `customer_semantic_profile` seed if unreferenced. |
| `016_add_unclassified_profile_domain.sql` | Register the `_` (`Unclassified / Not set`) domain and backfill legacy blank/unknown profile domains. |
| `017_agent_campagn_journey.sql` | Add campaign and agent journey stages, AI workflow canvas node types (`ai_decide`, `ai_generate`, `ai_recommend`), and tenant-scoped campaign-agent assignments (`crm_campaign_agent`). |
| `018_journey_report.sql` | Add journey KPI definitions (`cdp_journey_metric`, `cdp_journey_metric_domain`), tenant-scoped daily snapshots (`cdp_journey_metric_daily`), nightly PostgreSQL refresh (`refresh_journey_metrics_pg`), and `vw_journey_stage_report` supporting B2B/B2C/B2B2C (default `B2C`), `sys_domain` scopes, campaign/agent attribution, and currency separation. |

`cdp_journey_metric_domain` controls whether a KPI is general (`domain_code IS
NULL`) or applies to a specific `sys_domain`. The PostgreSQL refresh currently
writes general and explicitly mapped domain-scoped transaction aggregates and
churn-risk snapshots. Domain metrics require an active `sys_domain`, an enabled
metric/domain mapping, and a tenant business model the metric supports (`B2C`
by default). Campaigns do not yet carry a domain, so campaign totals remain
general. Do not infer a profile's business model from its domain. Other
source-job metrics are left untouched by the PostgreSQL refresh.

The historical workflow step is for existing installations. A fresh database
already receives `cdp_agent_workflow` from `database-schema.sql`; the step
creates an empty workflow table and does not seed steps or activate agents.

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
the upgrade to an existing test database with its required tables and seed
agents:

```bash
psql -v ON_ERROR_STOP=1 -d customer360_test \
    -f customer360-database/tests/cdp_agent_workflow.sql
```

The checks create fixtures and a temporary non-superuser role, exercise
ordering, tenant isolation, candidate protection, and transaction-isolation
guards, then roll back their fixtures. Run them as a test database
administrator; they are not production migration steps.

For the fresh bootstrap marker, tenant backfills, and repeat-run regression,
run the following against a **separate disposable database** after the schema
and both seeds. This test changes schema and data and does not roll them back:

```bash
psql -v ON_ERROR_STOP=1 -d customer360_test \
    -f customer360-database/tests/legacy_upgrade.sql
```

## Verification Queries

After initialization, verify the schema and RLS context with a non-superuser:

```sql
SELECT current_schema();
SELECT to_regclass('customer360.cdp_master_profiles');
SELECT to_regclass('customer360.crm_connector_config');
SELECT to_regclass('customer360.cdp_agent_workflow');
SELECT to_regclass('customer360.crm_campaign_agent');
SELECT to_regclass('customer360.cdp_journey_metric');
SELECT to_regclass('customer360.cdp_journey_metric_domain');
SELECT to_regclass('customer360.cdp_journey_metric_daily');
SELECT to_regclass('customer360.vw_journey_stage_report');
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relnamespace = 'customer360'::regnamespace
	AND relname IN (
		'cdp_master_profiles',
		'crm_connector_config',
		'cdp_agent_workflow',
		'crm_campaign_agent',
		'cdp_journey_metric_daily',
		'graph_edges'
	);
SELECT current_setting('app.tenant_id', true);
```

For tenant-facing application queries, set `app.tenant_id` in the same
transaction as the query. Do not rely on a process-global tenant value when
using pooled connections.