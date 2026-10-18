-- =====================================================================
-- Sample data for the Campaign Workflow builder  (schema: customer360)
-- Run AFTER database-schema.sql.  Re-runnable: every insert is guarded.
--
-- Tables touched
--   sys_tenant, sys_domain, sys_user
--   cdp_master_profiles (60 profiles)   cdp_segments (1 segment)
--   crm_message_templates (5)           crm_campaign (1) + workflow JSON
--   crm_campaign_performance_daily (14) cdp_campaign_dispatch_logs
--   crm_suppression_list (4)
--
-- Where the workflow lives:  crm_campaign.metadata -> 'workflow'
-- (JSONB, no schema change needed).  Node ids n1..n8 are written into
-- cdp_campaign_dispatch_logs.metadata -> 'workflow_node_id' so the report
-- can break delivery down per node.
-- =====================================================================

-- RLS: non-superuser app roles need the tenant context first.
SELECT set_config('app.tenant_id', 'a1111111-1111-4111-8111-111111111111', false);

-- Deterministic pseudo-random helper (0..0.999) so the sample is stable.
CREATE OR REPLACE FUNCTION pg_temp.r(i int, k int) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$ SELECT (('x' || substr(md5(i::text || '-' || k::text), 1, 6))::bit(24)::int) / 16777216.0 $$;

-- ---------------------------------------------------------------------
-- Tenant, domain, marketer
-- ---------------------------------------------------------------------
INSERT INTO customer360.sys_tenant (tenant_id, tenant_code, tenant_name, company_name, business_type)
VALUES ('a1111111-1111-4111-8111-111111111111', 'demo-retail', 'Demo Retail', 'Demo Retail JSC', 'retail')
ON CONFLICT (tenant_code) DO NOTHING;

INSERT INTO customer360.sys_domain (domain_code, domain_name, description, color, display_order)
VALUES ('retail', 'Retail', 'Retail and e-commerce', '#4F46E5', 1)
ON CONFLICT (domain_code) DO NOTHING;

INSERT INTO customer360.sys_user (user_id, tenant_id, username, email, full_name, job_title)
VALUES ('b2222222-2222-4222-8222-222222222222', 'a1111111-1111-4111-8111-111111111111',
        'marketer', 'marketer@example.vn', 'Linh Tran', 'CRM Marketer')
