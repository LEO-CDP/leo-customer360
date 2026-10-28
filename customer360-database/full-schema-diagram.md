# Customer 360 database schema

`database-schema.sql` is the source of truth for this diagram. The diagrams below cover its 62 base tables, the 15 partitions of `graph_edges`, and both views. They show primary keys, tenant keys, important identifying columns, and declared foreign-key relationships; types are abbreviated for readability. They are not a substitute for the SQL definitions, which contain every exact type, column, check, index, trigger, and function.

All names are exact table or view names in the `customer360` schema. Where a relationship is tenant-scoped, the SQL uses a composite foreign key containing `tenant_id`; the diagram draws that relationship once rather than repeating `tenant_id` as a separate edge to `sys_tenant` in every subject area. Global dictionaries/catalogs that are not tenant-owned do not have a tenant edge.

## 1. Tenant, organization, users, and access control

```mermaid
erDiagram
    sys_tenant {
        uuid tenant_id PK
        varchar tenant_code UK
        text tenant_name
        varchar status
        jsonb metadata
    }
    sys_domain {
        uuid domain_id PK
        varchar domain_code UK
        varchar domain_name
        boolean is_active
    }
    sys_tenant_domain {
        uuid tenant_id PK, FK
        uuid domain_id PK, FK
        boolean is_default
        boolean is_active
    }
    sys_organization {
        uuid organization_id PK
        uuid tenant_id FK
        uuid parent_organization_id FK
        varchar organization_code
        varchar organization_type
    }
    sys_user {
        uuid user_id PK
        uuid tenant_id FK
        uuid organization_id FK
        varchar username
        varchar email
        varchar status
    }
    sys_userinfo {
        uuid userinfo_id PK
        uuid tenant_id FK
        uuid user_id FK
        text provider
        text provider_user_id
    }
    sys_role {
        uuid role_id PK
        uuid tenant_id FK
        varchar role_code
        varchar role_name
    }
    sys_permission {
        uuid permission_id PK
        varchar permission_code UK
        varchar permission_name
    }
    sys_role_permission {
        uuid role_id PK, FK
        uuid permission_id PK, FK
    }
    sys_user_role {
        uuid tenant_id PK, FK
        uuid user_id PK, FK
        uuid role_id PK, FK
    }
    sys_audit_log {
        uuid audit_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid organization_id FK
        text resource_type
        text resource_id
        jsonb before_data
        jsonb after_data
    }
    sys_data_source {
        uuid data_source_id PK
        uuid tenant_id FK
        text source_name
        text source_type
        jsonb configuration
    }
    sys_assistant_message {
        uuid message_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid master_profile_id FK
        uuid conversation_id
        text role
        text message_text
    }
    cdp_master_profiles {
        uuid master_profile_id PK
        uuid tenant_id FK
    }

    sys_tenant ||--o{ sys_tenant_domain : enables
    sys_domain ||--o{ sys_tenant_domain : assigned_to
    sys_tenant ||--o{ sys_organization : owns
    sys_organization o|--o{ sys_organization : parent_of
    sys_organization o|--o{ sys_user : contains
    sys_tenant ||--o{ sys_user : owns
    sys_user ||--o{ sys_userinfo : has_identity
    sys_tenant ||--o{ sys_role : owns
    sys_user ||--o{ sys_user_role : assigned
    sys_role ||--o{ sys_user_role : grants
    sys_role ||--o{ sys_role_permission : includes
    sys_permission ||--o{ sys_role_permission : assigned
    sys_tenant ||--o{ sys_audit_log : records
    sys_user o|--o{ sys_audit_log : actor
    sys_organization o|--o{ sys_audit_log : scope
    sys_tenant ||--o{ sys_data_source : configures
    sys_user ||--o{ sys_assistant_message : authors
    cdp_master_profiles o|--o{ sys_assistant_message : context
```

`sys_permission` is global. `sys_domain` is a global dictionary; `sys_tenant_domain` associates domains with tenants. User-to-role, role-to-permission, and tenant-domain associations use composite primary keys.

## 2. CRM sales entities

