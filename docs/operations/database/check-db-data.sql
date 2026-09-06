-- Customer 360 core data audit
--
-- Read-only diagnostic queries for pgAdmin Query Tool. Change only the tenant
-- UUID below before running. Tenant-scoped queries use app.tenant_id so the
-- same checks respect the database RLS policies used by the API.
--
-- The script intentionally uses SQL only: no psql \commands, variables,
-- temporary tables, INSERT, UPDATE, DELETE, or DDL.

SET app.tenant_id = '11111111-1111-1111-1111-111111111111';
SET statement_timeout = '60s';
SET lock_timeout = '5s';
SET search_path = customer360, public;

-- ---------------------------------------------------------------------------
-- 1. Session and tenant context
-- ---------------------------------------------------------------------------

SELECT
	current_database() AS database_name,
	current_user AS database_user,
	current_setting('app.tenant_id', true) AS selected_tenant_id,
	current_timestamp AS checked_at;

SELECT
	t.tenant_id,
	t.tenant_code,
	t.tenant_name,
	t.company_name,
	t.business_type,
	t.status,
	t.created_at
FROM customer360.sys_tenant AS t
WHERE t.tenant_id = current_setting('app.tenant_id')::uuid;

-- A missing row here means the UUID at the top is not a tenant in this DB.
SELECT
	d.domain_code,
	d.domain_name,
	td.is_default,
	td.is_active
FROM customer360.sys_tenant_domain AS td
JOIN customer360.sys_domain AS d ON d.domain_id = td.domain_id
WHERE td.tenant_id = current_setting('app.tenant_id')::uuid
ORDER BY td.is_default DESC, d.display_order, d.domain_code;

-- ---------------------------------------------------------------------------
-- 2. Core table row counts for the selected tenant
-- ---------------------------------------------------------------------------

SELECT table_name, row_count
FROM (
	SELECT 'sys_user' AS table_name, COUNT(*)::bigint AS row_count
	FROM customer360.sys_user
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'sys_userinfo', COUNT(*)::bigint
	FROM customer360.sys_userinfo
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'sys_data_source', COUNT(*)::bigint
	FROM customer360.sys_data_source
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'sys_tenant_domain', COUNT(*)::bigint
	FROM customer360.sys_tenant_domain
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'sys_organization', COUNT(*)::bigint
	FROM customer360.sys_organization
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'sys_role', COUNT(*)::bigint
	FROM customer360.sys_role
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'sys_user_role', COUNT(*)::bigint
	FROM customer360.sys_user_role AS ur
	JOIN customer360.sys_user AS u ON u.user_id = ur.user_id
	WHERE u.tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'sys_audit_log', COUNT(*)::bigint
	FROM customer360.sys_audit_log
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_raw_profiles_stage', COUNT(*)::bigint
	FROM customer360.cdp_raw_profiles_stage
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_master_profiles', COUNT(*)::bigint
	FROM customer360.cdp_master_profiles
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_domain_profiles', COUNT(*)::bigint
	FROM customer360.cdp_domain_profiles
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_profile_links', COUNT(*)::bigint
	FROM customer360.cdp_profile_links
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_identity_index', COUNT(*)::bigint
	FROM customer360.cdp_identity_index
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_raw_events', COUNT(*)::bigint
	FROM customer360.cdp_raw_events
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_segments', COUNT(*)::bigint
	FROM customer360.cdp_segments
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_persona_archetypes', COUNT(*)::bigint
	FROM customer360.cdp_persona_archetypes
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_customer_personas', COUNT(*)::bigint
	FROM customer360.cdp_customer_personas
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_persona_features', COUNT(*)::bigint
	FROM customer360.cdp_persona_features AS pf
	JOIN customer360.cdp_customer_personas AS cp ON cp.persona_id = pf.persona_id
	WHERE cp.tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_persona_score_details', COUNT(*)::bigint
	FROM customer360.cdp_persona_score_details AS psd
	JOIN customer360.cdp_customer_personas AS cp ON cp.persona_id = psd.persona_id
	WHERE cp.tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_persona_history', COUNT(*)::bigint
	FROM customer360.cdp_persona_history AS ph
	JOIN customer360.cdp_customer_personas AS cp ON cp.persona_id = ph.persona_id
	WHERE cp.tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_profile_merge_history', COUNT(*)::bigint
	FROM customer360.cdp_profile_merge_history
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_relations', COUNT(*)::bigint
	FROM customer360.cdp_relations
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_campaign', COUNT(*)::bigint
	FROM customer360.crm_campaign
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_campaign_performance_daily', COUNT(*)::bigint
	FROM customer360.crm_campaign_performance_daily
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_campaign_member', COUNT(*)::bigint
	FROM customer360.crm_campaign_member
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_lead', COUNT(*)::bigint
	FROM customer360.crm_lead
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_lead_source', COUNT(*)::bigint
	FROM customer360.crm_lead_source
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_contact', COUNT(*)::bigint
	FROM customer360.crm_contact
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_account', COUNT(*)::bigint
	FROM customer360.crm_account
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_industry', COUNT(*)::bigint
	FROM customer360.crm_industry
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_opportunity', COUNT(*)::bigint
	FROM customer360.crm_opportunity
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_customer_contacts', COUNT(*)::bigint
	FROM customer360.crm_customer_contacts
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'crm_transactions', COUNT(*)::bigint
	FROM customer360.crm_transactions
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT 'cdp_content_items', COUNT(*)::bigint
	FROM customer360.cdp_content_items
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
) AS counts
ORDER BY table_name;

