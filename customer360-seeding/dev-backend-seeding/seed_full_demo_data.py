"""Seed the demo tenant with supported-domain CRM, profile, persona, and S3 fixtures."""

import gzip
import hashlib
import json
import logging
import os
import random
import sys
import uuid
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import connection as DatabaseConnection
from psycopg2.extras import Json, RealDictCursor


from leo_customer360_dao.agentic_engines.persona_engine import (  # noqa: E402
    PersonaResolutionEngine,
    compute_persona,
)
from leo_customer360_dao.utils.tenant_context import set_tenant_context
from seeding_content_items import seed_content_items  # noqa: E402

from seeding_utils import (
    DEMO_NAMESPACE,
    DEMO_TENANT_ID,
    behavioral_event_hour as _behavioral_event_hour,
    behavioral_object_key as _behavioral_object_key,
    build_global_profile_name,
    canonical_demo_domain,
    cosine_similarity as _cosine_similarity,
    demo_id,
    email_token,
    load_demo_metadata,
    realistic_event_days_ago,
    stable_rng,
    table_name,
    tracking_platform_for_campaign,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
METADATA = load_demo_metadata()

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")


DETAIL_PROFILE_LIMIT = 60
PERSONA_EMBEDDING_DIM = 768


BEHAVIORAL_EVENT_COUNT = int(os.environ.get("DEMO_BEHAVIORAL_EVENT_COUNT", "20000"))
_configured_event_lookback_days = int(
    os.environ.get("DEMO_BEHAVIORAL_EVENT_LOOKBACK_DAYS", "30")
)
if _configured_event_lookback_days < 1:
    raise ValueError("DEMO_BEHAVIORAL_EVENT_LOOKBACK_DAYS must be at least 1")
BEHAVIORAL_EVENT_LOOKBACK_DAYS = min(_configured_event_lookback_days, 30)
if _configured_event_lookback_days > 30:
    logger.warning(
        "DEMO_BEHAVIORAL_EVENT_LOOKBACK_DAYS=%d exceeds the 30-day S3 fixture limit; using 30 days.",
        _configured_event_lookback_days,
    )
S3_ENDPOINT_URL = os.environ.get("ANALYTICS_S3_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
S3_SESSION_TOKEN = os.environ.get("S3_SESSION_TOKEN") or os.environ.get("AWS_SESSION_TOKEN")
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
S3_VERIFY_SSL = os.environ.get("S3_VERIFY_SSL", "true").lower() == "true"
S3_AUTO_CREATE_BUCKETS = os.environ.get("S3_AUTO_CREATE_BUCKETS", "true").lower() == "true"
if not S3_VERIFY_SSL:
    from urllib3.exceptions import InsecureRequestWarning

    warnings.filterwarnings("ignore", category=InsecureRequestWarning)

BEHAVIORAL_SOURCE_SLUGS = METADATA.behavioral_source_slugs

BEHAVIORAL_EVENT_TEMPLATES = METADATA.behavioral_event_templates

MIN_EVENTS_PER_MASTER_PROFILE = 11
SUPPORTED_PROFILE_DOMAINS = (
    "travel",
    "media",
    "hospitality",
    "retail",
    "real_estate",
    "healthcare",
    "education",
)


def _table(name: str) -> str:
    return table_name(DB_SCHEMA, name)


def _profile_domain(master_profile_id: str, source_domain: str | None) -> str:
    """Return a supported domain while keeping legacy fixtures deterministic."""
    domain = canonical_demo_domain(source_domain)
    if domain in SUPPORTED_PROFILE_DOMAINS:
        return domain
    rng = stable_rng(f"profile-domain:{master_profile_id}")
    return SUPPORTED_PROFILE_DOMAINS[rng.randrange(len(SUPPORTED_PROFILE_DOMAINS))]


INDUSTRIES = METADATA.industries

ACCOUNTS = METADATA.accounts

LEAD_SOURCES = METADATA.lead_sources


CAMPAIGNS = METADATA.campaigns

LEAD_FIRST_NAMES = METADATA.lead_first_names

LEAD_LAST_NAMES = METADATA.lead_last_names


VN_PROFILE_FIRST_NAMES = METADATA.vn_profile_first_names

VN_PROFILE_LAST_NAMES = METADATA.vn_profile_last_names


EU_PROFILE_FIRST_NAMES = METADATA.eu_profile_first_names

EU_PROFILE_LAST_NAMES = METADATA.eu_profile_last_names


US_PROFILE_FIRST_NAMES = METADATA.us_profile_first_names

US_PROFILE_LAST_NAMES = METADATA.us_profile_last_names


def seed_relation_types(cursor) -> None:
    logger.info("Seeding cdp_relation_types...")
    for code, description in METADATA.relation_types:
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_relation_types')} (code, description)
            VALUES (%s, %s)
            ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;
            """,
            (code, description),
        )


def seed_crm_entities(cursor) -> dict:
    """Seed CRM entities and return IDs used by later fixture stages."""
    logger.info("Seeding CRM journey graph (industries/accounts/lead sources/leads/campaigns/...)...")
    ids: dict = {
        "industry": {}, "account": {}, "lead_source": {}, "lead": [],
        "campaign": {}, "contact": [], "contact_account_names": [], "opportunity": [],
    }

    for name, description in INDUSTRIES:
        industry_id = demo_id(f"crm_industry:{name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_industry')} (industry_id, tenant_id, name, description, keywords)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (industry_id) DO UPDATE SET description = EXCLUDED.description;
            """,
            (industry_id, DEMO_TENANT_ID, name, description, [name.lower().replace(" & ", "_").replace(" ", "_")]),
        )
        ids["industry"][name] = industry_id

    for name, industry_name in ACCOUNTS:
        account_id = demo_id(f"crm_account:{name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_account')} (account_id, tenant_id, name, industry_id, description, keywords)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id) DO UPDATE SET industry_id = EXCLUDED.industry_id;
            """,
            (account_id, DEMO_TENANT_ID, name, ids["industry"][industry_name], f"Demo account in {industry_name}.", [industry_name]),
        )
        ids["account"][name] = account_id

    for name, description in LEAD_SOURCES:
        lead_source_id = demo_id(f"crm_lead_source:{name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_lead_source')} (lead_source_id, tenant_id, name, description)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (lead_source_id) DO UPDATE SET description = EXCLUDED.description;
            """,
            (lead_source_id, DEMO_TENANT_ID, name, description),
        )
        ids["lead_source"][name] = lead_source_id

    for (name, campaign_code, status, channel, platform, objective,
         budget_vnd, start_offset, end_offset, utm_source, utm_medium) in CAMPAIGNS:
        campaign_id = demo_id(f"crm_campaign:{name}")
        today = datetime.now().date()
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_campaign')}
                (campaign_id, tenant_id, campaign_code, name, status, channel, platform,
                 objective, description, keywords, start_date, end_date,
                 budget_amount, currency, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (campaign_id) DO UPDATE SET
                status = EXCLUDED.status,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                budget_amount = EXCLUDED.budget_amount;
            """,
            (
                campaign_id, DEMO_TENANT_ID, campaign_code, name, status,
                channel, platform, objective,
                f"Demo {channel} campaign on {platform} targeting {objective}.",
                [channel.lower().replace(" ", "_"), platform.lower(), objective.lower().replace(" ", "_")],
                today + timedelta(days=start_offset),
                today + timedelta(days=end_offset),
                budget_vnd, "VND",
                Json({
                    "utm_source": utm_source,
                    "utm_medium": utm_medium,
                    "utm_campaign": campaign_code.lower(),
                    "utm_content": f"{platform.lower()}-{objective.lower().replace(' ', '_')}",
                    "tracking_platform": tracking_platform_for_campaign(platform),
                }),
            ),
        )
        ids["campaign"][name] = campaign_id

    rng = stable_rng("crm_leads")
    lead_source_names = list(ids["lead_source"].keys())
    for i in range(8):
        lead_id = demo_id(f"crm_lead:{i}")
        first_name = rng.choice(LEAD_FIRST_NAMES)
        last_name = rng.choice(LEAD_LAST_NAMES)
        source_name = rng.choice(lead_source_names)
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_lead')}
                (lead_id, tenant_id, first_name, last_name, email, phone, description, keywords, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (lead_id) DO UPDATE SET description = EXCLUDED.description;
            """,
            (
                lead_id, DEMO_TENANT_ID, first_name, last_name,
                f"demo.lead{i}@example.com", f"09{rng.randint(10000000, 99999999)}",
                f"Synthetic demo lead sourced via {source_name}.", [source_name],
                Json({"lead_source": source_name, "synthetic": True}),
            ),
        )
        ids["lead"].append(lead_id)


    industry_accounts: dict[str, list[str]] = {name: [] for name, _description in INDUSTRIES}
    for account_name, industry_name in ACCOUNTS:
        if account_name in ids["account"]:
            industry_accounts.setdefault(industry_name, []).append(account_name)

    all_seeded_accounts = list(ids["account"].keys())
    if not all_seeded_accounts:
        raise RuntimeError("No CRM accounts were seeded; verify ACCOUNTS fixture integrity.")

    fallback_account = all_seeded_accounts[0]

    def _pick_account(industry_name: str, index: int = 0) -> str:
        pool = industry_accounts.get(industry_name) or []
        if index < len(pool):
            return pool[index]
        if pool:
            return pool[0]
        return fallback_account

    contact_defs = [
        (_pick_account(industry, account_index), lead_index)
        for industry, account_index, lead_index in METADATA.contact_definitions
    ]
    for account_name, lead_index in contact_defs:
        account_id = ids["account"].get(account_name)
        if account_id is None:
            logger.warning("Skipping crm_contact seed for unknown account '%s'.", account_name)
            continue
        contact_id = demo_id(f"crm_contact:{account_name}")
        first_name = rng.choice(LEAD_FIRST_NAMES)
        last_name = rng.choice(LEAD_LAST_NAMES)
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_contact')}
                (contact_id, tenant_id, first_name, last_name, email, phone, account_id, description, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (contact_id) DO UPDATE SET account_id = EXCLUDED.account_id;
            """,
            (
                contact_id, DEMO_TENANT_ID, first_name, last_name,
                f"{first_name.lower()}.{last_name.lower()}@{account_name.lower().split()[0]}.example.com",
                f"09{rng.randint(10000000, 99999999)}", account_id,
                f"Primary contact at {account_name}, converted from a demo lead.",
                Json({"converted_from_lead_id": ids["lead"][lead_index]}),
            ),
        )
        ids["contact"].append(contact_id)
        ids["contact_account_names"].append(account_name)

    opportunity_defs = [
        (_pick_account(industry, account_index), name, value, stage, close_offset)
        for industry, account_index, name, value, stage, close_offset
        in METADATA.opportunity_definitions
    ]
    for account_name, opp_name, value, stage, close_offset in opportunity_defs:
        account_id = ids["account"].get(account_name)
        if account_id is None:
            logger.warning("Skipping crm_opportunity seed for unknown account '%s'.", account_name)
            continue
        opportunity_id = demo_id(f"crm_opportunity:{opp_name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_opportunity')}
                (opportunity_id, tenant_id, account_id, name, value, stage, close_date, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (opportunity_id) DO UPDATE SET stage = EXCLUDED.stage, value = EXCLUDED.value;
            """,
            (
                opportunity_id, DEMO_TENANT_ID, account_id, opp_name, value, stage,
                datetime.now().date() + timedelta(days=close_offset),
                f"Demo opportunity with {account_name}.",
            ),
        )
        ids["opportunity"].append(opportunity_id)


    active_campaign_names = [
        name for (name, _code, status, *_rest) in CAMPAIGNS if status in ("Active", "Completed")
    ][:3]
    for i, contact_id in enumerate(ids["contact"]):
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_campaign_member')}
                (campaign_member_id, tenant_id, campaign_id, contact_id, status, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (campaign_member_id) DO UPDATE SET status = EXCLUDED.status;
            """,
            (
                demo_id(f"crm_campaign_member:contact:{contact_id}"), DEMO_TENANT_ID,
                ids["campaign"][active_campaign_names[i % len(active_campaign_names)]],
                contact_id, "converted", "Already-converted contact who engaged with this campaign.",
            ),
        )
    for i, lead_id in enumerate(ids["lead"][:4]):
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_campaign_member')}
                (campaign_member_id, tenant_id, campaign_id, contact_id, status, description, metadata)
            VALUES (%s, %s, %s, NULL, %s, %s, %s)
            ON CONFLICT (campaign_member_id) DO UPDATE SET status = EXCLUDED.status;
            """,
            (
                demo_id(f"crm_campaign_member:lead:{lead_id}"), DEMO_TENANT_ID,
                ids["campaign"][active_campaign_names[i % len(active_campaign_names)]],
                "responded", "Lead responded to campaign but has not converted to a Contact yet.",
                Json({"lead_id": lead_id}),
            ),
        )

    return ids


_PLATFORM_PROFILE = METADATA.platform_profile
_DEFAULT_PROFILE = METADATA.default_profile

DATA_SOURCES = [source.runtime_values() for source in METADATA.data_sources]

AI_AGENT_MODELS = [agent.model_dump() for agent in METADATA.ai_agent_models]


def seed_campaign_experiments(cursor, campaign_ids: dict) -> dict[str, list[str]]:
    """Seed one deterministic two-arm experiment for the primary campaign."""
    campaign_name = CAMPAIGNS[0][0]
    campaign_id = campaign_ids.get(campaign_name)
    if campaign_id is None:
        raise RuntimeError(f"Campaign '{campaign_name}' was not seeded")

    cursor.execute(
        f"""
        SELECT segment_id
        FROM {_table('cdp_segments')}
        WHERE tenant_id = %s AND is_active = TRUE AND status_code = 1
        ORDER BY segment_name, segment_id
        LIMIT 2;
        """,
        (DEMO_TENANT_ID,),
    )
    segment_ids = [str(row["segment_id"]) for row in cursor.fetchall()]
    if len(segment_ids) < 2:
        raise RuntimeError("At least two active computed segments are required for demo A/B testing")

    campaign_key = CAMPAIGNS[0][1].lower()
    experiment_id = demo_id(f"crm_campaign_experiment:{campaign_key}")
    variant_ids = [
        demo_id(f"crm_campaign_experiment_variant:{campaign_key}:A"),
        demo_id(f"crm_campaign_experiment_variant:{campaign_key}:B"),
    ]
    today = datetime.now().date()
    cursor.execute(
        f"""
        INSERT INTO {_table('crm_campaign_experiments')}
            (experiment_id, tenant_id, campaign_id, name, status, primary_metric, start_date, winning_variant_id, created_by)
        VALUES (%s, %s, %s, %s, 'Completed', 'conversions', %s, %s, NULL)
        ON CONFLICT (experiment_id) DO UPDATE SET
            tenant_id = EXCLUDED.tenant_id,
            campaign_id = EXCLUDED.campaign_id,
            name = EXCLUDED.name,
            status = EXCLUDED.status,
            primary_metric = EXCLUDED.primary_metric,
            start_date = EXCLUDED.start_date,
            winning_variant_id = EXCLUDED.winning_variant_id,
            updated_at = NOW();
        """,
        (
            experiment_id,
            DEMO_TENANT_ID,
            campaign_id,
            "Primary audience targeting test",
            today - timedelta(days=30),
            variant_ids[1],
        ),
    )
    for variant_id, variant_key, name, segment_id, is_control in (
        (variant_ids[0], "A", "Control audience", segment_ids[0], True),
        (variant_ids[1], "B", "Expansion audience", segment_ids[1], False),
    ):
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_campaign_experiment_variants')}
                (variant_id, tenant_id, experiment_id, variant_key, name, segment_id,
                 allocation_percentage, is_control, status)
            VALUES (%s, %s, %s, %s, %s, %s, 50.00, %s, 'Completed')
            ON CONFLICT (variant_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                experiment_id = EXCLUDED.experiment_id,
                variant_key = EXCLUDED.variant_key,
                name = EXCLUDED.name,
                segment_id = EXCLUDED.segment_id,
                allocation_percentage = EXCLUDED.allocation_percentage,
                is_control = EXCLUDED.is_control,
                status = EXCLUDED.status,
                updated_at = NOW();
            """,
            (variant_id, DEMO_TENANT_ID, experiment_id, variant_key, name, segment_id, is_control),
        )
    return {str(campaign_id): variant_ids}


def seed_campaign_performance_daily(cursor, campaign_ids: dict, experiment_variants: dict[str, list[str]] | None = None) -> None:
    """Seed deterministic daily metrics for past and running campaign dates."""
    logger.info("Seeding crm_campaign_performance_daily with Adjust/GA4/C360 Tracker-style metrics...")
    today = datetime.now().date()

    for (name, campaign_code, status, channel, platform, objective,
         budget_vnd, start_offset, end_offset, utm_source, utm_medium) in CAMPAIGNS:
        campaign_id = campaign_ids.get(name)
        if campaign_id is None:
            continue

        run_start = today + timedelta(days=start_offset)
        run_end = min(today, today + timedelta(days=end_offset))
        if run_start > today:
            continue

        imp_range, ctr, cvr, rev_per_conv = _PLATFORM_PROFILE.get(platform, _DEFAULT_PROFILE)
        daily_budget = budget_vnd / max((run_end - run_start).days + 1, 1)

        rng = stable_rng(f"perf:{campaign_code}")
        current = run_start
        while current <= run_end:
            impressions = rng.randint(*imp_range)
            clicks = int(impressions * ctr * rng.uniform(0.8, 1.2))
            conversions = int(clicks * cvr * rng.uniform(0.7, 1.3))
            spend = round(daily_budget * rng.uniform(0.85, 1.05), 2)
            revenue = round(conversions * rev_per_conv * rng.uniform(0.9, 1.1), 2)

            cursor.execute(
                f"""
                INSERT INTO {_table('crm_campaign_performance_daily')}
                    (performance_id, tenant_id, campaign_id, report_date,
                     spend, impressions, clicks, conversions, revenue_estimated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, campaign_id, report_date)
                    WHERE experiment_variant_id IS NULL
                DO UPDATE SET
                    spend = EXCLUDED.spend,
                    impressions = EXCLUDED.impressions,
                    clicks = EXCLUDED.clicks,
                    conversions = EXCLUDED.conversions,
                    revenue_estimated = EXCLUDED.revenue_estimated,
                    updated_at = now();
                """,
                (
                    demo_id(f"perf:{campaign_code}:{current.isoformat()}"),
                    DEMO_TENANT_ID, campaign_id, current,
                    spend, impressions, clicks, conversions, revenue,
                ),
            )
            for variant_index, variant_id in enumerate((experiment_variants or {}).get(str(campaign_id), [])):
                cursor.execute(
                    f"""
                    INSERT INTO {_table('crm_campaign_performance_daily')}
                        (performance_id, tenant_id, campaign_id, report_date, experiment_variant_id,
                         spend, impressions, clicks, conversions, revenue_estimated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, campaign_id, report_date, experiment_variant_id)
                        WHERE experiment_variant_id IS NOT NULL
                    DO UPDATE SET
                        spend = EXCLUDED.spend,
                        impressions = EXCLUDED.impressions,
                        clicks = EXCLUDED.clicks,
                        conversions = EXCLUDED.conversions,
                        revenue_estimated = EXCLUDED.revenue_estimated,
                        updated_at = NOW();
                    """,
                    (
                        demo_id(f"perf:{campaign_code}:variant:{variant_id}:{current.isoformat()}"),
                        DEMO_TENANT_ID,
                        campaign_id,
                        current,
                        variant_id,
                        round(spend * (0.48 + variant_index * 0.04), 2),
                        int(impressions * (0.48 + variant_index * 0.04)),
                        int(clicks * (0.48 + variant_index * 0.04)),
                        int(conversions * (0.45 + variant_index * 0.15)),
                        round(revenue * (0.45 + variant_index * 0.15), 2),
                    ),
                )
            current += timedelta(days=1)


def seed_data_sources(cursor) -> None:
    """Seeds tenant-scoped rows in sys_data_source used by metadata/data-sources."""
    logger.info("Seeding sys_data_source catalog for demo tenant...")
    for data_source in DATA_SOURCES:
        data_source_id = demo_id(f"sys_data_source:{data_source['slug']}")
        cursor.execute(
            f"""
            INSERT INTO {_table('sys_data_source')}
                (data_source_id, tenant_id, name, slug, source_type, status,
                 data_source_url, thumbnail_url, collect_directly, first_party_data,
                 journey_level, journey_map_id, touchpoint_hub_id, security_code,
                 total_tracked_event, avg_daily_event, avg_events_per_profile,
                 access_tokens, data_source_hosts,
                 javascript_tags, qr_code_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, slug) DO UPDATE SET
                name = EXCLUDED.name,
                source_type = EXCLUDED.source_type,
                status = EXCLUDED.status,
                data_source_url = EXCLUDED.data_source_url,
                thumbnail_url = EXCLUDED.thumbnail_url,
                collect_directly = EXCLUDED.collect_directly,
                first_party_data = EXCLUDED.first_party_data,
                journey_level = EXCLUDED.journey_level,
                journey_map_id = EXCLUDED.journey_map_id,
                touchpoint_hub_id = EXCLUDED.touchpoint_hub_id,
                security_code = EXCLUDED.security_code,
                total_tracked_event = EXCLUDED.total_tracked_event,
                avg_daily_event = EXCLUDED.avg_daily_event,
                avg_events_per_profile = EXCLUDED.avg_events_per_profile,
                access_tokens = EXCLUDED.access_tokens,
                data_source_hosts = EXCLUDED.data_source_hosts,
                javascript_tags = EXCLUDED.javascript_tags,
                qr_code_data = EXCLUDED.qr_code_data,
                updated_at = now();
            """,
            (
                data_source_id,
                DEMO_TENANT_ID,
                data_source["name"],
                data_source["slug"],
                data_source["source_type"],
                data_source["status"],
                data_source["data_source_url"],
                data_source["thumbnail_url"],
                data_source["collect_directly"],
                data_source["first_party_data"],
                data_source["journey_level"],
                data_source["journey_map_id"],
                data_source["touchpoint_hub_id"],
                data_source["security_code"],
                data_source["total_tracked_event"],
                data_source["avg_daily_event"],
                data_source["avg_events_per_profile"],
                Json(data_source["access_tokens"]),
                data_source["data_source_hosts"],
                data_source["javascript_tags"],
                Json(data_source["qr_code_data"]),
            ),
        )


def update_data_source_statistics(cursor, statistics_by_source: dict[str, dict[str, Any]]) -> None:
    """Persist statistics calculated from the event objects written to S3."""
    for source_id, statistics in statistics_by_source.items():
        cursor.execute(
            f"""
            UPDATE {_table('sys_data_source')}
            SET total_tracked_event = %s,
                avg_daily_event = %s,
                avg_events_per_profile = %s,
                updated_at = now()
            WHERE tenant_id = %s
              AND data_source_id = %s
              AND status = 1;
            """,
            (
                statistics["total_tracked_event"],
                statistics["avg_daily_event"],
                statistics["avg_events_per_profile"],
                DEMO_TENANT_ID,
                source_id,
            ),
        )


def seed_ai_agents(cursor) -> None:
    """Seeds demo model rows in the unified cdp_ai_agents catalog."""
    logger.info("Seeding cdp_ai_agents model catalog...")
    for model in AI_AGENT_MODELS:
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_ai_agents')}
                (agent_code, display_name, description, model_type, status,
                 schedule_definition, input_features, hyperparameters)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_code) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                model_type = EXCLUDED.model_type,
                status = EXCLUDED.status,
                schedule_definition = EXCLUDED.schedule_definition,
                input_features = EXCLUDED.input_features,
                hyperparameters = EXCLUDED.hyperparameters,
                updated_at = now();
            """,
            (
                model["agent_code"],
                model["display_name"],
                model["description"],
                model["model_type"],
                model["status"],
                model["schedule_definition"],
                model["input_features"],
                Json(model["hyperparameters"]),
            ),
        )


def reset_tenant_scoped_demo_tables(cursor) -> None:
    logger.info("Resetting previous demo rows in tenant-scoped tables (relations/contacts/transactions)...")
    cursor.execute(f"DELETE FROM {_table('cdp_relations')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_customer_contacts')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_transactions')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_campaign_performance_daily')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_campaign_experiment_variants')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_campaign_experiments')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('graph_edges')} WHERE metadata->>'demo_tenant' = %s;", (DEMO_TENANT_ID,))


def seed_relations(cursor, master_profiles: list) -> None:
    if len(master_profiles) < 4:
        logger.warning("Not enough master profiles to seed cdp_relations demo rows -- skipping.")
        return
    logger.info("Seeding cdp_relations between resolved master profiles...")

    def _link(a, b, code):
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_relations')}
                (tenant_id, source_master_id, target_master_id, relation_type_id)
            SELECT %s, %s, %s, relation_type_id FROM {_table('cdp_relation_types')} WHERE code = %s
            ON CONFLICT (tenant_id, source_master_id, target_master_id, relation_type_id) DO NOTHING;
            """,
            (DEMO_TENANT_ID, a, b, code),
        )

    by_domain = {}
    for m in master_profiles:
        normalized_domain = _profile_domain(m["master_profile_id"], m["domain"])
        by_domain.setdefault(normalized_domain, []).append(m)
    domains = list(by_domain.keys())


    for domain, members in by_domain.items():
        if len(members) >= 2:
            _link(members[0]["master_profile_id"], members[1]["master_profile_id"], "friend")


    if len(domains) >= 2 and by_domain[domains[0]] and by_domain[domains[1]]:
        _link(
            by_domain[domains[0]][0]["master_profile_id"],
            by_domain[domains[1]][0]["master_profile_id"],
            "customer-contact",
        )


def seed_customer_contacts(cursor, master_profiles: list) -> None:
    logger.info("Seeding crm_customer_contacts (CS/call-center interaction log)...")
    channels = ("call_center", "live_chat", "email", "branch_visit")
    types = ("inquiry", "complaint", "feedback", "support_request")
    for m in master_profiles:
        rng = stable_rng(f"contacts:{m['master_profile_id']}")
        for _ in range(rng.randint(1, 3)):
            cursor.execute(
                f"""
                INSERT INTO {_table('crm_customer_contacts')}
                    (contact_id, tenant_id, master_profile_id, contact_type, contact_channel,
                     contact_content, contact_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    str(uuid.uuid4()), DEMO_TENANT_ID, m["master_profile_id"],
                    rng.choice(types), rng.choice(channels),
                    "Synthetic demo interaction log entry.",
                    datetime.now() - timedelta(days=realistic_event_days_ago(rng), hours=rng.randint(0, 23)),
                ),
            )