```mermaid
erDiagram
    sys_user {
        uuid user_id PK
        uuid tenant_id FK
    }
    crm_lead_source {
        uuid lead_source_id PK
        uuid tenant_id FK
        uuid user_id FK
        text name
    }
    crm_lead {
        uuid lead_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid lead_source_id FK
        text email
    }
    crm_industry {
        uuid industry_id PK
        uuid tenant_id FK
        uuid user_id FK
        text name
    }
    crm_account {
        uuid account_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid industry_id FK
        text name
    }
    crm_contact {
        uuid contact_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid account_id FK
        text email
    }
    crm_opportunity {
        uuid opportunity_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid account_id FK
        numeric amount
    }
    crm_campaign {
        uuid campaign_id PK
        uuid tenant_id FK
    }
    crm_campaign_member {
        uuid campaign_member_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid user_id FK
        uuid contact_id FK
    }

    sys_user ||--o{ crm_lead_source : owns
    sys_user ||--o{ crm_lead : owns
    crm_lead_source o|--o{ crm_lead : source
    sys_user ||--o{ crm_industry : owns
    sys_user ||--o{ crm_account : owns
    crm_industry o|--o{ crm_account : classifies
    crm_account o|--o{ crm_contact : has
    sys_user ||--o{ crm_contact : owns
    crm_account o|--o{ crm_opportunity : has
    sys_user ||--o{ crm_opportunity : owns
    crm_campaign o|--o{ crm_campaign_member : includes
    sys_user o|--o{ crm_campaign_member : manages
    crm_contact o|--o{ crm_campaign_member : participates
```

Tenant-consistent composite foreign keys in the SQL reinforce the CRM references shown above. Nullable owners, account links, and lead sources remain optional.

## 3. CDP profiles and identity resolution

```mermaid
erDiagram
    sys_tenant {
        uuid tenant_id PK
    }
    sys_user {
        uuid user_id PK
        uuid tenant_id FK
    }
    sys_domain {
        uuid domain_id PK
    }
    cdp_master_profiles {
        uuid master_profile_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid current_persona_id FK
        text primary_email
        text phone_number
        jsonb domain_attributes
        jsonb segmentation_tags
    }
    cdp_domain_profiles {
        uuid domain_profile_id PK
        uuid tenant_id FK
        uuid master_profile_id FK
        uuid domain_id FK
        jsonb attributes
    }
    cdp_raw_profiles_stage {
        uuid raw_profile_id PK
        uuid tenant_id FK
        uuid user_id FK
        text source_system
        text source_record_id
        smallint status_code
    }
    cdp_profile_links {
        uuid link_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid raw_profile_id FK
        uuid master_profile_id FK
        uuid unlinked_by FK
    }
    cdp_identity_index {
        uuid identity_index_id PK
        uuid tenant_id FK
        uuid master_profile_id FK
        varchar identifier_type
        text identifier_value_normalized
    }
    cdp_profile_merge_history {
        uuid merge_id PK
        uuid tenant_id FK
        uuid target_master_profile_id FK
        uuid source_master_profile_id
        uuid merged_by FK
        jsonb source_profile_snapshot
        jsonb target_profile_snapshot
    }
    cdp_relation_types {
        serial relation_type_id PK
        text code UK
    }
    cdp_relations {
        uuid relation_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid source_master_id FK
        uuid target_master_id FK
        int relation_type_id FK
    }
    crm_customer_contacts {
        uuid contact_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid master_profile_id FK
        text contact_type
        text contact_channel
    }
    crm_transactions {
        uuid transaction_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid master_profile_id FK
        text source_transaction_id
        numeric transaction_amount
    }
    cdp_customer_personas {
        uuid persona_id PK
        uuid tenant_id FK
        uuid master_profile_id FK
    }

    sys_tenant ||--o{ cdp_master_profiles : owns
    sys_user o|--o{ cdp_master_profiles : owner
    cdp_master_profiles ||--o{ cdp_domain_profiles : has_domain_record
    sys_domain ||--o{ cdp_domain_profiles : scopes
    sys_tenant ||--o{ cdp_raw_profiles_stage : stages
    sys_user o|--o{ cdp_raw_profiles_stage : submitted_by
    cdp_raw_profiles_stage ||--o{ cdp_profile_links : linked
    cdp_master_profiles ||--o{ cdp_profile_links : resolves_to
    sys_user o|--o{ cdp_profile_links : manages
    cdp_master_profiles ||--o{ cdp_identity_index : indexed_by
    cdp_master_profiles ||--o{ cdp_profile_merge_history : retained_target
    sys_user o|--o{ cdp_profile_merge_history : merged_by
    cdp_master_profiles ||--o{ cdp_relations : source_profile
    cdp_master_profiles ||--o{ cdp_relations : target_profile
    cdp_relation_types ||--o{ cdp_relations : types
    cdp_master_profiles ||--o{ crm_customer_contacts : interaction
    cdp_master_profiles o|--o{ crm_transactions : transaction
    cdp_customer_personas o|--o| cdp_master_profiles : current_persona
```

