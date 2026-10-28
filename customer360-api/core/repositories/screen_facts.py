"""What is on the admin screen, for the LEO Assistant.

Each admin route pattern maps to a loader that returns the allowlisted facts for the object the
user is looking at. Level 2.0 registered the profile loader; 2.1 adds the segment detail page and
2.2 the campaign detail/editor pages. Later work packages add the dashboards and the persona page.

Loaders repeat the tenant check themselves (row-level security is the second line of defence), and
they never invent fields: a field is sent only when the loader names it in code. Staff-written free
text goes through :func:`staff_text` (masked, whitespace-collapsed, capped) before it can reach the
model.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.repositories.assistant_repository import AssistantRepository, show_value
from core.repositories.campaign_draft_repository import (
    CampaignDraftNotFoundError,
    CampaignDraftRepository,
)
from core.repositories.campaign_experiment_repository import CampaignExperimentRepository
from core.repositories.events_s3_repository import EventQueryRepository
from core.repositories.reporting_repository import ReportingRepository
from core.utils.text_safety import collapse_whitespace, mask_text
from leo_customer360_dao.config import settings
from leo_customer360_dao.models.agent_workflow import CdpAgentWorkflow
from leo_customer360_dao.models.content import CdpContentItem
from leo_customer360_dao.models.crm import (
    CampaignContentItem,
    CampaignDispatchLog,
    CampaignExperiment,
    CampaignExperimentVariant,
    VwCampaignPerformanceMetrics,
)
from leo_customer360_dao.models.identity import CdpPersonaArchetype, CdpProfileAttribute
from leo_customer360_dao.models.segmentation import CdpSegment

STAFF_TEXT_MAX_CHARS = 200
PROFILE_PAGE = "/profiles/:id"
SEGMENT_PAGE = "/segments/:id"
CAMPAIGN_PAGE = "/campaigns/:id"
CAMPAIGN_EDIT_PAGE = "/campaigns/:id/edit"
ANALYTICS_PAGE = "/analytics"
OVERVIEW_PAGE = "/overview"
PERSONA_PAGE = "/personas/:archetypeId/matched-profiles"

# Segment rules: at most this many leaf rules and this many characters in the summary.
SEGMENT_RULES_MAX = 20
SEGMENT_RULES_MAX_CHARS = 1500

# Campaign detail: keep the facts block short when a campaign has many experiments, variants,
# content types or dispatch statuses.
CAMPAIGN_EXPERIMENTS_MAX = 5
CAMPAIGN_VARIANTS_MAX = 6
CAMPAIGN_COUNT_LINES_MAX = 10

# Dashboards: the page's own default period, the accepted range, and how many day lines to show.
ANALYTICS_DEFAULT_DAYS = 30
OVERVIEW_DEFAULT_DAYS = 90
PERIOD_MIN_DAYS = 1
PERIOD_MAX_DAYS = 400
DAILY_TOTALS_MAX = 14
SOURCE_SYSTEM_TOP = 10

# QueryBuilder operator codes -> words a reader understands.
_OPERATOR_WORDS = {
    "equal": "is",
    "not_equal": "is not",
    "less": "is less than",
    "less_or_equal": "is at most",
    "greater": "is greater than",
    "greater_or_equal": "is at least",
    "between": "is between",
    "not_between": "is not between",
    "in": "is one of",
    "not_in": "is not one of",
    "is_null": "is empty",
    "is_not_null": "is not empty",
    "contains": "contains",
    "begins_with": "starts with",
    "ends_with": "ends with",
    "is_empty": "is empty",
    "is_not_empty": "is not empty",
}

# Domain attributes are referenced as ``dp.domain_attributes->>'key'`` (see
# SegmentRepository.get_segmentable_attributes); the catalog key is the quoted part.
_DOMAIN_FIELD = re.compile(r"domain_attributes->>'([^']+)'")


@dataclass
class ScreenFacts:
    """Facts about the object on screen.

    ``title`` heads the facts block for the model, ``label`` is what the panel shows after
    "Based on:", ``lines`` are plain "Label: value" lines and ``fields`` are the field names used
    (for the audit row, never the values).
    """

    title: str
    label: str
    lines: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)


# A loader reads one object in the caller's tenant, or returns None when it is not there.
Loader = Callable[[Session, uuid.UUID, Optional[uuid.UUID], Optional[int]], Optional[ScreenFacts]]


def field_names(lines: list[str]) -> list[str]:
    """The field names used, taken from ``Label (code): value`` lines (never the values)."""
    names = [line.split(" (")[0].split(":")[0] for line in lines]
    return [name for name in names if name]


def fact(label: str, value) -> Optional[str]:
    """A plain ``Label: value`` line, or None when there is nothing to say."""
    text = show_value(value)
    return f"{label}: {text}" if text is not None else None


def staff_text(label: str, value) -> Optional[str]:
    """``Label (written by staff): "value"``, or None when the value is blank.

    The value is masked (emails, phones), whitespace-collapsed and cut to
    ``STAFF_TEXT_MAX_CHARS``. The "written by staff" marker tells the model the quoted text is
    data, not an instruction.
    """
    if value is None:
        return None
    text = collapse_whitespace(str(value))
    if not text:
        return None
    text = mask_text(text, STAFF_TEXT_MAX_CHARS)
    if not text:
        return None
    return f'{label} (written by staff): "{text}"'


def _append(lines: list[str], *candidates: Optional[str]) -> None:
    """Append the non-empty lines, in order."""
    lines.extend(line for line in candidates if line)


# --------------------------------------------------------------------------- profile


def load_profile_facts(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: Optional[uuid.UUID],
    period_days: Optional[int],
) -> Optional[ScreenFacts]:
    """Today's profile facts, wrapped in the screen-facts shape.

    Returns None when the profile is not in the caller's tenant, so the API can answer 404 before
    anything reaches the docs service.
    """
    if entity_id is None:
        return None
    lines = AssistantRepository(db).profile_facts(tenant_id, entity_id)
    if lines is None:
        return None
    return ScreenFacts(
        title="Customer profile on screen",
        label="this customer's profile",
        lines=lines,
        fields=field_names(lines),
    )


# --------------------------------------------------------------------------- segment


def _rule_field_keys(json_rules) -> set[str]:
    """Every leaf ``field`` in the rule tree, plus the catalog key of a domain attribute."""
    keys: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, dict):
            return
        children = node.get("rules")
        if children is not None:
            for child in children:
                walk(child)
            return
        field = str(node.get("field") or "").strip()
        if not field:
            return
        keys.add(field)
        match = _DOMAIN_FIELD.search(field)
        if match:
            keys.add(match.group(1))

    walk(json_rules)
    return keys


def _segment_rule_catalog(db: Session, json_rules) -> dict[str, tuple[str, bool]]:
    """Attribute code/column -> (display name, is_pii) for the rules' fields.

    A field missing from the catalog is simply absent, which the summary treats as PII (value
    hidden), the same fail-closed rule the profile loader uses.
    """
    keys = _rule_field_keys(json_rules)
    if not keys:
        return {}
    rows = db.execute(
        select(
            CdpProfileAttribute.attribute_internal_code,
            CdpProfileAttribute.master_profile_column,
            CdpProfileAttribute.name,
            CdpProfileAttribute.is_pii,
        ).where(
            or_(
                CdpProfileAttribute.attribute_internal_code.in_(keys),
                CdpProfileAttribute.master_profile_column.in_(keys),
            )
        )
    ).all()
    catalog: dict[str, tuple[str, bool]] = {}
    for code, column, name, is_pii in rows:
        entry = (name or code, bool(is_pii))
        catalog[code] = entry
        if column:
            catalog[column] = entry
    return catalog


def _summarize_rule_node(node, catalog: dict[str, tuple[str, bool]], count: list[int]) -> Optional[str]:
    """One rule or group as words; None when it is malformed or past the rule cap."""
    if not isinstance(node, dict):
        return None
    children = node.get("rules")
    if children is not None:
        condition = str(node.get("condition") or "AND").upper()
        parts = [part for part in (_summarize_rule_node(child, catalog, count) for child in children) if part]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        joiner = " AND " if condition == "AND" else " OR "
        return "(" + joiner.join(parts) + ")"
    if count[0] >= SEGMENT_RULES_MAX:
        return None
    count[0] += 1
    field = str(node.get("field") or "").strip()
    if not field:
        return None
    entry = catalog.get(field)
    name = (entry[0] if entry and entry[0] else field)
    operator = _OPERATOR_WORDS.get(str(node.get("operator") or "").lower(), str(node.get("operator") or "").replace("_", " "))
    value = node.get("value")
    if value is None or value == "" or value == []:
        return f"{name} {operator}".strip()
    # A non-PII attribute can still hold a pasted email or phone number as a rule value, so the shown
    # value is masked like every other staff-written text.
    shown = "(value hidden)" if entry is None or entry[1] else mask_text(str(show_value(value)), STAFF_TEXT_MAX_CHARS)
    return f"{name} {operator} {shown}".strip()


def summarize_segment_rules(json_rules, catalog: dict[str, tuple[str, bool]]) -> Optional[str]:
    """A plain-language summary of a segment's rule tree, or None when there is nothing to say.

    AND/OR groups keep their parentheses; a value is shown only when the catalog has the attribute
    and marks it non-PII, otherwise it is ``(value hidden)``. Capped at ``SEGMENT_RULES_MAX`` rules
    and ``SEGMENT_RULES_MAX_CHARS`` characters.
    """
    if not isinstance(json_rules, dict) or not json_rules.get("rules"):
        return None
    text = _summarize_rule_node(json_rules, catalog, [0])
    if not text:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]  # the root group's own parentheses add nothing
    return text[:SEGMENT_RULES_MAX_CHARS]


def _processed_by_label(value) -> Optional[str]:
    """``processed_by`` as Human or AI."""
    if value is None:
        return None
    return "AI" if str(value).lower() in ("ai", "ai_agent") else "Human"


def _segment_status_label(value) -> Optional[str]:
    """``status_code`` as Active/Inactive (the segment list's own labels)."""
    if value is None:
        return None
    if value == 1:
        return "Active"
    if value == 0:
        return "Inactive"
    return str(value)


def _segment_workflow_steps(db: Session, tenant_id: uuid.UUID, segment_id: uuid.UUID) -> list[tuple]:
    """Ordered agent steps, or [] when the workflow table is missing (test DB) or unreadable.

    The table is optional: a missing one must not cost the user the rest of the segment facts.
    """
    try:
        # A savepoint: on Postgres a failed statement aborts the whole transaction, so without one a
        # missing table would also break every later query of this request (and the audit row).
        with db.begin_nested():
            rows = db.execute(
                select(
                    CdpAgentWorkflow.agent_code,
                    CdpAgentWorkflow.execution_order,
                    CdpAgentWorkflow.is_active,
                    CdpAgentWorkflow.schedule_definition,
                )
                .where(
                    CdpAgentWorkflow.tenant_id == tenant_id,
                    CdpAgentWorkflow.segment_id == segment_id,
                )
                .order_by(CdpAgentWorkflow.execution_order.asc())
            ).all()
    except SQLAlchemyError:
        return []
    return [(row[0], row[1], bool(row[2]), row[3]) for row in rows]


def build_segment_facts(segment, catalog: dict[str, tuple[str, bool]], workflow_steps: list[tuple]) -> list[str]:
    """The allowlisted segment lines. Never ``sql_rules``, ``final_generated_sql``, ``user_id`` or
    the matched-profiles list."""
    lines: list[str] = []
    _append(
        lines,
        staff_text("Name", segment.segment_name),
        staff_text("Description", segment.description),
        fact("Active", segment.is_active),
        fact("Processed by", _processed_by_label(segment.processed_by)),
        fact("Domain", segment.domain),
        fact("Audience size", segment.member_count),
        fact("Last computed", segment.last_computed_at),
        fact("Created", segment.created_at),
        fact("Updated", segment.updated_at),
        fact("Status", _segment_status_label(segment.status_code)),
    )
    rules = summarize_segment_rules(segment.json_rules, catalog)
    if rules:
        lines.append(f"Rules: {rules}")
    for agent_code, order, is_active, schedule in workflow_steps:
        parts = [f"step {order}", str(agent_code), "active" if is_active else "inactive"]
        if schedule:
            parts.append(f"schedule: {schedule}")
        lines.append("Agent workflow: " + ", ".join(parts))
    return lines


def load_segment_facts(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: Optional[uuid.UUID],
    period_days: Optional[int],
) -> Optional[ScreenFacts]:
    """Facts for one segment in the caller's tenant, or None when it is not there."""
    if entity_id is None:
        return None
    segment = db.get(CdpSegment, entity_id)
    if segment is None or segment.tenant_id != tenant_id:
        return None
    catalog = _segment_rule_catalog(db, segment.json_rules)
    workflow_steps = _segment_workflow_steps(db, tenant_id, segment.segment_id)
    lines = build_segment_facts(segment, catalog, workflow_steps)
    return ScreenFacts(
        title="Segment on screen",
        label="this segment's details",
        lines=lines,
        fields=field_names(lines),
    )


# --------------------------------------------------------------------------- campaign


def _campaign_segment(db: Session, tenant_id: uuid.UUID, segment_id) -> Optional[CdpSegment]:
    """The linked segment, or None when unset or outside the tenant."""
    if segment_id is None:
        return None
    segment = db.get(CdpSegment, segment_id)
    if segment is None or segment.tenant_id != tenant_id:
        return None
    return segment


def _campaign_metrics(db: Session, tenant_id: uuid.UUID, campaign_id: uuid.UUID):
    """The lifetime metrics row from the view, or None when the campaign has no performance."""
    return db.execute(
        select(VwCampaignPerformanceMetrics).where(
            VwCampaignPerformanceMetrics.tenant_id == tenant_id,
            VwCampaignPerformanceMetrics.campaign_id == campaign_id,
        )
    ).scalar_one_or_none()


def _campaign_experiments(db: Session, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> list[dict]:
    """Experiments with status/metrics and per-variant numbers, never names or keys."""
    experiments = db.execute(
        select(CampaignExperiment)
        .where(CampaignExperiment.tenant_id == tenant_id, CampaignExperiment.campaign_id == campaign_id)
        .order_by(CampaignExperiment.created_at.asc())
    ).scalars().all()
    repo = CampaignExperimentRepository(db)
    result: list[dict] = []
    for position, experiment in enumerate(experiments):
        if position >= CAMPAIGN_EXPERIMENTS_MAX:
            # Only the first few are described; the rest are counted ("+N more experiments"), so they
            # cost no variant or performance queries.
            result.append({"status": experiment.status, "primary_metric": experiment.primary_metric, "variants": []})
            continue
        variants = db.execute(
            select(CampaignExperimentVariant)
            .where(
                CampaignExperimentVariant.tenant_id == tenant_id,
                CampaignExperimentVariant.experiment_id == experiment.experiment_id,
            )
            .order_by(CampaignExperimentVariant.variant_key.asc())
        ).scalars().all()
        performance = {row["variant_id"]: row for row in repo.performance(tenant_id, experiment.experiment_id)}
        result.append(
            {
                "status": experiment.status,
                "primary_metric": experiment.primary_metric,
                "variants": [
                    {
                        "allocation": variant.allocation_percentage,
                        "is_control": bool(variant.is_control),
                        "conversions": (performance.get(variant.variant_id) or {}).get("conversions"),
                        "cvr": (performance.get(variant.variant_id) or {}).get("conversion_rate"),
                        "roas": (performance.get(variant.variant_id) or {}).get("roas"),
                    }
                    for variant in variants
                ],
            }
        )
    return result


def _campaign_content_counts(db: Session, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> dict[str, int]:
    """Content plan counts by item type (never titles or URLs)."""
    rows = db.execute(
        select(CdpContentItem.item_type, func.count())
        .join(CampaignContentItem, CampaignContentItem.content_item_id == CdpContentItem.content_item_id)
        .where(
            CampaignContentItem.tenant_id == tenant_id,
            CampaignContentItem.campaign_id == campaign_id,
        )
        .group_by(CdpContentItem.item_type)
    ).all()
    return {str(item_type): int(count) for item_type, count in rows}


def _campaign_dispatch_counts(db: Session, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> dict[str, int]:
    """Dispatch counts by status (never recipients)."""
    rows = db.execute(
        select(CampaignDispatchLog.status, func.count())
        .where(
            CampaignDispatchLog.tenant_id == tenant_id,
            CampaignDispatchLog.campaign_id == campaign_id,
        )
        .group_by(CampaignDispatchLog.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def build_campaign_facts(
    campaign,
    segment,
    metrics,
    experiments: list[dict],
    content_counts: dict[str, int],
    dispatch_counts: dict[str, int],
) -> list[str]:
    """The allowlisted campaign lines. Never keywords, strategy summary, ``ai_plan``, content
    titles/URLs, history reasons, staff ids or recipients."""
    lines: list[str] = []
    _append(
        lines,
        staff_text("Name", campaign.name),
        staff_text("Description", campaign.description),
        fact("Status", campaign.status),
        fact("Approval status", campaign.approval_status),
        fact("Channel", campaign.channel),
        fact("Platform", campaign.platform),
        fact("Objective", campaign.objective),
        fact("Language", campaign.lang),
        fact("Currency", campaign.currency),
        fact("Start date", campaign.start_date),
        fact("End date", campaign.end_date),
        fact("Budget", campaign.budget_amount),
        fact("Approved at", campaign.approved_at),
    )
    if campaign.segment_id is None:
        lines.append("Linked segment: none")
    elif segment is None:
        lines.append("Linked segment: unavailable")
    else:
        _append(
            lines,
            staff_text("Linked segment", segment.segment_name),
            fact("Linked segment size", segment.member_count),
            fact("Linked segment active", segment.is_active),
        )
    if metrics is None:
        lines.append("Lifetime metrics: none recorded")
    else:
        _append(
            lines,
            fact("Lifetime spend", metrics.total_spend),
            fact("Lifetime impressions", metrics.total_impressions),
            fact("Lifetime clicks", metrics.total_clicks),
            fact("Lifetime conversions", metrics.total_conversions),
            fact("Lifetime revenue", metrics.total_revenue),
            fact("CTR", metrics.ctr_percentage),
            fact("CVR", metrics.cvr_percentage),
            fact("CPA", metrics.cpa),
            fact("ROAS", metrics.roas),
        )
    if experiments:
        lines.append(f"Experiments: {len(experiments)}")
        for index, experiment in enumerate(experiments[:CAMPAIGN_EXPERIMENTS_MAX], start=1):
            variants = experiment.get("variants") or []
            _append(
                lines,
                fact(f"Experiment {index} status", experiment.get("status")),
                fact(f"Experiment {index} primary metric", experiment.get("primary_metric")),
                fact(f"Experiment {index} variants", len(variants)),
            )
            allocations = [variant.get("allocation") for variant in variants if variant.get("allocation") is not None]
            if allocations:
                lines.append(f"Experiment {index} allocation: " + " / ".join(show_value(value) for value in allocations))
            if any(variant.get("is_control") for variant in variants):
                lines.append(f"Experiment {index} control: yes")
            for variant_index, variant in enumerate(variants[:CAMPAIGN_VARIANTS_MAX], start=1):
                _append(
                    lines,
                    fact(f"Experiment {index} variant {variant_index} conversions", variant.get("conversions")),
                    fact(f"Experiment {index} variant {variant_index} CVR", variant.get("cvr")),
                    fact(f"Experiment {index} variant {variant_index} ROAS", variant.get("roas")),
                )
            if len(variants) > CAMPAIGN_VARIANTS_MAX:
                lines.append(f"Experiment {index} (+{len(variants) - CAMPAIGN_VARIANTS_MAX} more variants)")
        if len(experiments) > CAMPAIGN_EXPERIMENTS_MAX:
            lines.append(f"(+{len(experiments) - CAMPAIGN_EXPERIMENTS_MAX} more experiments)")
    if content_counts:
        lines.append(f"Content items: {sum(content_counts.values())}")
        content_items = sorted(content_counts.items())
        for item_type, count in content_items[:CAMPAIGN_COUNT_LINES_MAX]:
            lines.append(f"Content items ({item_type}): {count}")
        if len(content_items) > CAMPAIGN_COUNT_LINES_MAX:
            lines.append(f"(+{len(content_items) - CAMPAIGN_COUNT_LINES_MAX} more content types)")
    if dispatch_counts:
        lines.append(f"Dispatches: {sum(dispatch_counts.values())}")
        dispatch_items = sorted(dispatch_counts.items())
        for status, count in dispatch_items[:CAMPAIGN_COUNT_LINES_MAX]:
            lines.append(f"Dispatches ({status}): {count}")
        if len(dispatch_items) > CAMPAIGN_COUNT_LINES_MAX:
            lines.append(f"(+{len(dispatch_items) - CAMPAIGN_COUNT_LINES_MAX} more dispatch statuses)")
    return lines


def load_campaign_facts(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: Optional[uuid.UUID],
    period_days: Optional[int],
) -> Optional[ScreenFacts]:
    """Facts for one campaign in the caller's tenant, or None when it is not there.

    Reuses ``CampaignDraftRepository.get_campaign`` (tenant-checked) so the campaign detail and
    editor pages share the same not-found behaviour as the rest of the campaign API.
    """
    if entity_id is None:
        return None
    try:
        campaign = CampaignDraftRepository(db).get_campaign(tenant_id, entity_id)
    except CampaignDraftNotFoundError:
        return None
    segment = _campaign_segment(db, tenant_id, campaign.segment_id)
    metrics = _campaign_metrics(db, tenant_id, campaign.campaign_id)
    experiments = _campaign_experiments(db, tenant_id, campaign.campaign_id)
    content_counts = _campaign_content_counts(db, tenant_id, campaign.campaign_id)
    dispatch_counts = _campaign_dispatch_counts(db, tenant_id, campaign.campaign_id)
    lines = build_campaign_facts(campaign, segment, metrics, experiments, content_counts, dispatch_counts)
    return ScreenFacts(
        title="Campaign on screen",
        label="this campaign's details",
        lines=lines,
        fields=field_names(lines),
    )


# --------------------------------------------------------------------------- dashboards


def _clamp_period(period_days: Optional[int], default: int) -> int:
    """The dashboard period in days: the page default when unset, else clamped to 1-400."""
    if period_days is None:
        return default
    return max(PERIOD_MIN_DAYS, min(PERIOD_MAX_DAYS, int(period_days)))


def _daily_total_lines(daily_totals: list[dict]) -> list[str]:
    """Peak/active/average summary plus the most recent ``DAILY_TOTALS_MAX`` day lines.

    A long period must not turn the facts block into a day-by-day dump; the summary carries the
    shape and the day lines are capped with a "(+N more days)" marker. The repository returns the
    series oldest-first, so the tail is the latest days (what the user is looking at).
    """
    totals = [(row.get("day"), int(row.get("total") or 0)) for row in daily_totals]
    if not totals:
        return ["Total events: 0"]
    total_events = sum(total for _, total in totals)
    active_days = sum(1 for _, total in totals if total > 0)
    peak_day, peak_total = max(totals, key=lambda item: item[1])
    lines = [
        f"Total events: {total_events}",
        f"Active days: {active_days}",
        f"Peak day: {show_value(peak_day) or 'unknown'} ({peak_total} events)",
        f"Average per day: {round(total_events / len(totals), 2)}",
    ]
    for day, total in totals[-DAILY_TOTALS_MAX:]:
        lines.append(f"Daily {show_value(day) or 'unknown'}: {total}")
    if len(totals) > DAILY_TOTALS_MAX:
        lines.append(f"(+{len(totals) - DAILY_TOTALS_MAX} more days)")
    return lines


def _analytics_event_lines(db: Session, tenant_id: uuid.UUID, period: int) -> list[str]:
    """Event totals from the event lake, or an "unavailable" line when it cannot be read.

    The event store is a stand-in (S3/MinIO) and may be down; that must not cost the user the
    profile numbers or the rest of the answer.
    """
    try:
        repo = EventQueryRepository(settings)
        with db.begin_nested():  # a failed read must not abort the rest of the request (see above)
            daily_totals = repo.query_daily_totals(db, tenant_id, days=period)
            device_totals = repo.query_device_type_totals(db, tenant_id, days=period)
    except Exception:  # noqa: BLE001 - the event lake is optional; never raise from a loader
        return ["Event data: unavailable"]
    lines = _daily_total_lines(daily_totals)
    for row in device_totals:
        device = row.get("device_type") or "unknown"
        lines.append(f"Events by device ({device}): {int(row.get('total') or 0)}")
    return lines


def _analytics_profile_lines(db: Session, tenant_id: uuid.UUID, period: int) -> list[str]:
    """Master-profile totals and the top source-system x domain rows, or an "unavailable" line."""
    try:
        repo = ReportingRepository(db)
        with db.begin_nested():
            total_master = repo.count_master_profiles(tenant_id, days=period)
            by_source = repo.raw_profiles_by_source_system(tenant_id, days=period)
    except Exception:  # noqa: BLE001 - a failed aggregate must not raise from a loader
        return ["Profile data: unavailable"]
    lines = [f"Total master profiles: {int(total_master or 0)}"]
    rows = sorted(by_source, key=lambda row: int(row.get("count") or 0), reverse=True)[:SOURCE_SYSTEM_TOP]
    for row in rows:
        source = row.get("source_system") or "unknown"
        domain = row.get("domain") or "unknown"
        lines.append(f"Profiles ({source} / {domain}): {int(row.get('count') or 0)}")
    return lines


def load_analytics_facts(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: Optional[uuid.UUID],
    period_days: Optional[int],
) -> Optional[ScreenFacts]:
    """Tenant-wide aggregates for the Analytics dashboard's selected period.

    The repositories are called directly with the session tenant; no client ``tenant_id`` and no
    ``master_profile_id`` ever reaches them.
    """
    period = _clamp_period(period_days, ANALYTICS_DEFAULT_DAYS)
    lines = _analytics_event_lines(db, tenant_id, period)
    lines.extend(_analytics_profile_lines(db, tenant_id, period))
    lines.append("Conversions is not available yet")
    return ScreenFacts(
        title=f"Analytics for the last {period} days",
        label="this dashboard's numbers",
        lines=lines,
        fields=field_names(lines),
    )


def _overview_summary_lines(summary) -> list[str]:
    """The Overview dashboard's profile counts, breakdowns and top source systems."""
    lines = [
        f"Raw profiles: {int(summary.total_raw_profiles or 0)}",
        f"Master profiles: {int(summary.total_master_profiles or 0)}",
        f"Duplicate master profiles: {int(summary.duplicate_master_profile_count or 0)}",
        f"Processed raw profiles: {int(summary.processed_raw_profiles or 0)}",
        f"In-progress raw profiles: {int(summary.in_progress_raw_profiles or 0)}",
        f"Pending raw profiles: {int(summary.pending_raw_profiles or 0)}",
    ]
    for row in summary.raw_profiles_by_status or []:
        lines.append(f"Raw profiles by status ({row.label}): {int(row.count or 0)}")
    for row in summary.raw_profiles_by_domain or []:
        lines.append(f"Raw profiles by domain ({row.domain}): {int(row.count or 0)}")
    for row in summary.master_profiles_by_domain or []:
        lines.append(f"Master profiles by domain ({row.domain}): {int(row.count or 0)}")
    sources = sorted(
        summary.raw_profiles_by_source_system or [],
        key=lambda row: int(row.count or 0),
        reverse=True,
    )[:SOURCE_SYSTEM_TOP]
    for row in sources:
        lines.append(f"Raw profiles ({row.source_system} / {row.domain}): {int(row.count or 0)}")
    return lines


def _coverage_lines(coverage) -> list[str]:
    """Identity-graph channel coverage as percentages of the master-profile total.

    The DAO annotates ``IdentityGraphCoverage`` but returns a plain dict at runtime (FastAPI turns it
    into the schema on the coverage endpoint), so both shapes are read here.
    """

    def value(name: str):
        return coverage.get(name) if isinstance(coverage, dict) else getattr(coverage, name, None)

    total = int(value("total_master_profiles") or 0)
    channels = [
        ("Email", value("with_email")),
        ("Phone", value("with_phone_number")),
        ("Device ID", value("with_device_id")),
        ("Advertising ID", value("with_advertising_id")),
        ("Cookie ID", value("with_cookie_id")),
        ("External ID", value("with_external_id")),
        ("National ID", value("with_national_id")),
    ]
    lines = []
    for label, count in channels:
        count = int(count or 0)
        percent = round((count / total) * 100, 1) if total else 0.0
        lines.append(f"Identity coverage ({label}): {percent}%")
    return lines


def load_overview_facts(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: Optional[uuid.UUID],
    period_days: Optional[int],
) -> Optional[ScreenFacts]:
    """Tenant-wide aggregates for the Overview dashboard's selected period.

    The repositories are called directly with the session tenant; no client ``tenant_id`` and no
    ``master_profile_id`` ever reaches them.
    """
    period = _clamp_period(period_days, OVERVIEW_DEFAULT_DAYS)
    repo = ReportingRepository(db)
    try:
        with db.begin_nested():  # each aggregate in its own savepoint: one failing must not break the other
            lines = _overview_summary_lines(repo.get_cir_summary(tenant_id, days=period))
    except Exception:  # noqa: BLE001 - a failed aggregate must not raise from a loader
        lines = ["Profile data: unavailable"]
    try:
        with db.begin_nested():
            lines.extend(_coverage_lines(repo.get_identity_graph_coverage(tenant_id, days=period)))
    except Exception:  # noqa: BLE001 - a failed aggregate must not raise from a loader
        lines.append("Identity coverage: unavailable")
    return ScreenFacts(
        title=f"Overview for the last {period} days",
        label="this dashboard's numbers",
        lines=lines,
        fields=field_names(lines),
    )


# --------------------------------------------------------------------------- persona


def build_persona_facts(archetype) -> list[str]:
    """The allowlisted persona-archetype lines.

    Never the matched-profiles list and never ``persona_embedding``.
    """
    lines: list[str] = []
    _append(
        lines,
        staff_text("Name", archetype.persona_name),
        staff_text("Summary", archetype.persona_summary),
        fact("Domain", archetype.domain),
        fact("Code", archetype.persona_code),
        fact("Category", archetype.persona_category),
        fact("Active", archetype.is_active),
        fact("Matched profiles", archetype.matched_profile_count),
        fact("Centroid behavior score", archetype.centroid_behavior_score),
        fact("Centroid engagement score", archetype.centroid_engagement_score),
        fact("Centroid financial score", archetype.centroid_financial_score),
        fact("Centroid loyalty score", archetype.centroid_loyalty_score),
        fact("Centroid relationship score", archetype.centroid_relationship_score),
        fact("Centroid risk score", archetype.centroid_risk_score),
        fact("LLM provider", archetype.llm_provider),
        fact("LLM model", archetype.llm_model),
        fact("Created", archetype.created_at),
        fact("Updated", archetype.updated_at),
    )
    return lines


def load_persona_facts(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: Optional[uuid.UUID],
    period_days: Optional[int],
) -> Optional[ScreenFacts]:
    """Facts for one persona archetype in the caller's tenant, or None when it is not there.

    ``PersonaRepository.get_archetype`` is RLS-only, so the tenant check is repeated here.
    """
    if entity_id is None:
        return None
    archetype = db.get(CdpPersonaArchetype, entity_id)
    if archetype is None or archetype.tenant_id != tenant_id:
        return None
    lines = build_persona_facts(archetype)
    return ScreenFacts(
        title="Persona on screen",
        label="this persona's details",
        lines=lines,
        fields=field_names(lines),
    )


# page pattern -> loader. One function and one entry per page added in later work packages.
SCREEN_FACTS: dict[str, Loader] = {
    PROFILE_PAGE: load_profile_facts,
    SEGMENT_PAGE: load_segment_facts,
    CAMPAIGN_PAGE: load_campaign_facts,
    CAMPAIGN_EDIT_PAGE: load_campaign_facts,
    ANALYTICS_PAGE: load_analytics_facts,
    OVERVIEW_PAGE: load_overview_facts,
    PERSONA_PAGE: load_persona_facts,
}