DOMAIN_TRANSACTION_CATALOG = METADATA.domain_transaction_catalog


def seed_transactions(cursor, master_profiles: list) -> None:
    logger.info("Seeding crm_transactions per domain...")
    for m in master_profiles:
        rng = stable_rng(f"transactions:{m['master_profile_id']}")
        domain = _profile_domain(m["master_profile_id"], m["domain"])
        catalog = DOMAIN_TRANSACTION_CATALOG.get(domain)
        if catalog is None:
            continue


        for _ in range(rng.randint(2, 5)):
            source_system, txn_type, entity_type, entity_name, amount_range, channel = rng.choice(catalog)
            cursor.execute(
                f"""
                INSERT INTO {_table('crm_transactions')}
                    (transaction_id, tenant_id, master_profile_id, source_system, transaction_type,
                     transaction_status, entity_type, entity_name, amount, currency, channel,
                     transaction_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    str(uuid.uuid4()), DEMO_TENANT_ID, m["master_profile_id"], source_system, txn_type,
                    "completed", entity_type, entity_name, rng.randint(*amount_range), "VND", channel,
                    datetime.now() - timedelta(days=realistic_event_days_ago(rng), hours=rng.randint(0, 23)),
                ),
            )


    rng = stable_rng("unresolved_transactions")
    for i in range(2):
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_transactions')}
                (transaction_id, tenant_id, master_profile_id, source_system, source_transaction_id,
                 transaction_type, transaction_status, entity_type, amount, currency, channel,
                 transaction_time)
            VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, source_system, source_transaction_id) WHERE source_transaction_id IS NOT NULL
            DO NOTHING;
            """,
            (
                str(uuid.uuid4()), DEMO_TENANT_ID, "POS", f"pos-unresolved-{i}",
                "purchase", "completed", "product", rng.randint(50_000, 500_000), "VND", "pos",
                datetime.now() - timedelta(hours=rng.randint(1, 48)),
            ),
        )