-- Global catalogs are not tenant-scoped.
SELECT
	'sys_domain' AS table_name,
	COUNT(*)::bigint AS row_count,
	COUNT(*) FILTER (WHERE is_active)::bigint AS active_rows
FROM customer360.sys_domain
UNION ALL
SELECT
	'cdp_event_catalog',
	COUNT(*)::bigint,
	COUNT(*) FILTER (WHERE status = 'ACTIVE')::bigint
FROM customer360.cdp_event_catalog
UNION ALL
SELECT
	'cdp_profile_attributes',
	COUNT(*)::bigint,
	COUNT(*) FILTER (WHERE status = 'ACTIVE')::bigint
FROM customer360.cdp_profile_attributes
UNION ALL
SELECT
	'cdp_scoring_models',
	COUNT(*)::bigint,
	COUNT(*) FILTER (WHERE status = 'ACTIVE')::bigint
FROM customer360.cdp_scoring_models
ORDER BY table_name;

-- ---------------------------------------------------------------------------
-- 3. Master profile quality
-- ---------------------------------------------------------------------------

SELECT
	domain,
	COALESCE(lifecycle_stage, '<none>') AS lifecycle_stage,
	COUNT(*)::bigint AS profile_count
FROM customer360.cdp_master_profiles
WHERE tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY domain, lifecycle_stage
ORDER BY domain, lifecycle_stage;

SELECT
	COUNT(*)::bigint AS total_profiles,
	COUNT(*) FILTER (WHERE email IS NULL OR btrim(email) = '')::bigint AS missing_email,
	COUNT(*) FILTER (WHERE phone_number IS NULL OR btrim(phone_number) = '')::bigint AS missing_phone,
	COUNT(*) FILTER (WHERE customer_since IS NULL)::bigint AS missing_customer_since,
	COUNT(*) FILTER (WHERE lifecycle_stage IS NULL)::bigint AS missing_lifecycle_stage,
	COUNT(*) FILTER (WHERE last_activity_at IS NULL)::bigint AS missing_last_activity,
	COUNT(*) FILTER (WHERE external_ids = '{}'::jsonb OR external_ids IS NULL)::bigint AS missing_external_ids,
	COUNT(*) FILTER (WHERE COALESCE(array_length(segmentation_tags, 1), 0) = 0)::bigint AS profiles_without_segments
FROM customer360.cdp_master_profiles
WHERE tenant_id = current_setting('app.tenant_id')::uuid;

SELECT
	COUNT(*) FILTER (WHERE lead_conversion_probability IS NULL)::bigint AS missing_lead_score,
	COUNT(*) FILTER (WHERE churn_probability IS NULL)::bigint AS missing_churn_score,
	COUNT(*) FILTER (WHERE churn_risk_tier IS NULL)::bigint AS missing_churn_tier,
	COUNT(*) FILTER (WHERE predictive_clv IS NULL)::bigint AS missing_predictive_clv,
	COUNT(*) FILTER (WHERE engagement_score IS NULL)::bigint AS missing_engagement_score,
	COUNT(*) FILTER (WHERE identity_confidence_score IS NULL)::bigint AS missing_identity_confidence,
	COUNT(*) FILTER (WHERE profile_completeness_score IS NULL)::bigint AS missing_completeness_score