`source_master_profile_id` in `cdp_profile_merge_history` is intentionally not a foreign key: the merged source profile may no longer exist. `cdp_master_profiles` and `cdp_raw_profiles_stage` are wide ingestion/identity records; only relationship and identifying fields are expanded here.

## 4. Personas and AI metadata

```mermaid
erDiagram
    sys_tenant {
        uuid tenant_id PK
    }
    sys_user {
        uuid user_id PK
        uuid tenant_id FK
    }
    cdp_master_profiles {
        uuid master_profile_id PK
        uuid tenant_id FK
        uuid current_persona_id FK
    }
    cdp_persona_archetypes {
        uuid persona_archetype_id PK
        uuid tenant_id FK
        text domain
        varchar persona_code
        vector persona_embedding
        int matched_profile_count
    }
    cdp_customer_personas {
        uuid persona_id PK
        uuid tenant_id FK
        uuid master_profile_id FK
        uuid persona_archetype_id FK
        integer computed_version
        boolean is_active
        numeric persona_score
    }
    cdp_persona_features {
        uuid feature_id PK
        uuid persona_id FK
        varchar feature_code
        numeric numeric_value
        text text_value
    }
    cdp_persona_score_details {
        uuid score_id PK
        uuid persona_id FK
        varchar score_type
        numeric score_value
    }
    cdp_persona_history {
        uuid history_id PK
        uuid persona_id FK
        numeric old_score
        numeric new_score
        timestamptz changed_at
    }
    cdp_persona_config {
        varchar config_key PK
        text config_value
        varchar data_type
    }
    cdp_event_catalog {
        uuid id PK
        text event_name UK
        text event_category
        text domain_scope
    }
    cdp_ai_feature_catalog {
        varchar feature_key PK
        varchar source_kind
        varchar data_type
    }
    cdp_ai_agents {
        varchar agent_code PK
        varchar display_name
        varchar model_type
    }
    cdp_profile_attributes {
        uuid id PK
        varchar attribute_internal_code UK
        varchar attribute_group
        varchar agent_code FK
        boolean is_identity_resolution
    }

    sys_tenant ||--o{ cdp_persona_archetypes : owns
    cdp_persona_archetypes ||--o{ cdp_customer_personas : matches
    cdp_master_profiles ||--o{ cdp_customer_personas : versioned_assignments
    cdp_customer_personas ||--o{ cdp_persona_features : explains
    cdp_customer_personas ||--o{ cdp_persona_score_details : scores
    cdp_customer_personas ||--o{ cdp_persona_history : history
    cdp_customer_personas o|--o| cdp_master_profiles : current_persona
    cdp_ai_agents ||--o{ cdp_profile_attributes : defines_outputs
```

`cdp_event_catalog`, `cdp_ai_feature_catalog`, `cdp_ai_agents`, `cdp_profile_attributes`, and `cdp_persona_config` are global metadata/configuration catalogs; they do not all have a tenant key. Persona assignments are versioned rows linking a master profile to a reusable archetype.

## 5. Segmentation, content, and recommendations