def seed_graph_edges(cursor, crm_ids: dict, master_profiles: list) -> None:
    logger.info("Seeding graph_edges (belongs_to/converted/has/belongs_to_industry/is_connected_to/is_from)...")
    metadata = Json({"demo_tenant": DEMO_TENANT_ID})
    edges = []
    if crm_ids["contact"] and crm_ids["account"]:
        account_id = list(crm_ids["account"].values())[0]
        edges.append(("belongs_to", crm_ids["contact"][0], "crm_contact", account_id, "crm_account"))
        edges.append(("belongs_to_industry", account_id, "crm_account", list(crm_ids["industry"].values())[0], "crm_industry"))
    if crm_ids["lead"] and crm_ids["contact"]:
        edges.append(("converted", crm_ids["lead"][0], "crm_lead", crm_ids["contact"][0], "crm_contact"))
    if crm_ids["lead"] and crm_ids["lead_source"]:
        edges.append(("is_from", crm_ids["lead"][0], "crm_lead", list(crm_ids["lead_source"].values())[0], "crm_lead_source"))
    if crm_ids["account"] and crm_ids["opportunity"]:
        edges.append(("has", list(crm_ids["account"].values())[0], "crm_account", crm_ids["opportunity"][0], "crm_opportunity"))
    if len(master_profiles) >= 2:
        edges.append((
            "is_connected_to", master_profiles[0]["master_profile_id"], "cdp_master_profiles",
            master_profiles[1]["master_profile_id"], "cdp_master_profiles",
        ))

    for relation, from_id, from_type, to_id, to_type in edges:
        cursor.execute(
            f"""
            INSERT INTO {_table('graph_edges')}
                (tenant_id, from_id, to_id, from_type, to_type, relation, description, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                DEMO_TENANT_ID,
                from_id,
                to_id,
                from_type,
                to_type,
                relation,
                f"Demo edge: {from_type} -{relation}-> {to_type}.",
                metadata,
            ),
        )


CONTACT_MASTER_LINK_ACCOUNTS = METADATA.contact_master_link_accounts


def link_crm_contacts_to_master_profiles(cursor, crm_ids: dict, master_profiles: list) -> None:
    """Link profiles to CRM contacts through graph edges and JSONB references."""
    domain_pools = {
        domain: [
            m["master_profile_id"]
            for m in master_profiles
            if _profile_domain(m["master_profile_id"], m["domain"]) == domain
        ]
        for domain in SUPPORTED_PROFILE_DOMAINS
    }
    contacts = crm_ids["contact"]
    contact_account_names = crm_ids.get("contact_account_names") or []

    metadata = Json({"demo_tenant": DEMO_TENANT_ID})
    links = []
    for _account_name, contact_index, domain, pool_index in CONTACT_MASTER_LINK_ACCOUNTS:
        pool = domain_pools.get(domain)
        if pool is None or contact_index >= len(contacts) or pool_index >= len(pool):
            continue
        account_name = (
            contact_account_names[contact_index]
            if contact_index < len(contact_account_names)
            else _account_name
        )
        links.append((pool[pool_index], contacts[contact_index], account_name))

    if not links:
        logger.warning("Not enough resolved master profiles/CRM contacts to link -- skipping.")
        return

    logger.info("Linking %d crm_contact row(s) to real resolved cdp_master_profiles rows...", len(links))
    for master_id, contact_id, account_name in links:
        cursor.execute(
            f"""
            INSERT INTO {_table('graph_edges')}
                (tenant_id, from_id, to_id, from_type, to_type, relation, description, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                DEMO_TENANT_ID,
                master_id,
                contact_id,
                "cdp_master_profiles",
                "crm_contact",
                "is_active_as",
                f"This resolved consumer profile is also the B2B contact/decision-maker at {account_name}.",
                metadata,
            ),
        )
        cursor.execute(
            f"""
            UPDATE {_table('cdp_master_profiles')}
            SET attributes = COALESCE(attributes, '{{}}'::jsonb) || %s
            WHERE master_profile_id = %s;
            """,
            (Json({"linked_crm_contact_id": contact_id}), master_id),
        )
        cursor.execute(
            f"""
            UPDATE {_table('crm_contact')}
            SET metadata = COALESCE(metadata, '{{}}'::jsonb) || %s
            WHERE contact_id = %s;
            """,
            (Json({"linked_master_profile_id": master_id}), contact_id),
        )