FROM customer360.cdp_master_profiles
WHERE tenant_id = current_setting('app.tenant_id')::uuid;

-- Domain values must exist in the active domain catalog.
SELECT
	m.domain,
	COUNT(*)::bigint AS invalid_profile_count
FROM customer360.cdp_master_profiles AS m
LEFT JOIN customer360.sys_domain AS d ON d.domain_code = m.domain
WHERE m.tenant_id = current_setting('app.tenant_id')::uuid
  AND (d.domain_id IS NULL OR NOT d.is_active)
GROUP BY m.domain
ORDER BY invalid_profile_count DESC, m.domain;

-- ---------------------------------------------------------------------------
-- 4. Domain profiles and identity-resolution integrity
-- ---------------------------------------------------------------------------

SELECT
	COALESCE(d.domain_code, '<missing domain>') AS domain,
	COUNT(*)::bigint AS domain_profile_count,
	COUNT(*) FILTER (WHERE dp.master_profile_id IS NULL)::bigint AS missing_master_id,
	COUNT(*) FILTER (WHERE dp.domain_attributes = '{}'::jsonb)::bigint AS empty_domain_attributes
FROM customer360.cdp_domain_profiles AS dp
LEFT JOIN customer360.sys_domain AS d ON d.domain_id = dp.domain_id
WHERE dp.tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY d.domain_code
ORDER BY d.domain_code NULLS FIRST;

-- The schema has single-column FKs, so explicitly check tenant consistency too.
SELECT
	dp.domain_profile_id,
	dp.master_profile_id,
	dp.tenant_id AS domain_profile_tenant_id,
	mp.tenant_id AS master_profile_tenant_id,
	d.domain_code AS domain_profile_domain,
	mp.domain AS master_profile_domain
FROM customer360.cdp_domain_profiles AS dp
JOIN customer360.cdp_master_profiles AS mp ON mp.master_profile_id = dp.master_profile_id
LEFT JOIN customer360.sys_domain AS d ON d.domain_id = dp.domain_id
WHERE dp.tenant_id = current_setting('app.tenant_id')::uuid
  AND (dp.tenant_id <> mp.tenant_id OR d.domain_code IS NULL OR d.domain_code <> mp.domain)
ORDER BY dp.domain_profile_id;

SELECT
	identifier_type,
	COUNT(*)::bigint AS identifier_count,
	COUNT(*) FILTER (WHERE is_primary)::bigint AS primary_count,
	COUNT(*) FILTER (WHERE is_blocked)::bigint AS blocked_count
FROM customer360.cdp_identity_index
WHERE tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY identifier_type
ORDER BY identifier_type;

-- Identity index rows must point to profiles in the same tenant.
SELECT
	ii.identity_index_id,
	ii.identifier_type,
	ii.master_profile_id,
	ii.tenant_id AS index_tenant_id,
	mp.tenant_id AS profile_tenant_id
FROM customer360.cdp_identity_index AS ii
LEFT JOIN customer360.cdp_master_profiles AS mp ON mp.master_profile_id = ii.master_profile_id
WHERE ii.tenant_id = current_setting('app.tenant_id')::uuid
  AND (mp.master_profile_id IS NULL OR mp.tenant_id <> ii.tenant_id)
ORDER BY ii.identity_index_id;

-- ---------------------------------------------------------------------------
-- 5. Raw ingestion, CIR links, and event health
-- ---------------------------------------------------------------------------

SELECT
	status_code,
	COUNT(*)::bigint AS staged_profile_count,
	MIN(created_at) AS oldest_created_at,
	MAX(created_at) AS newest_created_at
FROM customer360.cdp_raw_profiles_stage
WHERE tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY status_code
ORDER BY status_code DESC;

SELECT
	COUNT(*)::bigint AS stale_unprocessed_profiles
FROM customer360.cdp_raw_profiles_stage
WHERE tenant_id = current_setting('app.tenant_id')::uuid
  AND status_code IN (1, 2)
  AND created_at < current_timestamp - INTERVAL '24 hours';

SELECT
	l.status,
	COUNT(*)::bigint AS link_count,
	COUNT(*) FILTER (WHERE l.match_score IS NULL)::bigint AS missing_match_score,
	COUNT(*) FILTER (WHERE l.match_score < 0 OR l.match_score > 1)::bigint AS invalid_match_score