```mermaid
erDiagram
    sys_tenant {
        uuid tenant_id PK
    }
    sys_user {
        uuid user_id PK
        uuid tenant_id FK
    }
    cdp_segments {
        uuid segment_id PK
        uuid tenant_id FK
        uuid user_id FK
        text segment_tag
        text segment_name
        jsonb json_rules
        text sql_rules
        text final_generated_sql
        boolean is_active
    }
    cdp_ai_agents {
        varchar agent_code PK
    }
    cdp_agent_workflow {
        uuid agent_workflow_id PK
        uuid tenant_id FK
        uuid segment_id FK
        varchar agent_code FK
        integer execution_order
        uuid_array candidate_content_item_ids
    }
    cdp_content_items {
        uuid content_item_id PK
        uuid tenant_id FK
        text domain
        text item_type
        text title
        text_array segment_tags
        vector embedding
    }
    cdp_product_items {
        text product_item_id PK
        uuid tenant_id FK
        uuid content_item_id FK
        text source_id
        text product_id
    }
    cdp_profile_recommendation_runs {
        uuid tenant_id PK, FK
        uuid segment_id PK, FK
        text run_id PK
        text status
        integer profile_count
    }
    cdp_master_profiles {
        uuid master_profile_id PK
        uuid tenant_id FK
    }
    cdp_profile_recommendations {
        uuid tenant_id PK, FK
        uuid segment_id PK, FK
        uuid master_profile_id PK, FK
        varchar agent_code PK
        text run_id PK, FK
        uuid content_item_id PK, FK
        integer rank
        double score
    }

    sys_tenant ||--o{ cdp_segments : owns
    sys_user o|--o{ cdp_segments : manages
    cdp_segments ||--o{ cdp_agent_workflow : configures
    cdp_ai_agents ||--o{ cdp_agent_workflow : executes
    cdp_content_items o|--o| cdp_product_items : product_content
    cdp_segments ||--o{ cdp_profile_recommendation_runs : runs
    cdp_profile_recommendation_runs ||--o{ cdp_profile_recommendations : produces
    cdp_master_profiles ||--o{ cdp_profile_recommendations : receives
    cdp_content_items ||--o{ cdp_profile_recommendations : recommended
```

`cdp_product_items.content_item_id` is tenant-scoped and unique when present. `cdp_agent_workflow.candidate_content_item_ids` is an array validated by database triggers rather than a conventional foreign key. Recommendation records reference their run, tenant profile, and tenant content item with composite foreign keys.

## 6. Campaign planning, approval, activation, and reporting