LIFECYCLE_STAGES = METADATA.lifecycle_stages
OCCUPATIONS = METADATA.occupations
INCOME_SEGMENTS = METADATA.income_segments
CITIES = METADATA.cities

DOMAIN_PREFERRED_CHANNELS = METADATA.domain_preferred_channels

DOMAIN_CLV_CONFIG = {domain: config.model_dump() for domain, config in METADATA.domain_clv_config.items()}


def _make_persona_summary(domain: str, lifecycle_stage: str, preferred_channel: str, rng: random.Random) -> str:
    flavor = rng.choice(METADATA.persona_summary_flavors)
    return (
        f"{domain.capitalize()} profile who {flavor}; primarily engages via {preferred_channel}; "
        f"currently in the '{lifecycle_stage}' lifecycle stage."
    )


def enrich_master_profiles(cursor, master_profiles: list) -> None:
    logger.info("Enriching %d master profiles with lifecycle/ML-scoring/domain-specific fields...", len(master_profiles))
    for m in master_profiles:
        master_id = m["master_profile_id"]
        domain = _profile_domain(m["master_profile_id"], m["domain"])
        rng = stable_rng(f"enrich:{master_id}")

        lifecycle_stage = rng.choice(LIFECYCLE_STAGES)
        is_established_customer = lifecycle_stage in ("customer", "vip", "dormant", "churn_risk")
        preferred_channel = rng.choice(
            DOMAIN_PREFERRED_CHANNELS.get(domain, DOMAIN_PREFERRED_CHANNELS["retail"])
        )

        customer_since = (
            (m["created_at"] - timedelta(days=rng.randint(0, 365))).date() if is_established_customer else None
        )
        last_activity_at = datetime.now() - timedelta(days=rng.randint(0, 30), hours=rng.randint(0, 23))

        num_sources = len(m.get("source_systems") or [])
        identity_confidence_score = min(1.0, round(0.5 + 0.15 * num_sources, 4))

        churn_rand = rng.random()
        if churn_rand < 0.65:
            churn_probability = round(rng.uniform(0.0, 0.25), 4)
        elif churn_rand < 0.85:
            churn_probability = round(rng.uniform(0.25, 0.55), 4)
        elif churn_rand < 0.95:
            churn_probability = round(rng.uniform(0.55, 0.80), 4)
        else:
            churn_probability = round(rng.uniform(0.80, 1.0), 4)
        churn_risk_tier = (
            "critical" if churn_probability >= 0.85 else
            "high" if churn_probability >= 0.6 else
            "medium" if churn_probability >= 0.3 else "low"
        )
        lead_conversion_probability = round(rng.uniform(0, 1), 4)
        lead_grade = "Hot" if lead_conversion_probability >= 0.7 else "Warm" if lead_conversion_probability >= 0.4 else "Cold"
        clv_config = DOMAIN_CLV_CONFIG.get(domain, DOMAIN_CLV_CONFIG["retail"])
        historical_clv = round(rng.uniform(500, 5000) * clv_config["multiplier"], 2)
        predictive_clv = round(historical_clv * rng.uniform(1.0, 1.8), 2)
        clv_high_threshold = clv_config["high"]
        clv_medium_threshold = clv_config["medium"]
        clv_segment = "high" if predictive_clv > clv_high_threshold else "medium" if predictive_clv > clv_medium_threshold else "low"
        engagement_score = round(rng.uniform(0, 100), 2)
        latest_nps_score = rng.randint(0, 10)
        average_csat = round(rng.uniform(1, 5), 2)
        overall_sentiment_score = round(rng.uniform(-1, 1), 4)
        profile_completeness_score = round(rng.uniform(40, 100), 2)
        segmentation_tags = [domain, lifecycle_stage, clv_segment + "_value"]
        communication_preferences = Json(
            {
                "email_opt_in": rng.random() < 0.7,
                "sms_opt_in": rng.random() < 0.45,
                "push_opt_in": rng.random() < 0.8,
            }
        )
        attributes = Json({"occupation": rng.choice(OCCUPATIONS), "income_segment": rng.choice(INCOME_SEGMENTS)})
        model_versions = Json({
            "churn_model": "v1", "clv_model": "v1", "lead_scoring_model": "v1",
            "cx_scoring_model": "v1", "data_quality_model": "v1",
            "identity_resolution_scoring_model": "v1",
        })
        first_name, last_name, full_name, _name_locale = build_global_profile_name(rng, METADATA)
        gender = rng.choice(("male", "female", "other"))
        address = Json({"city": rng.choice(CITIES), "country": "VN"})
        profile_picture_url = f"https://api.dicebear.com/7.x/identicon/svg?seed={master_id}"
        persona_summary = _make_persona_summary(domain, lifecycle_stage, preferred_channel, rng)

        set_clauses = [
            "domain = %s", "lifecycle_stage = %s", "preferred_channel = %s", "customer_since = %s",
            "last_activity_at = %s", "churn_probability = %s", "churn_risk_tier = %s",
            "lead_conversion_probability = %s", "lead_grade = %s", "historical_clv = %s",
            "predictive_clv = %s", "clv_segment = %s", "engagement_score = %s",
            "latest_nps_score = %s", "average_csat = %s", "overall_sentiment_score = %s",
            "profile_completeness_score = %s", "identity_confidence_score = %s",
            "segmentation_tags = %s", "communication_preferences = COALESCE(communication_preferences, '{}'::jsonb) || %s",
            "attributes = COALESCE(attributes, '{}'::jsonb) || %s",
            "model_versions = %s", "scores_updated_at = NOW()", "gender = %s", "address = %s",
            "profile_picture_url = %s", "persona_summary = %s",
            "full_name = %s", "first_name = %s", "last_name = %s",


            f"""acquisition_source = COALESCE(acquisition_source, (
                SELECT media_source FROM {_table('cdp_raw_profiles_stage')}
                WHERE raw_profile_id = {_table('cdp_master_profiles')}.first_seen_raw_profile_id
            ))""",
            f"""acquisition_campaign = COALESCE(acquisition_campaign, (
                SELECT campaign FROM {_table('cdp_raw_profiles_stage')}
                WHERE raw_profile_id = {_table('cdp_master_profiles')}.first_seen_raw_profile_id
            ))""",
        ]
        params = [
            domain, lifecycle_stage, preferred_channel, customer_since, last_activity_at,
            churn_probability, churn_risk_tier, lead_conversion_probability, lead_grade,
            historical_clv, predictive_clv, clv_segment, engagement_score, latest_nps_score,
            average_csat, overall_sentiment_score, profile_completeness_score,
            identity_confidence_score, segmentation_tags, communication_preferences, attributes, model_versions,
            gender, address, profile_picture_url, persona_summary,
            full_name, first_name, last_name,
        ]
        domain_attributes: dict[str, object] = {}

        if domain == "retail":
            email = f"{email_token(first_name)}.{email_token(last_name)}.{master_id[:8]}@example.com"
            phone_number = f"09{rng.randint(10000000, 99999999)}"
            set_clauses.extend([
                "email = %s", "phone_number = %s", "is_hashed = FALSE",
            ])
            params.extend([email, phone_number])

            domain_attributes = {
                "loyalty_id": f"LOY-{master_id[:8]}",
                "membership_tier": rng.choice(("Silver", "Gold", "Platinum")),
                "preferred_store_code": f"STORE-{rng.randint(1, 20):03d}",
            }
        elif domain == "travel":
            domain_attributes = {
                "travel_loyalty_program_id": f"TVL-{rng.randint(100000, 999999)}",
                "preferred_travel_class": rng.choice(("economy", "business", "first")),
            }
        elif domain == "media":
            domain_attributes = {
                "media_subscription_id": f"SUB-{rng.randint(100000, 999999)}",
                "preferred_content_genres": rng.sample(
                    ["news", "sports", "entertainment", "documentary", "music"],
                    k=rng.randint(1, 3),
                ),
            }
        elif domain == "hospitality":
            domain_attributes = {
                "hospitality_loyalty_id": f"HSP-{rng.randint(100000, 999999)}",
                "preferred_experience": rng.choice(("hotel", "restaurant", "resort", "cafe")),
                "dietary_preferences": rng.sample(
                    ["vegetarian", "local_cuisine", "seafood", "healthy_options"],
                    k=rng.randint(1, 2),
                ),
            }
        elif domain == "real_estate":
            domain_attributes = {
                "property_types_of_interest": rng.sample(
                    ["apartment", "villa", "land", "townhouse", "condo"],
                    k=rng.randint(1, 3),
                ),
                "preferred_location_codes": [f"DIST-{rng.randint(1, 12):02d}" for _ in range(rng.randint(1, 2))],
            }
        elif domain == "healthcare":
            domain_attributes = {
                "patient_program_id": f"CARE-{rng.randint(100000, 999999)}",
                "care_preferences": rng.sample(
                    ["telehealth", "preventive_care", "chronic_care", "wellness"],
                    k=rng.randint(1, 2),
                ),
            }
        elif domain == "education":
            domain_attributes = {
                "student_id": f"STU-{rng.randint(100000, 999999)}",
                "institution_name": rng.choice(
                    ("Demo University", "Demo Online Academy", "Demo Polytechnic")
                ),
                "learning_mode": rng.choice(("self_paced", "instructor_led", "hybrid")),
                "course_completion_rate": round(rng.uniform(0.35, 0.98), 4),
                "enrolled_programs": rng.sample(
                    ["Data Analytics Certificate", "AI Foundations", "Digital Marketing", "Business English"],
                    k=rng.randint(1, 2),
                ),
            }


        params.append(master_id)
        cursor.execute(
            f"UPDATE {_table('cdp_master_profiles')} SET {', '.join(set_clauses)} WHERE master_profile_id = %s;",
            tuple(params),
        )

        if domain_attributes:
            cursor.execute(
                f"""
                INSERT INTO {_table('cdp_domain_profiles')} (
                    tenant_id,
                    master_profile_id,
                    domain_id,
                    profile_name,
                    lifecycle_stage,
                    persona_name,
                    persona_summary,
                    engagement_score,
                    domain_attributes,
                    first_activity_at,
                    last_activity_at,
                    status_code,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    (SELECT domain_id FROM {_table('sys_domain')} WHERE domain_code = %s LIMIT 1),
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    1,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (master_profile_id, domain_id)
                DO UPDATE SET
                    profile_name = EXCLUDED.profile_name,
                    lifecycle_stage = EXCLUDED.lifecycle_stage,
                    persona_name = EXCLUDED.persona_name,
                    persona_summary = EXCLUDED.persona_summary,
                    engagement_score = EXCLUDED.engagement_score,
                    domain_attributes = COALESCE(cdp_domain_profiles.domain_attributes, '{{}}'::jsonb) || EXCLUDED.domain_attributes,
                    first_activity_at = EXCLUDED.first_activity_at,
                    last_activity_at = EXCLUDED.last_activity_at,
                    status_code = EXCLUDED.status_code,
                    updated_at = NOW();
                """,
                (
                    DEMO_TENANT_ID,
                    master_id,
                    domain,
                    f"{domain.title()} Profile",
                    lifecycle_stage,
                    None,
                    None,
                    engagement_score,
                    Json(domain_attributes),
                    customer_since,
                    last_activity_at,
                ),
            )