ON CONFLICT (user_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- 60 master profiles. 48 of them carry the segment tag 'lapsing_customers'.
-- ---------------------------------------------------------------------
INSERT INTO customer360.cdp_master_profiles (
    master_profile_id, tenant_id, domain, first_name, last_name, full_name, email, phone_number,
    persona_name, segmentation_tags, communication_preferences, push_tokens, preferred_channel,
    lifecycle_stage, engagement_score, churn_probability, churn_risk_tier,
    historical_clv, predictive_clv, lead_conversion_probability, latest_nps_score,
    last_activity_at, status_code
)
SELECT
    uuid_generate_v5('a1111111-1111-4111-8111-111111111111'::uuid, 'profile-' || p.i),
    'a1111111-1111-4111-8111-111111111111', 'retail',
    p.fn, p.ln, p.fn || ' ' || p.ln,
    lower(p.fn || '.' || p.ln || p.i || '@example.vn'),
    '+84901' || lpad(p.i::text, 6, '0'),
    (ARRAY['Bargain Hunter','Loyal Regular','Trend Follower','Occasional Browser'])[1 + p.i % 4],
    CASE WHEN p.i % 5 = 0 THEN ARRAY['new_visitor'] ELSE ARRAY['lapsing_customers'] END,
    jsonb_build_object('email_opt_in', pg_temp.r(p.i, 7) < 0.85,
                       'push_opt_in',  pg_temp.r(p.i, 8) < 0.60,
                       'sms_opt_in',   false),
    CASE WHEN pg_temp.r(p.i, 9) < 0.65
         THEN jsonb_build_object('fcm', 'fcm-token-' || p.i) ELSE '{}'::jsonb END,
    (ARRAY['Email','Mobile App','Website'])[1 + p.i % 3],
    CASE WHEN round(pg_temp.r(p.i, 3) * 30000000) >= 15000000 THEN 'vip'
         WHEN pg_temp.r(p.i, 6) * 120 > 90 THEN 'dormant'
         WHEN pg_temp.r(p.i, 2) >= 0.6 THEN 'churn_risk'
         ELSE 'customer' END,
    round(pg_temp.r(p.i, 1) * 100, 2),
    round(pg_temp.r(p.i, 2), 4),
    CASE WHEN pg_temp.r(p.i, 2) >= 0.8 THEN 'critical' WHEN pg_temp.r(p.i, 2) >= 0.6 THEN 'high'
         WHEN pg_temp.r(p.i, 2) >= 0.35 THEN 'medium' ELSE 'low' END,
    round(pg_temp.r(p.i, 3) * 30000000),
    round(pg_temp.r(p.i, 3) * 30000000 * (0.6 + pg_temp.r(p.i, 4))),
    round(pg_temp.r(p.i, 5), 4),
    floor(pg_temp.r(p.i, 5) * 11)::int,
    now() - (pg_temp.r(p.i, 6) * 120) * interval '1 day',
    1
FROM (
    SELECT i,
           (ARRAY['An','Binh','Chi','Dung','Giang','Ha','Hanh','Hieu','Khoa','Lan',
                  'Linh','Minh','Nam','Ngoc','Phuong','Quan','Quynh','Son','Thao','Trang'])[1 + (i * 7) % 20] AS fn,
           (ARRAY['Nguyen','Tran','Le','Pham','Hoang','Vu','Dang','Bui'])[1 + (i * 3) % 8] AS ln
    FROM generate_series(1, 60) AS i
) p
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Segment (jQuery QueryBuilder rules + the SQL they translate to)
-- ---------------------------------------------------------------------
INSERT INTO customer360.cdp_segments (
    segment_id, tenant_id, user_id, domain, segment_tag, segment_name, description,
    json_rules, sql_rules, final_generated_sql, processed_by, member_count, last_computed_at
)
VALUES (
    'c3333333-3333-4333-8333-333333333333', 'a1111111-1111-4111-8111-111111111111',
    'b2222222-2222-4222-8222-222222222222', 'retail',
    'lapsing_customers', 'Lapsing customers (last 120 days)',
    'Customers whose last activity is getting stale. Target for win-back and upsell.',
    '{"condition":"AND","rules":[{"id":"segmentation_tags","operator":"contains","value":"lapsing_customers"}]}'::jsonb,
    'segmentation_tags @> ARRAY[''lapsing_customers'']',
    'SELECT master_profile_id FROM customer360.cdp_master_profiles WHERE tenant_id = $1 AND segmentation_tags @> ARRAY[''lapsing_customers'']',
    'human', 48, now()
)
ON CONFLICT (tenant_id, segment_tag) DO NOTHING;

-- ---------------------------------------------------------------------
-- Message templates. PUSH uses subject = title, message_body = body.
-- ---------------------------------------------------------------------
INSERT INTO customer360.crm_message_templates
    (template_id, tenant_id, name, message_type, subject, message_body, html_body, variables, status, created_by, approved_by, approved_at)
VALUES
 ('d4444444-0000-4444-8444-000000000001', 'a1111111-1111-4111-8111-111111111111', 'VIP early access', 'EMAIL',
  '{{first_name}}, your early access starts now',
  E'Hi {{first_name}},\n\nAs one of our best customers you can shop the new collection 48 hours before everyone else.\n\nUnsubscribe: {{unsubscribe_url}}',
  '<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto"><h2>Hi {{first_name}},</h2><p>As one of our best customers you can shop the new collection <b>48 hours before everyone else</b>.</p><p><a href="#" style="background:#4F46E5;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">Shop early access</a></p><p style="color:#888;font-size:12px"><a href="{{unsubscribe_url}}">Unsubscribe</a></p></div>',
  '{"first_name":"string","unsubscribe_url":"url"}', 'Approved', 'b2222222-2222-4222-8222-222222222222', 'b2222222-2222-4222-8222-222222222222', now()),
 ('d4444444-0000-4444-8444-000000000002', 'a1111111-1111-4111-8111-111111111111', 'Back in stock nudge', 'PUSH',
  'Still on your wishlist', '{{first_name}}, the items you viewed are back in stock.',
  NULL, '{"first_name":"string"}', 'Approved', 'b2222222-2222-4222-8222-222222222222', 'b2222222-2222-4222-8222-222222222222', now()),
 ('d4444444-0000-4444-8444-000000000003', 'a1111111-1111-4111-8111-111111111111', 'Win-back 15% offer', 'EMAIL',
  'We miss you, {{first_name}}: 15% off inside',
  E'Hi {{first_name}},\n\nIt has been a while. Here is 15% off your next order.\n\nUnsubscribe: {{unsubscribe_url}}',
  '<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto"><h2>We miss you, {{first_name}}</h2><p>It has been a while. Here is <b>15% off</b> your next order, valid for 7 days.</p><p><a href="#" style="background:#0E8F7E;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">Claim 15% off</a></p><p style="color:#888;font-size:12px"><a href="{{unsubscribe_url}}">Unsubscribe</a></p></div>',
  '{"first_name":"string","unsubscribe_url":"url"}', 'Approved', 'b2222222-2222-4222-8222-222222222222', 'b2222222-2222-4222-8222-222222222222', now()),
 ('d4444444-0000-4444-8444-000000000004', 'a1111111-1111-4111-8111-111111111111', 'Flash sale (6 hours)', 'PUSH',
  'Flash sale: 15% off for 6 hours', '{{first_name}}, open the app before it ends.',
  NULL, '{"first_name":"string"}', 'Approved', 'b2222222-2222-4222-8222-222222222222', 'b2222222-2222-4222-8222-222222222222', now()),
 ('d4444444-0000-4444-8444-000000000005', 'a1111111-1111-4111-8111-111111111111', 'Black Friday teaser (draft)', 'EMAIL',
  'Something big is coming, {{first_name}}', 'Draft copy, not yet reviewed.',
  NULL, '{"first_name":"string"}', 'Draft', 'b2222222-2222-4222-8222-222222222222', NULL, NULL)
ON CONFLICT (template_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Campaign + decision-tree workflow (stored in metadata.workflow)
--   n1 segment -> n2 engagement >= 50
--      yes -> n3 historical CLV >= 10M VND -> yes n5 email VIP | no n6 push nudge
--      no  -> n4 churn probability >= 0.6  -> yes n7 email win-back | no n8 push flash sale
-- ---------------------------------------------------------------------
INSERT INTO customer360.crm_campaign (
    campaign_id, tenant_id, user_id, segment_id, template_id, approval_status, approved_by, approved_at,
    campaign_code, name, status, channel, platform, objective, description,
    start_date, end_date, budget_amount, currency, metadata
)
VALUES (
    'e5555555-5555-4555-8555-555555555555', 'a1111111-1111-4111-8111-111111111111',
    'b2222222-2222-4222-8222-222222222222', 'c3333333-3333-4333-8333-333333333333',
    'd4444444-0000-4444-8444-000000000001', 'Approved', 'b2222222-2222-4222-8222-222222222222', now(),
    'Q4-WINBACK-2026', 'Q4 win-back and upsell journey', 'Active', 'Email + Web push', 'Customer360',
    'Reactivation', 'Branch lapsing customers by engagement, value and churn risk, then activate by email or web push.',
    current_date - 14, current_date + 16, 25000000, 'VND',
    $wf${
      "workflow": {
        "schema_version": 1,
        "root": {
          "id": "n1", "type": "segment",
          "config": {"segment_id": "c3333333-3333-4333-8333-333333333333"},
          "children": [{
            "id": "n2", "type": "condition",
            "config": {"field": "engagement_score", "op": "gte", "value": 50},
            "children": [
              {"id": "n3", "type": "condition", "branch": "yes",
               "config": {"field": "historical_clv", "op": "gte", "value": 10000000},
               "children": [
                 {"id": "n5", "type": "email", "branch": "yes", "config": {"template_id": "d4444444-0000-4444-8444-000000000001"}, "children": []},
                 {"id": "n6", "type": "push",  "branch": "no",  "config": {"template_id": "d4444444-0000-4444-8444-000000000002"}, "children": []}
               ]},
              {"id": "n4", "type": "condition", "branch": "no",
               "config": {"field": "churn_probability", "op": "gte", "value": 0.6},
               "children": [
                 {"id": "n7", "type": "email", "branch": "yes", "config": {"template_id": "d4444444-0000-4444-8444-000000000003"}, "children": []},
                 {"id": "n8", "type": "push",  "branch": "no",  "config": {"template_id": "d4444444-0000-4444-8444-000000000004"}, "children": []}
               ]}
            ]
          }]
        }
      }
    }$wf$::jsonb
)
ON CONFLICT (tenant_id, campaign_code) DO NOTHING;

-- ---------------------------------------------------------------------
-- Suppression list: 3 unsubscribed emails + 1 profile-level block
-- ---------------------------------------------------------------------
INSERT INTO customer360.crm_suppression_list
    (tenant_id, master_profile_id, channel, identifier_type, identifier, reason, scope, source)
SELECT mp.tenant_id, mp.master_profile_id, 'EMAIL', 'EMAIL', mp.email, 'UNSUBSCRIBE', 'GLOBAL', 'unsubscribe_link'
FROM customer360.cdp_master_profiles mp
WHERE mp.tenant_id = 'a1111111-1111-4111-8111-111111111111'
  AND mp.master_profile_id IN (SELECT uuid_generate_v5('a1111111-1111-4111-8111-111111111111'::uuid, 'profile-' || i) FROM unnest(ARRAY[3, 11, 26]) AS i)
ON CONFLICT DO NOTHING;

INSERT INTO customer360.crm_suppression_list
    (tenant_id, master_profile_id, channel, identifier_type, identifier, reason, scope, source)
SELECT mp.tenant_id, mp.master_profile_id, 'GLOBAL', 'PROFILE_ID', mp.master_profile_id::text, 'LEGAL', 'GLOBAL', 'legal_hold'
FROM customer360.cdp_master_profiles mp
WHERE mp.master_profile_id = uuid_generate_v5('a1111111-1111-4111-8111-111111111111'::uuid, 'profile-' || 14)
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Dispatch ledger: one row per profile that reaches a send node.
-- Status mirrors what the activation job would write.
-- ---------------------------------------------------------------------
WITH routed AS (
    SELECT mp.*,
           CASE WHEN COALESCE(mp.engagement_score >= 50, false)
                THEN CASE WHEN COALESCE(mp.historical_clv >= 10000000, false) THEN 'n5' ELSE 'n6' END
                ELSE CASE WHEN COALESCE(mp.churn_probability >= 0.6, false)   THEN 'n7' ELSE 'n8' END
           END AS node_id
    FROM customer360.cdp_master_profiles mp
    WHERE mp.tenant_id = 'a1111111-1111-4111-8111-111111111111'
      AND mp.segmentation_tags @> ARRAY['lapsing_customers']
), typed AS (
    SELECT r.*,
           CASE r.node_id WHEN 'n5' THEN 'EMAIL' WHEN 'n6' THEN 'PUSH' WHEN 'n7' THEN 'EMAIL' ELSE 'PUSH' END AS channel,
           CASE r.node_id WHEN 'n5' THEN 'd4444444-0000-4444-8444-000000000001'
                          WHEN 'n6' THEN 'd4444444-0000-4444-8444-000000000002'
                          WHEN 'n7' THEN 'd4444444-0000-4444-8444-000000000003'
                          ELSE 'd4444444-0000-4444-8444-000000000004' END::uuid AS template_id,
           abs(hashtext(r.master_profile_id::text)) % 100 AS dice
    FROM routed r
)
INSERT INTO customer360.cdp_campaign_dispatch_logs
    (tenant_id, campaign_id, master_profile_id, template_id, recipient_email, status, provider,
     provider_message_id, rendered_subject, attempt_count, dispatched_at, metadata)
SELECT t.tenant_id, 'e5555555-5555-4555-8555-555555555555', t.master_profile_id, t.template_id,
       CASE WHEN t.channel = 'EMAIL' THEN t.email END,
       CASE
         WHEN t.channel = 'EMAIL' AND NOT COALESCE((t.communication_preferences->>'email_opt_in')::boolean, false) THEN 'Skipped'
         WHEN t.channel = 'PUSH'  AND NOT (COALESCE((t.communication_preferences->>'push_opt_in')::boolean, false)
                                           AND t.push_tokens <> '{}'::jsonb) THEN 'Skipped'
         WHEN EXISTS (SELECT 1 FROM customer360.crm_suppression_list s
                      WHERE s.tenant_id = t.tenant_id AND s.status = 'ACTIVE'
                        AND ((s.identifier_type = 'EMAIL' AND s.identifier = lower(t.email))
                          OR (s.identifier_type = 'PROFILE_ID' AND s.identifier = t.master_profile_id::text))) THEN 'Suppressed'
         WHEN t.dice < 92 THEN 'Sent'
         WHEN t.dice < 96 THEN 'Failed'
         ELSE 'Pending'
       END,
       CASE t.channel WHEN 'EMAIL' THEN 'sendgrid' ELSE 'onesignal' END,
       'msg-' || substr(md5(t.master_profile_id::text), 1, 12),
       NULL, 1, now() - (t.dice % 14) * interval '1 day',
       jsonb_build_object('workflow_node_id', t.node_id, 'channel', t.channel)
FROM typed t
ON CONFLICT (campaign_id, master_profile_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Daily performance (feeds vw_campaign_performance_metrics)
-- ---------------------------------------------------------------------
INSERT INTO customer360.crm_campaign_performance_daily
    (tenant_id, campaign_id, report_date, spend, impressions, clicks, conversions, revenue_estimated)
SELECT 'a1111111-1111-4111-8111-111111111111', 'e5555555-5555-4555-8555-555555555555',
       current_date - d,
       round(900000 + pg_temp.r(d, 11) * 800000, -3),
       (4000 + pg_temp.r(d, 12) * 3000)::bigint,
       (170 + pg_temp.r(d, 13) * 160)::bigint,
       (8 + pg_temp.r(d, 14) * 14)::bigint,
       round((8 + pg_temp.r(d, 14) * 14) * 480000, -3)
FROM generate_series(1, 14) AS d
ON CONFLICT DO NOTHING;

-- Keep the denormalised segment size honest.
UPDATE customer360.cdp_segments s
SET member_count = (SELECT count(*) FROM customer360.cdp_master_profiles mp
                    WHERE mp.tenant_id = s.tenant_id AND mp.segmentation_tags @> ARRAY[s.segment_tag])
WHERE s.segment_id = 'c3333333-3333-4333-8333-333333333333';