FROM customer360.cdp_profile_links AS l
WHERE l.tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY l.status
ORDER BY l.status;

SELECT
	l.link_id,
	l.raw_profile_id,
	l.master_profile_id,
	l.tenant_id AS link_tenant_id,
	r.tenant_id AS raw_profile_tenant_id,
	m.tenant_id AS master_profile_tenant_id
FROM customer360.cdp_profile_links AS l
LEFT JOIN customer360.cdp_raw_profiles_stage AS r ON r.raw_profile_id = l.raw_profile_id
LEFT JOIN customer360.cdp_master_profiles AS m ON m.master_profile_id = l.master_profile_id
WHERE l.tenant_id = current_setting('app.tenant_id')::uuid
  AND (r.raw_profile_id IS NULL OR m.master_profile_id IS NULL
	   OR r.tenant_id <> l.tenant_id OR m.tenant_id <> l.tenant_id)
ORDER BY l.link_id;

SELECT
	event_category,
	domain,
	COUNT(*)::bigint AS event_count,
	COUNT(*) FILTER (WHERE is_conversion)::bigint AS conversion_count,
	COUNT(*) FILTER (WHERE master_profile_id IS NULL)::bigint AS unresolved_count
FROM customer360.cdp_raw_events
WHERE tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY event_category, domain
ORDER BY domain, event_category;

SELECT
	COUNT(*)::bigint AS total_events,
	COUNT(*) FILTER (WHERE master_profile_id IS NULL)::bigint AS unresolved_events,
	COUNT(*) FILTER (WHERE event_time > current_timestamp)::bigint AS future_events,
	MIN(event_time) AS oldest_event_time,
	MAX(event_time) AS newest_event_time
FROM customer360.cdp_raw_events
WHERE tenant_id = current_setting('app.tenant_id')::uuid;

-- Events should use the governed catalog when a matching catalog row exists.
SELECT
	e.event_category,
	e.domain,
	e.event_name,
	COUNT(*)::bigint AS uncatalogued_event_count
FROM customer360.cdp_raw_events AS e
LEFT JOIN customer360.cdp_event_catalog AS c
	ON c.event_name = e.event_name
   AND (c.domain_scope = 'all' OR c.domain_scope = e.domain)
WHERE e.tenant_id = current_setting('app.tenant_id')::uuid
  AND c.id IS NULL
GROUP BY e.event_category, e.domain, e.event_name
ORDER BY uncatalogued_event_count DESC, e.event_name;

-- Duplicate non-null source deduplication keys indicate retry/idempotency issues.
SELECT
	source_system,
	event_dedup_key,
	COUNT(*)::bigint AS duplicate_count
FROM customer360.cdp_raw_events
WHERE tenant_id = current_setting('app.tenant_id')::uuid
  AND event_dedup_key IS NOT NULL
GROUP BY source_system, event_dedup_key
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, source_system, event_dedup_key;

-- ---------------------------------------------------------------------------
-- 6. Transactions and customer contacts
-- ---------------------------------------------------------------------------

SELECT
	COALESCE(transaction_type, '<none>') AS transaction_type,
	COALESCE(transaction_status, '<none>') AS transaction_status,
	COUNT(*)::bigint AS transaction_count,
	COALESCE(SUM(amount), 0)::numeric(20, 2) AS total_amount,
	COUNT(*) FILTER (WHERE master_profile_id IS NULL)::bigint AS unlinked_count
FROM customer360.crm_transactions
WHERE tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY transaction_type, transaction_status
ORDER BY transaction_type, transaction_status;

SELECT
	COUNT(*)::bigint AS total_transactions,
	COUNT(*) FILTER (WHERE master_profile_id IS NULL)::bigint AS transactions_without_profile,
	COUNT(*) FILTER (WHERE transaction_time IS NULL)::bigint AS transactions_without_time,
	COUNT(*) FILTER (WHERE amount IS NULL)::bigint AS transactions_without_amount
FROM customer360.crm_transactions
WHERE tenant_id = current_setting('app.tenant_id')::uuid;

SELECT
	cc.contact_channel,
	cc.contact_type,
	COUNT(*)::bigint AS contact_count,
	COUNT(*) FILTER (WHERE cc.contact_date IS NULL)::bigint AS missing_contact_date