ICP_ARCHETYPES = [archetype.model_dump() for archetype in METADATA.icp_archetypes]


def seed_persona_archetypes(cursor) -> dict:
    """Upsert shared ICP archetypes with deterministic synthetic embeddings."""
    logger.info("Seeding %d ICP persona archetypes across every sys_domain...", len(ICP_ARCHETYPES))
    archetypes_by_domain: dict[str, list] = {}
    for icp in ICP_ARCHETYPES:
        embedding_rng = stable_rng(f"persona_archetype_embedding:{icp['persona_code']}")
        vector_literal = (
            "[" + ",".join(f"{embedding_rng.uniform(-1, 1):.6f}" for _ in range(PERSONA_EMBEDDING_DIM)) + "]"
        )
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_persona_archetypes')} (
                tenant_id, domain, persona_code, persona_name, persona_category, persona_summary,
                llm_provider, llm_model, persona_embedding,
                centroid_behavior_score, centroid_engagement_score, centroid_financial_score,
                centroid_loyalty_score, centroid_relationship_score, centroid_risk_score
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector({PERSONA_EMBEDDING_DIM}), %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, domain, persona_code) DO UPDATE SET
                persona_name = EXCLUDED.persona_name,
                persona_category = EXCLUDED.persona_category,
                persona_summary = EXCLUDED.persona_summary,
                llm_provider = EXCLUDED.llm_provider,
                llm_model = EXCLUDED.llm_model,
                persona_embedding = EXCLUDED.persona_embedding,
                centroid_behavior_score = EXCLUDED.centroid_behavior_score,
                centroid_engagement_score = EXCLUDED.centroid_engagement_score,
                centroid_financial_score = EXCLUDED.centroid_financial_score,
                centroid_loyalty_score = EXCLUDED.centroid_loyalty_score,
                centroid_relationship_score = EXCLUDED.centroid_relationship_score,
                centroid_risk_score = EXCLUDED.centroid_risk_score,
                updated_at = NOW()
            RETURNING persona_archetype_id;
            """,
            (
                DEMO_TENANT_ID, icp["domain"], icp["persona_code"], icp["persona_name"],
                icp["persona_category"], icp["persona_summary"], "seed-script", "icp-catalog-v1",
                vector_literal,
                icp["centroid"]["behavior"], icp["centroid"]["engagement"], icp["centroid"]["financial"],
                icp["centroid"]["loyalty"], icp["centroid"]["relationship"], icp["centroid"]["risk"],
            ),
        )
        persona_archetype_id = cursor.fetchone()["persona_archetype_id"]
        archetypes_by_domain.setdefault(icp["domain"], []).append(
            {
                "persona_archetype_id": persona_archetype_id,
                "persona_name": icp["persona_name"],
                "persona_summary": icp["persona_summary"],
                "persona_category": icp["persona_category"],
                "centroid_behavior_score": icp["centroid"]["behavior"],
                "centroid_engagement_score": icp["centroid"]["engagement"],
                "centroid_financial_score": icp["centroid"]["financial"],
                "centroid_loyalty_score": icp["centroid"]["loyalty"],
                "centroid_relationship_score": icp["centroid"]["relationship"],
                "centroid_risk_score": icp["centroid"]["risk"],
            }
        )
    return archetypes_by_domain


def _lookalike_match(computation, archetypes: list):
    """Find the domain archetype closest to the profile's six component scores."""
    profile_vector = [
        computation.behavior_score, computation.engagement_score, computation.financial_score,
        computation.loyalty_score, computation.relationship_score, computation.risk_score,
    ]
    best_archetype = None
    best_score = -1.0
    for archetype in archetypes:
        centroid_vector = [
            archetype["centroid_behavior_score"], archetype["centroid_engagement_score"],
            archetype["centroid_financial_score"], archetype["centroid_loyalty_score"],
            archetype["centroid_relationship_score"], archetype["centroid_risk_score"],
        ]
        similarity = _cosine_similarity(profile_vector, centroid_vector)
        if similarity > best_score:
            best_score = similarity
            best_archetype = archetype
    return best_archetype, round(max(best_score, 0.0), 4)