```mermaid
erDiagram
    sys_tenant {
        uuid tenant_id PK
    }
    sys_user {
        uuid user_id PK
        uuid tenant_id FK
    }
    cdp_segments {
        uuid segment_id PK
        uuid tenant_id FK
    }
    cdp_persona_archetypes {
        uuid persona_archetype_id PK
        uuid tenant_id FK
    }
    cdp_master_profiles {
        uuid master_profile_id PK
        uuid tenant_id FK
    }
    cdp_content_items {
        uuid content_item_id PK
        uuid tenant_id FK
    }
    crm_message_templates {
        uuid template_id PK
        uuid tenant_id FK
        uuid persona_id FK
        uuid created_by FK
        uuid approved_by FK
        text message_type
        text status
    }
    crm_campaign {
        uuid campaign_id PK
        uuid tenant_id FK
        uuid user_id FK
        uuid approved_by FK
        uuid segment_id FK
        uuid workflow_id FK
        integer workflow_version FK
        text name
        varchar approval_status
        text strategy_summary
        jsonb ai_plan
    }
    crm_campaign_performance_daily {
        uuid performance_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid experiment_variant_id FK
        date report_date
        numeric spend
        bigint impressions
        bigint clicks
        bigint conversions
    }
    crm_campaign_member {
        uuid campaign_member_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid user_id FK
        uuid contact_id FK
    }
    crm_campaign_reviews {
        uuid review_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid reviewer_id FK
        text decision
    }
    crm_campaign_experiments {
        uuid experiment_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid winning_variant_id FK
        text status
        varchar primary_metric
    }
    crm_campaign_experiment_variants {
        uuid variant_id PK
        uuid tenant_id FK
        uuid experiment_id FK
        uuid segment_id FK
        uuid template_id FK
        numeric allocation_percentage
    }
    crm_campaign_content_items {
        uuid campaign_content_item_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid content_item_id FK
        integer position
    }
    crm_segment_sync_runs {
        uuid sync_run_id PK
        uuid tenant_id FK
        uuid segment_id FK
        uuid triggered_by FK
        text status
    }
    crm_ad_sync_runs {
        uuid sync_run_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid segment_id FK
        varchar platform
        varchar audience_name
        text idempotency_key UK
        text external_audience_id
        text status
        integer eligible_count
        integer uploaded_count
    }
    crm_campaign_workflow_action_runs {
        uuid action_run_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid master_profile_id FK
        text node_id
        varchar action_type
        varchar status
        text provider_message_id
        integer attempt_count
    }
    cdp_campaign_dispatch_logs {
        uuid dispatch_id PK
        uuid tenant_id FK
        uuid campaign_id FK
        uuid master_profile_id FK
        uuid template_id FK
        text status
        text provider_message_id
    }
    crm_connector_config {
        uuid connector_id PK
        uuid tenant_id FK
        uuid user_id FK
        varchar connector_type
        varchar provider
        jsonb config
    }
    crm_suppression_list {
        uuid suppression_id PK
        uuid tenant_id FK
        uuid master_profile_id FK
        uuid campaign_id FK
        uuid removed_by FK
        text channel
        text identifier
        text scope
    }

    sys_tenant ||--o{ crm_campaign : owns
    sys_user o|--o{ crm_campaign : owner
    sys_user o|--o{ crm_campaign : approver
    cdp_segments o|--o{ crm_campaign : targets
    crm_message_templates o|--o{ crm_campaign : uses
    crm_campaign ||--o{ crm_campaign_performance_daily : reports
    crm_campaign ||--o{ crm_campaign_member : includes
    sys_user o|--o{ crm_campaign_member : manages
    crm_campaign ||--o{ crm_campaign_reviews : reviewed_by
    sys_user ||--o{ crm_campaign_reviews : reviewer
    crm_campaign ||--o{ crm_campaign_experiments : tests
    sys_user o|--o{ crm_campaign_experiments : creates
    sys_user o|--o{ crm_message_templates : created_by
    sys_user o|--o{ crm_message_templates : approved_by
    crm_campaign_experiments ||--o{ crm_campaign_experiment_variants : contains
    cdp_segments ||--o{ crm_campaign_experiment_variants : targets
    crm_message_templates o|--o{ crm_campaign_experiment_variants : uses
    crm_campaign_experiment_variants o|--o| crm_campaign_experiments : winning_variant
    crm_campaign ||--o{ crm_campaign_content_items : plans
    cdp_content_items ||--o{ crm_campaign_content_items : selected_item
    cdp_segments ||--o{ crm_segment_sync_runs : synchronized
    sys_user o|--o{ crm_segment_sync_runs : triggered_by
    crm_campaign ||--o{ crm_ad_sync_runs : exports
    cdp_segments ||--o{ crm_ad_sync_runs : audience
    crm_campaign ||--o{ crm_campaign_workflow_action_runs : executes
    cdp_master_profiles ||--o{ crm_campaign_workflow_action_runs : action_target
    crm_campaign ||--o{ cdp_campaign_dispatch_logs : dispatches
    cdp_master_profiles ||--o{ cdp_campaign_dispatch_logs : recipient
    crm_message_templates o|--o{ cdp_campaign_dispatch_logs : rendered_from
    sys_user o|--o{ crm_connector_config : configures
    cdp_master_profiles o|--o{ crm_suppression_list : suppresses
    crm_campaign o|--o{ crm_suppression_list : campaign_scope
    sys_user o|--o{ crm_suppression_list : removes
    cdp_persona_archetypes o|--o{ crm_message_templates : guides
```