FROM customer360.crm_customer_contacts AS cc
WHERE cc.tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY cc.contact_channel, cc.contact_type
ORDER BY contact_count DESC;

-- Transactions and contacts use single-column profile FKs; check tenant safety.
SELECT
	'crm_transactions' AS table_name,
	COUNT(*)::bigint AS tenant_mismatch_count
FROM customer360.crm_transactions AS tr
JOIN customer360.cdp_master_profiles AS mp ON mp.master_profile_id = tr.master_profile_id
WHERE tr.tenant_id = current_setting('app.tenant_id')::uuid
  AND tr.master_profile_id IS NOT NULL
  AND mp.tenant_id <> tr.tenant_id
UNION ALL
SELECT
	'crm_customer_contacts',
	COUNT(*)::bigint
FROM customer360.crm_customer_contacts AS cc
JOIN customer360.cdp_master_profiles AS mp ON mp.master_profile_id = cc.master_profile_id
WHERE cc.tenant_id = current_setting('app.tenant_id')::uuid
  AND mp.tenant_id <> cc.tenant_id;

-- ---------------------------------------------------------------------------
-- 7. Segmentation and Audience Builder health
-- ---------------------------------------------------------------------------

SELECT
	domain,
	is_active,
	COUNT(*)::bigint AS segment_count,
	COUNT(*) FILTER (WHERE NULLIF(btrim(segment_name), '') IS NULL)::bigint AS missing_name_count,
	COUNT(*) FILTER (WHERE NULLIF(btrim(description), '') IS NULL)::bigint AS missing_description_count,
	COUNT(*) FILTER (WHERE json_rules IS NULL OR json_rules = '{}'::jsonb)::bigint AS missing_rules_count,
	COUNT(*) FILTER (WHERE NULLIF(btrim(sql_rules), '') IS NULL)::bigint AS missing_sql_count
FROM customer360.cdp_segments
WHERE tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY domain, is_active
ORDER BY domain, is_active DESC;

-- Every segment domain must be all or an active configured domain.
SELECT
	s.segment_tag,
	s.segment_name,
	s.domain
FROM customer360.cdp_segments AS s
LEFT JOIN customer360.sys_domain AS d ON d.domain_code = s.domain
WHERE s.tenant_id = current_setting('app.tenant_id')::uuid
  AND s.domain <> 'all'
  AND (d.domain_id IS NULL OR NOT d.is_active)
ORDER BY s.domain, s.segment_tag;

-- Compare stored member_count with the current profile tag array.
SELECT
	s.segment_tag,
	s.segment_name,
	s.member_count AS stored_member_count,
	COUNT(mp.master_profile_id)::integer AS calculated_member_count
FROM customer360.cdp_segments AS s
LEFT JOIN customer360.cdp_master_profiles AS mp
	ON mp.tenant_id = s.tenant_id
   AND s.segment_tag = ANY(COALESCE(mp.segmentation_tags, ARRAY[]::text[]))
WHERE s.tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY s.segment_id, s.segment_tag, s.segment_name, s.member_count
HAVING s.member_count IS DISTINCT FROM COUNT(mp.master_profile_id)::integer
ORDER BY s.segment_tag;

-- ---------------------------------------------------------------------------
-- 8. Persona and explainability health
-- ---------------------------------------------------------------------------

SELECT
	a.domain,
	COUNT(*)::bigint AS archetype_count,
	COUNT(*) FILTER (WHERE a.is_active)::bigint AS active_archetype_count,
	COUNT(*) FILTER (WHERE a.persona_embedding IS NULL)::bigint AS missing_embedding_count,
	COALESCE(SUM(a.matched_profile_count), 0)::bigint AS stored_matched_profile_count
FROM customer360.cdp_persona_archetypes AS a
WHERE a.tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY a.domain
ORDER BY a.domain;

SELECT
	COUNT(*)::bigint AS persona_assignment_count,
	COUNT(*) FILTER (WHERE is_active)::bigint AS active_assignment_count,
	COUNT(*) FILTER (WHERE match_score < 0 OR match_score > 1)::bigint AS invalid_match_scores,
	COUNT(*) FILTER (WHERE confidence_score < 0 OR confidence_score > 1)::bigint AS invalid_confidence_scores
FROM customer360.cdp_customer_personas
WHERE tenant_id = current_setting('app.tenant_id')::uuid;