def seed_customer_personas(cursor, master_profiles: list, archetypes_by_domain: dict) -> int:
    """Persist versioned persona matches using the shared persona engine."""
    logger.info(
        "Computing personas + lookalike-matching %d master profiles against %d ICP archetypes...",
        len(master_profiles), sum(len(v) for v in archetypes_by_domain.values()),
    )
    engine = PersonaResolutionEngine(schema=DB_SCHEMA)
    engine._ensure_runtime_persona_config(cursor)
    computed = 0
    unmatched_domains = set()

    for m in master_profiles:
        master_profile = engine._fetch_master_profile(cursor, DEMO_TENANT_ID, m["master_profile_id"])
        if master_profile is None:
            continue

        domain = _profile_domain(m["master_profile_id"], master_profile.get("domain"))
        archetypes = archetypes_by_domain.get(domain)
        if not archetypes:
            unmatched_domains.add(domain)
            continue

        computation = compute_persona(master_profile)
        best_archetype, match_score = _lookalike_match(computation, archetypes)
        assert best_archetype is not None
        computation.match_score = match_score


        computation.persona_name = best_archetype["persona_name"]
        computation.persona_summary = best_archetype["persona_summary"]
        computation.persona_category = best_archetype["persona_category"]

        old_persona = engine._fetch_current_persona(cursor, DEMO_TENANT_ID, m["master_profile_id"])
        computed_version = engine._next_computed_version(
            cursor, DEMO_TENANT_ID, m["master_profile_id"], best_archetype["persona_archetype_id"]
        )
        engine._deactivate_previous_personas(cursor, DEMO_TENANT_ID, m["master_profile_id"])
        persona_id = engine._insert_persona(
            cursor, DEMO_TENANT_ID, domain, m["master_profile_id"], best_archetype["persona_archetype_id"],
            computation, computed_version,
        )
        engine._insert_features(cursor, persona_id, computation.features)
        engine._insert_score_details(cursor, persona_id, computation)
        if engine._should_insert_history(old_persona, computation):
            engine._insert_history(cursor, persona_id, old_persona, computation)
        engine._update_master_profile(cursor, DEMO_TENANT_ID, m["master_profile_id"], persona_id, computation)
        computed += 1

    if unmatched_domains:
        logger.warning(
            "No ICP archetypes seeded for domain(s) %s -- skipped persona matching for those profiles.",
            sorted(unmatched_domains),
        )
    logger.info("Computed %d personas, each lookalike-matched to a shared ICP archetype.", computed)
    return computed