`crm_campaign.workflow_id`, `workflow_version`, and `tenant_id` reference the exact `(flow_id, version, tenant_id)` row in `crm_flow_version`; both workflow columns are nullable together. See the workflow diagram below. The daily performance view is derived and includes conversion, CPA, CTR, CVR, and ROAS aggregates.

## 7. General-purpose graph edges

```mermaid
erDiagram
    sys_tenant {
        uuid tenant_id PK
    }
    graph_edges {
        uuid edge_id PK
        text relation PK
        uuid tenant_id FK
        uuid from_id
        uuid to_id
        text from_type
        text to_type
        text description
        jsonb metadata
        vector embedding
    }
    sys_tenant ||--o{ graph_edges : owns
```

`graph_edges` is partitioned by `relation`; the primary key is `(edge_id, relation)`. Its `from_id` and `to_id` are polymorphic identifiers and deliberately are not foreign keys. SQL creates 15 partitions: `graph_edges_belongs_to`, `graph_edges_comes_from`, `graph_edges_converted`, `graph_edges_follows`, `graph_edges_is_part_of`, `graph_edges_is_active_as`, `graph_edges_is_connected_to`, `graph_edges_is_from`, `graph_edges_created_by`, `graph_edges_is_driven_by`, `graph_edges_has_role`, `graph_edges_has`, `graph_edges_is_for_the`, `graph_edges_belongs_to_industry`, and the default partition `graph_edges_other`.

## 8. Persisted customer journey workflows

```mermaid
erDiagram
    sys_tenant {
        uuid tenant_id PK
    }
    crm_campaign {
        uuid campaign_id PK
        uuid tenant_id FK
        uuid workflow_id FK
        integer workflow_version FK
    }
    crm_flow {
        uuid flow_id PK
        uuid tenant_id FK
        text name
        text status
        integer live_version
    }
    crm_flow_version {
        uuid flow_id PK, FK
        integer version PK
        uuid tenant_id FK
        integer schema_version
        jsonb definition
        text created_by
        uuid created_by_user_id FK
        varchar agent_code FK
        jsonb provenance
        varchar approval_status
        uuid reviewed_by FK
        timestamptz reviewed_at
    }
    crm_flow_node {
        uuid flow_id PK, FK
        integer version PK, FK
        text node_id PK
        uuid tenant_id FK
        text node_type
        jsonb config
    }
    crm_flow_edge {
        uuid flow_id PK, FK
        integer version PK, FK
        text from_node PK, FK
        text to_node PK, FK
        uuid tenant_id FK
        text branch
        smallint position
    }
    sys_tenant ||--o{ crm_flow : owns
    crm_flow ||--o{ crm_flow_version : versions
    crm_flow_version ||--o{ crm_flow_node : contains
    crm_flow_node ||--o{ crm_flow_edge : from_node
    crm_flow_node ||--o| crm_flow_edge : to_node
    crm_flow_version ||--o{ crm_flow_edge : orders
    crm_flow_version o|--o{ crm_campaign : selected_by
```

`crm_flow_version.definition` is the immutable workflow JSON snapshot; its author, agent and provenance are fixed too, and only the review fields change (`crm_review_flow_version`). `crm_flow_node` and `crm_flow_edge` are its immutable relational projection. `crm_flow.live_version` must reference an existing version and can only be published when that version is Approved. The campaign reference is separately pinned to a specific workflow version.

## Other schema objects

| Object | SQL definition |
| --- | --- |
| `vw_campaign_performance_metrics` | View joining `crm_campaign` to `crm_campaign_performance_daily` to calculate campaign-level totals and KPIs. |
| `crm_vw_flow_node` | View over `crm_flow_node` deriving message channel and wait duration. |

`database-schema.sql` also defines functions for saving/publishing/loading flow JSON, persona and embedding maintenance triggers, tenant-consistent composite foreign keys, indexes, and row-level security policies. Those implementation objects are omitted from the ER diagrams. For exact column definitions, check constraints, uniqueness, delete behavior, indexes, and functions, consult [`database-schema.sql`](./database-schema.sql).