-- Only one active persona assignment should exist per master profile.
SELECT
	master_profile_id,
	COUNT(*)::bigint AS active_persona_count
FROM customer360.cdp_customer_personas
WHERE tenant_id = current_setting('app.tenant_id')::uuid
  AND is_active
GROUP BY master_profile_id
HAVING COUNT(*) > 1
ORDER BY active_persona_count DESC, master_profile_id;

-- Check the denormalized archetype member count maintained by its trigger.
SELECT
	a.persona_archetype_id,
	a.persona_code,
	a.matched_profile_count AS stored_matched_profile_count,
	COUNT(DISTINCT cp.master_profile_id)::integer AS calculated_matched_profile_count
FROM customer360.cdp_persona_archetypes AS a
LEFT JOIN customer360.cdp_customer_personas AS cp
	ON cp.tenant_id = a.tenant_id
   AND cp.persona_archetype_id = a.persona_archetype_id
   AND cp.is_active
WHERE a.tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY a.persona_archetype_id, a.persona_code, a.matched_profile_count
HAVING a.matched_profile_count IS DISTINCT FROM COUNT(DISTINCT cp.master_profile_id)::integer
ORDER BY a.persona_code;

-- Current persona pointers must remain in the same tenant and point to active rows.
SELECT
	mp.master_profile_id,
	mp.current_persona_id,
	cp.tenant_id AS persona_tenant_id,
	cp.is_active AS persona_is_active
FROM customer360.cdp_master_profiles AS mp
LEFT JOIN customer360.cdp_customer_personas AS cp ON cp.persona_id = mp.current_persona_id
WHERE mp.tenant_id = current_setting('app.tenant_id')::uuid
  AND mp.current_persona_id IS NOT NULL
  AND (cp.persona_id IS NULL OR cp.tenant_id <> mp.tenant_id OR NOT cp.is_active);

-- ---------------------------------------------------------------------------
-- 9. CRM campaign and relationship integrity
-- ---------------------------------------------------------------------------

SELECT
	status,
	COUNT(*)::bigint AS campaign_count,
	COUNT(*) FILTER (WHERE start_date IS NULL)::bigint AS missing_start_date,
	COUNT(*) FILTER (WHERE end_date IS NULL)::bigint AS missing_end_date
FROM customer360.crm_campaign
WHERE tenant_id = current_setting('app.tenant_id')::uuid
GROUP BY status
ORDER BY status;

SELECT
	'crm_campaign_member_without_campaign' AS check_name,
	COUNT(*)::bigint AS issue_count
FROM customer360.crm_campaign_member AS cm
LEFT JOIN customer360.crm_campaign AS c ON c.campaign_id = cm.campaign_id
WHERE cm.tenant_id = current_setting('app.tenant_id')::uuid
  AND (cm.campaign_id IS NOT NULL AND (c.campaign_id IS NULL OR c.tenant_id <> cm.tenant_id))
UNION ALL
SELECT
	'crm_campaign_performance_without_campaign',
	COUNT(*)::bigint
FROM customer360.crm_campaign_performance_daily AS p
LEFT JOIN customer360.crm_campaign AS c ON c.campaign_id = p.campaign_id
WHERE p.tenant_id = current_setting('app.tenant_id')::uuid
  AND (c.campaign_id IS NULL OR c.tenant_id <> p.tenant_id)
UNION ALL
SELECT
	'crm_opportunity_without_account',
	COUNT(*)::bigint
FROM customer360.crm_opportunity AS o
LEFT JOIN customer360.crm_account AS a ON a.account_id = o.account_id
WHERE o.tenant_id = current_setting('app.tenant_id')::uuid
  AND o.account_id IS NOT NULL
  AND (a.account_id IS NULL OR a.tenant_id <> o.tenant_id)
UNION ALL
SELECT
	'crm_contact_without_account',
	COUNT(*)::bigint
FROM customer360.crm_contact AS c
LEFT JOIN customer360.crm_account AS a ON a.account_id = c.account_id
WHERE c.tenant_id = current_setting('app.tenant_id')::uuid
  AND c.account_id IS NOT NULL
  AND (a.account_id IS NULL OR a.tenant_id <> c.tenant_id);

SELECT
	r.relation_id,
	r.source_master_id,
	r.target_master_id,
	r.relation_type_id