def fetch_master_profiles(cursor) -> list:
    cursor.execute(
        f"""
        SELECT master_profile_id, domain, segmentation_tags, source_systems, first_seen_raw_profile_id, created_at
        FROM {_table('cdp_master_profiles')}
        WHERE tenant_id = %s
        ORDER BY created_at;
        """,
        (DEMO_TENANT_ID,),
    )
    return cursor.fetchall()


def fetch_event_profiles(cursor) -> list:
    """Return active raw-profile links with their resolved master profiles."""
    cursor.execute(
        f"""
        SELECT m.master_profile_id, m.domain, m.created_at,
               r.raw_profile_id, r.source_system, r.channel,
               r.external_customer_id, r.device_id, r.advertising_id,
               r.cookie_id, r.platform
        FROM {_table('cdp_master_profiles')} m
        JOIN {_table('cdp_profile_links')} l
          ON l.tenant_id = m.tenant_id
         AND l.master_profile_id = m.master_profile_id
         AND l.status = 'ACTIVE'
        JOIN {_table('cdp_raw_profiles_stage')} r
          ON r.tenant_id = l.tenant_id
         AND r.raw_profile_id = l.raw_profile_id
        WHERE m.tenant_id = %s
          AND m.status_code = 1
        ORDER BY m.master_profile_id, r.raw_profile_id;
        """,
        (DEMO_TENANT_ID,),
    )
    return cursor.fetchall()


def _build_demo_s3_client() -> Any:
    import boto3
    from botocore.client import Config

    client_kwargs: dict[str, Any] = {
        "region_name": S3_REGION,
        "verify": S3_VERIFY_SSL,
        "config": Config(
            s3={"addressing_style": "path" if S3_FORCE_PATH_STYLE else "auto"}
        ),
    }
    if S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = S3_ENDPOINT_URL
    if S3_ACCESS_KEY_ID:
        client_kwargs["aws_access_key_id"] = S3_ACCESS_KEY_ID
    if S3_SECRET_ACCESS_KEY:
        client_kwargs["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY
    if S3_SESSION_TOKEN:
        client_kwargs["aws_session_token"] = S3_SESSION_TOKEN
    return boto3.client("s3", **client_kwargs)


def _ensure_demo_s3_bucket(client: Any, bucket: str) -> None:
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise RuntimeError(f"Could not access demo event bucket {bucket}") from exc
        if not S3_AUTO_CREATE_BUCKETS:
            raise RuntimeError(f"Demo event bucket does not exist: {bucket}") from exc
    except BotoCoreError as exc:
        raise RuntimeError(f"Could not access demo event bucket {bucket}") from exc

    create_kwargs: dict[str, Any] = {"Bucket": bucket}
    if S3_REGION != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": S3_REGION}
    try:
        client.create_bucket(**create_kwargs)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
            raise RuntimeError(f"Could not create demo event bucket {bucket}") from exc


def _clear_demo_event_objects(client: Any, bucket: str) -> None:
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        paginator = client.get_paginator("list_objects_v2")
        keys = [
            str(item["Key"])
            for page in paginator.paginate(Bucket=bucket, Prefix="events/")
            for item in page.get("Contents", [])
            if "-demo-behavioral-" in str(item.get("Key", ""))
        ]
        for start in range(0, len(keys), 1000):
            client.delete_objects(
                Bucket=bucket,
                Delete={
                    "Objects": [{"Key": key} for key in keys[start : start + 1000]],
                    "Quiet": True,
                },
            )
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"Could not clear prior demo event objects from {bucket}") from exc


def _event_source_slug(source_system: str | None) -> str:
    return BEHAVIORAL_SOURCE_SLUGS.get(
        (source_system or "Adjust").strip().lower(),
        "adjust-mobile-attribution",
    )


def _event_output_source(source_system: str | None) -> str:
    return {
        "adjust": "Adjust",
        "onesignal": "OneSignal",
        "webtracking": "GoogleAnalytics",
    }.get((source_system or "Adjust").strip().lower(), source_system or "Adjust")


def _build_behavioral_event(
    profile: dict,
    raw_profile: dict,
    event_index: int,
    event_time: datetime,
) -> dict[str, Any]:
    domain = _profile_domain(str(profile["master_profile_id"]), profile.get("domain"))
    templates = BEHAVIORAL_EVENT_TEMPLATES.get(domain, BEHAVIORAL_EVENT_TEMPLATES["retail"])
    rng = stable_rng(f"behavioral-event:{DEMO_TENANT_ID}:{event_index}")
    event_name, event_category, entity_type, is_conversion, channel = rng.choice(templates)
    device_type = "mobile" if channel == "mobile_app" else "desktop"
    source_system = _event_output_source(raw_profile.get("source_system"))
    event_id = str(uuid.uuid5(DEMO_NAMESPACE, f"behavioral-event:{DEMO_TENANT_ID}:{event_index}"))
    session_id = f"demo-session-{event_index // 5:06d}"
    entity_id = f"demo-{entity_type}-{rng.randint(1, 5000):05d}"
    event_value = round(rng.uniform(150_000, 3_000_000), 2) if is_conversion else None
    payload = {
        "event_id": event_id,
        "event_time": event_time.isoformat(),
        "tenant_id": DEMO_TENANT_ID,
        "domain": domain,
        "master_profile_id": str(profile["master_profile_id"]),
        "raw_profile_id": str(raw_profile["raw_profile_id"]),
        "external_customer_id": raw_profile.get("external_customer_id"),
        "device_id": raw_profile.get("device_id"),
        "session_id": session_id,
        "source_system": source_system,
        "channel": channel or raw_profile.get("channel"),
        "device_type": device_type,
        "platform": raw_profile.get("platform"),
        "event_category": event_category,
        "event_name": event_name,
        "is_conversion": is_conversion,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "event_value": event_value,
        "currency": "VND",
        "transaction_id": event_id if is_conversion else None,
        "transaction_status": "completed" if is_conversion else None,
        "location_name": rng.choice(("Ho Chi Minh City", "Hanoi", "Da Nang")),
    }
    return {
        "schema_version": 1,
        "ingestion_version": "demo-1.0",
        "event_id": event_id,
        "data_source_id": str(uuid.uuid5(DEMO_NAMESPACE, f"sys_data_source:{_event_source_slug(raw_profile.get('source_system'))}")),
        "tenant_id": DEMO_TENANT_ID,
        "event_time": event_time.isoformat(),
        "received_at": (event_time + timedelta(seconds=rng.randint(1, 90))).isoformat(),
        "source_system": source_system,
        "domain": domain,
        "event_name": event_name,
        "event_category": event_category,
        "device_type": device_type,
        "event_dedup_key": f"demo:{event_id}",
        "identity": {
            "user_id": str(raw_profile["raw_profile_id"]),
            "session_id": session_id,
            "device_id": raw_profile.get("device_id"),
            "external_customer_id": raw_profile.get("external_customer_id"),
        },
        "master_profile_id": str(profile["master_profile_id"]),
        "raw_profile_id": str(raw_profile["raw_profile_id"]),
        "payload": payload,
    }


