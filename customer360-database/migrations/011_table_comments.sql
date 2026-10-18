-- Table comments for the nine tables that had none. Text-to-SQL retrieval reads
-- COMMENT ON TABLE to describe each table, so every table needs one. Comment-only:
-- no tables, columns or rows change, and re-applying is harmless.
BEGIN;

COMMENT ON TABLE customer360.cdp_profile_recommendation_runs IS
    'One execution of the profile recommendation job for a segment: its status, how many profiles and steps were processed, how many recommendations were written, timing, and any error message.';

COMMENT ON TABLE customer360.cdp_profile_recommendations IS
    'Content items recommended to one master profile by an AI agent for a segment run, with rank, overall, semantic and tag scores, the strategy used, matched tags and the reason.';

COMMENT ON TABLE customer360.crm_campaign_experiments IS
    'Campaign A/B tests: one experiment per campaign with its status, primary metric, start and end dates and winning variant. Variant-attributed results are in crm_campaign_performance_daily.';

COMMENT ON TABLE customer360.crm_campaign_experiment_variants IS
    'The variants of a campaign A/B test: each has a variant key, a target segment, an email template, a traffic allocation percentage and a flag for the control.';

COMMENT ON TABLE customer360.crm_flow IS
    'A saved customer-journey workflow: its name, status and which saved version is live. The saved versions are in crm_flow_version.';

COMMENT ON TABLE customer360.crm_flow_version IS
    'Immutable JSON snapshot of a customer-journey workflow, one per save; the source of truth for exact round trips.';

COMMENT ON TABLE customer360.crm_flow_node IS
    'One row per node of a customer-journey workflow version, with its node type and config, shredded from the version JSON for SQL queries and joins.';

COMMENT ON TABLE customer360.crm_flow_edge IS
    'Parent to child links between the nodes of a customer-journey workflow version, by branch. Each node has one parent and each branch one child.';

COMMENT ON TABLE customer360.sys_tenant_domain IS
    'The business domains (industries) a tenant supports, with default and active flags. A tenant can support several domains.';

COMMIT;