FROM customer360.cdp_relations AS r
LEFT JOIN customer360.cdp_master_profiles AS source_profile
	ON source_profile.master_profile_id = r.source_master_id
LEFT JOIN customer360.cdp_master_profiles AS target_profile
	ON target_profile.master_profile_id = r.target_master_id
WHERE r.tenant_id = current_setting('app.tenant_id')::uuid
  AND (source_profile.master_profile_id IS NULL
	   OR target_profile.master_profile_id IS NULL
	   OR source_profile.tenant_id <> r.tenant_id
	   OR target_profile.tenant_id <> r.tenant_id);

-- ---------------------------------------------------------------------------
-- 10. Consolidated issue summary
-- ---------------------------------------------------------------------------

WITH checks AS (
	SELECT
		'tenant_exists' AS check_name,
		COUNT(*)::bigint AS issue_count
	FROM customer360.sys_tenant
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	UNION ALL
	SELECT
		'profiles_with_no_identity',
		COUNT(*)::bigint
	FROM customer360.cdp_master_profiles
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	  AND COALESCE(NULLIF(btrim(email), ''), NULLIF(btrim(phone_number), ''), NULLIF(full_name, '')) IS NULL
	UNION ALL
	SELECT
		'stale_unprocessed_raw_profiles',
		COUNT(*)::bigint
	FROM customer360.cdp_raw_profiles_stage
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	  AND status_code IN (1, 2)
	  AND created_at < current_timestamp - INTERVAL '24 hours'
	UNION ALL
	SELECT
		'unresolved_events',
		COUNT(*)::bigint
	FROM customer360.cdp_raw_events
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	  AND master_profile_id IS NULL
	UNION ALL
	SELECT
		'transactions_without_profile',
		COUNT(*)::bigint
	FROM customer360.crm_transactions
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	  AND master_profile_id IS NULL
	UNION ALL
	SELECT
		'segments_without_rules',
		COUNT(*)::bigint
	FROM customer360.cdp_segments
	WHERE tenant_id = current_setting('app.tenant_id')::uuid
	  AND (json_rules IS NULL OR json_rules = '{}'::jsonb OR NULLIF(btrim(sql_rules), '') IS NULL)
	UNION ALL
	SELECT
		'profiles_with_multiple_active_personas',
		COUNT(*)::bigint
	FROM (
		SELECT master_profile_id
		FROM customer360.cdp_customer_personas
		WHERE tenant_id = current_setting('app.tenant_id')::uuid
		  AND is_active
		GROUP BY master_profile_id
		HAVING COUNT(*) > 1
	) AS duplicate_personas
)
SELECT
	check_name,
	issue_count,
	CASE
		WHEN check_name = 'tenant_exists' AND issue_count = 1 THEN 'PASS'
		WHEN check_name = 'tenant_exists' THEN 'FAIL'
		WHEN issue_count = 0 THEN 'PASS'
		ELSE 'WARN'
	END AS status
FROM checks
ORDER BY CASE WHEN check_name = 'tenant_exists' THEN 0 ELSE 1 END, check_name;

-- ---------------------------------------------------------------------------
-- 11. Database-level constraint and RLS visibility
-- ---------------------------------------------------------------------------

SELECT
	n.nspname AS schema_name,
	c.relname AS table_name,
	c.relrowsecurity AS row_security_enabled,
	c.relforcerowsecurity AS force_row_security
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'customer360'
  AND c.relkind IN ('r', 'p')
  AND c.relname IN (
	  'sys_user', 'sys_userinfo', 'sys_data_source',
	  'cdp_raw_profiles_stage', 'cdp_master_profiles', 'cdp_domain_profiles',
	  'cdp_profile_links', 'cdp_identity_index', 'cdp_raw_events',
	  'cdp_segments', 'cdp_persona_archetypes', 'cdp_customer_personas',
	  'crm_campaign', 'crm_campaign_member', 'crm_lead', 'crm_contact',
	  'crm_account', 'crm_opportunity', 'crm_customer_contacts', 'crm_transactions'
  )
ORDER BY table_name;

-- Unvalidated constraints require migration attention before relying on them.
SELECT
	conname AS constraint_name,
	conrelid::regclass AS table_name,
	contype AS constraint_type
FROM pg_catalog.pg_constraint
WHERE connamespace = 'customer360'::regnamespace
  AND NOT convalidated
ORDER BY table_name, constraint_name;