def _calculate_event_statistics(
    batches: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Calculate data-source metrics from the event envelopes queued for S3."""
    source_totals: dict[str, int] = defaultdict(int)
    source_dates: dict[str, set[str]] = defaultdict(set)
    source_profiles: dict[str, set[str]] = defaultdict(set)

    for (source_id, event_hour), envelopes in batches.items():
        source_totals[source_id] += len(envelopes)
        source_dates[source_id].add(event_hour[:10])
        source_profiles[source_id].update(
            str(envelope["master_profile_id"])
            for envelope in envelopes
            if envelope.get("master_profile_id")
        )

    statistics_by_source: dict[str, dict[str, Any]] = {}
    for source_slug in sorted(set(BEHAVIORAL_SOURCE_SLUGS.values())):
        source_id = str(uuid.uuid5(DEMO_NAMESPACE, f"sys_data_source:{source_slug}"))
        total_events = source_totals[source_id]
        statistics_by_source[source_id] = {
            "total_tracked_event": total_events,
            "avg_daily_event": round(
                total_events / len(source_dates[source_id]), 2
            )
            if source_dates[source_id]
            else 0,
            "avg_events_per_profile": round(
                total_events / len(source_profiles[source_id]), 2
            )
            if source_profiles[source_id]
            else 0,
        }
    return statistics_by_source


def _validate_event_batches(
    batches: dict[tuple[str, str], list[dict[str, Any]]],
    expected_event_count: int,
    now: datetime,
) -> None:
    """Validate S3 batch counts, partitions, and the enforced 30-day timeline."""
    actual_event_count = sum(len(envelopes) for envelopes in batches.values())
    if actual_event_count != expected_event_count:
        raise RuntimeError(
            f"Generated {actual_event_count} events but expected {expected_event_count}"
        )

    window_start = now - timedelta(days=BEHAVIORAL_EVENT_LOOKBACK_DAYS)
    for (source_id, event_hour), envelopes in batches.items():
        for envelope in envelopes:
            event_time = datetime.fromisoformat(envelope["event_time"])
            received_at = datetime.fromisoformat(envelope["received_at"])
            if not window_start <= event_time <= now:
                raise RuntimeError(
                    f"Event {envelope['event_id']} is outside the {BEHAVIORAL_EVENT_LOOKBACK_DAYS}-day window"
                )
            if event_time > received_at or received_at > now:
                raise RuntimeError(
                    f"Event {envelope['event_id']} has an invalid event/received timeline"
                )
            if _behavioral_event_hour(event_time) != event_hour:
                raise RuntimeError(
                    f"Event {envelope['event_id']} is in the wrong S3 timeline partition"
                )


def seed_behavioral_events(
    event_profiles: list,
    *,
    event_count: int = BEHAVIORAL_EVENT_COUNT,
    s3_client: Any | None = None,
) -> dict[str, dict[str, Any]]:
    if not event_profiles:
        raise RuntimeError("No raw/master profile links found for behavioral events")
    if event_count < 1:
        raise ValueError("event_count must be at least 1")
    profiles_by_master: dict[str, list[dict]] = defaultdict(list)
    for row in event_profiles:
        profiles_by_master[str(row["master_profile_id"])].append(row)
    masters = sorted(profiles_by_master)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=BEHAVIORAL_EVENT_LOOKBACK_DAYS)
    rng = stable_rng(f"behavioral-events:{DEMO_TENANT_ID}:{event_count}:{BEHAVIORAL_EVENT_LOOKBACK_DAYS}")
    latest_event_time = now - timedelta(seconds=90)
    span_seconds = max(1, int((latest_event_time - start).total_seconds()))
    batches: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event_index in range(event_count):
        master_id = masters[event_index % len(masters)]
        raw_profile = profiles_by_master[master_id][event_index % len(profiles_by_master[master_id])]
        event_time = start + timedelta(seconds=rng.randint(0, span_seconds))
        envelope = _build_behavioral_event(
            {"master_profile_id": master_id, "domain": raw_profile.get("domain")},
            raw_profile,
            event_index,
            event_time,
        )
        source_id = str(uuid.uuid5(DEMO_NAMESPACE, f"sys_data_source:{_event_source_slug(raw_profile.get('source_system'))}"))
        batches[(source_id, _behavioral_event_hour(event_time))].append(envelope)

    _validate_event_batches(batches, event_count, now)
    client = s3_client or _build_demo_s3_client()
    for source_slug in sorted(set(BEHAVIORAL_SOURCE_SLUGS.values())):
        source_id = str(uuid.uuid5(DEMO_NAMESPACE, f"sys_data_source:{source_slug}"))
        bucket = f"data-tracking-{source_id}"
        _ensure_demo_s3_bucket(client, bucket)
        _clear_demo_event_objects(client, bucket)

    for (source_id, event_hour), envelopes in sorted(batches.items()):
        bucket = f"data-tracking-{source_id}"
        object_key = _behavioral_object_key(source_id, event_hour)
        body = gzip.compress(
            ("\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in envelopes) + "\n").encode("utf-8"),
            mtime=0,
        )
        checksum = hashlib.sha256(body).hexdigest()
        event_times = sorted(item["event_time"] for item in envelopes)
        object_event_count = len(envelopes)
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=body,
            ContentType="application/x-ndjson",
            ContentEncoding="gzip",
            Metadata={
                "data-source-id": source_id,
                "event-count": str(object_event_count),
                "event-time-start": event_times[0],
                "event-time-end": event_times[-1],
                "sha256": checksum,
            },
        )
        object_id = uuid.uuid5(uuid.NAMESPACE_URL, f"s3://{bucket}/{object_key}")
        client.put_object(
            Bucket=bucket,
            Key=f"_processed/{object_id}.json",
            Body=json.dumps(
                {
                    "object_id": str(object_id),
                    "bucket": bucket,
                    "object_key": object_key,
                    "event_count": object_event_count,
                    "event_time_start": event_times[0],
                    "event_time_end": event_times[-1],
                    "content_sha256": checksum,
                    "status": "stored",
                },
                separators=(",", ":"),
            ).encode("utf-8"),
            ContentType="application/json",
        )
    statistics_by_source = _calculate_event_statistics(batches)
    if sum(item["total_tracked_event"] for item in statistics_by_source.values()) != event_count:
        raise RuntimeError("S3 event totals do not match generated event count")
    logger.info(
        "Seeded %d behavioral events across %d S3 object(s) within the last %d days.",
        event_count,
        len(batches),
        BEHAVIORAL_EVENT_LOOKBACK_DAYS,
    )
    return statistics_by_source


@dataclass(frozen=True)
class SeedResult:
    """Counts from the database fixture stages."""

    master_profiles: int
    detail_profiles: int
    personas_computed: int


class FullDemoSeeder:
    """Coordinate database fixtures and S3 events on one owned connection."""

    def __init__(self, connection: DatabaseConnection) -> None:
        self.connection = connection

    def seed_database(self, cursor) -> tuple[SeedResult, list]:
        """Seed fixtures in dependency order and return profiles for S3 events."""
        set_tenant_context(cursor, DEMO_TENANT_ID)
        master_profiles = fetch_master_profiles(cursor)
        if not master_profiles:
            raise RuntimeError(
                f"No resolved master profiles found for tenant_id={DEMO_TENANT_ID} -- "
                "run customer360-seeding/dev-backend-seeding/init_sample_data.py "
                "+ scripts/test_resolution_task.py first."
            )
        detail_profiles = master_profiles[:DETAIL_PROFILE_LIMIT]

        seed_relation_types(cursor)
        crm_ids = seed_crm_entities(cursor)
        seed_data_sources(cursor)
        seed_ai_agents(cursor)
        reset_tenant_scoped_demo_tables(cursor)
        event_profiles = fetch_event_profiles(cursor)
        experiment_variants = seed_campaign_experiments(cursor, crm_ids["campaign"])
        seed_campaign_performance_daily(cursor, crm_ids["campaign"], experiment_variants)
        seed_relations(cursor, detail_profiles)
        seed_customer_contacts(cursor, detail_profiles)
        seed_transactions(cursor, detail_profiles)
        seed_graph_edges(cursor, crm_ids, detail_profiles)
        enrich_master_profiles(cursor, master_profiles)
        master_profiles = fetch_master_profiles(cursor)
        archetypes = seed_persona_archetypes(cursor)
        personas_computed = seed_customer_personas(cursor, master_profiles, archetypes)
        seed_content_items(cursor)
        link_crm_contacts_to_master_profiles(cursor, crm_ids, master_profiles)
        return SeedResult(len(master_profiles), len(detail_profiles), personas_computed), event_profiles

    def run(self) -> SeedResult:
        """Commit completed fixtures or roll back on failure; always close."""
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                result, event_profiles = self.seed_database(cursor)
            statistics = seed_behavioral_events(event_profiles)
            with self.connection.cursor() as cursor:
                update_data_source_statistics(cursor, statistics)
            self.connection.commit()
            self.log_result(result)
            return result
        except Exception:
            self.connection.rollback()
            logger.exception("Failed to seed full demo data.")
            raise
        finally:
            self.connection.close()

    @staticmethod
    def log_result(result: SeedResult) -> None:
        """Report completed database stages after a successful commit."""
        logger.info(
            "Full demo seeded: profiles=%d, detail_profiles=%d, archetypes=%d, personas=%d; "
            "CRM, relations, supported-domain content, and S3 events completed.",
            result.master_profiles, result.detail_profiles, len(ICP_ARCHETYPES), result.personas_computed,
        )


def main() -> None:
    """Run the existing full-demo CLI without changing its launch commands."""
    if len(sys.argv) > 1 and sys.argv[1] == "--new-data":
        raise SystemExit(
            "--new-data moved to customer360-seeding/seed_api_data.py; "
            "use ./dev-c360.sh seed-new-data"
        )
    connection = psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT,
    )
    FullDemoSeeder(connection).run()


if __name__ == "__main__":
    main()
