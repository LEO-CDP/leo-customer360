"""Ad personalisation admin endpoints.

Lets marketers and tenant administrators configure the ad personalisation
engine for their tenant. Contract:
specs/003-ad-personalization-engine/contracts/admin-plane.openapi.yaml.

* The tenant always comes from the caller's token (``require_tenant``); a tenant
  in the request body is ignored. The platform tenant is mapped to the ad
  tenant through ``leo_ads.tenant.customer360_tenant_id``; an unmapped tenant
  gets 404.
* Reads are open to any authenticated user of the tenant. Writes need a tenant
  admin (``require_tenant_admin``). No new role types are introduced.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from core.ads_database import get_ads_db
from core.auth import require_tenant, require_tenant_admin
from core.repositories.ad_personalization_repository import (
    AdPersonalizationRepository,
    AdPersonalizationValidationError,
    AdTenantNotFoundError,
)
from core.utils import ad_hot_state, ad_visitor_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ad-personalization", tags=["Ad Personalisation"])


class Setting(BaseModel):
    """One scope's personalisation settings."""

    scope_type: Literal["tenant", "site", "placement"]
    scope_ref: str = Field(description="Empty for tenant; site key for site; placement key for placement.")
    enabled: bool
    holdout_pct: float = Field(ge=0, le=100)
    consent_mode: Optional[Literal["opt_in", "opt_out"]] = Field(
        default=None, description="Only set on the tenant scope."
    )
    holdout_salt: str
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class SettingsResponse(BaseModel):
    settings: list[Setting]


class SettingUpdate(BaseModel):
    """Body of ``PUT /settings``. Only the fields provided change.

    Any ``tenant`` sent alongside is ignored: the tenant comes from the token.
    """

    model_config = ConfigDict(extra="ignore")

    scope_type: Literal["tenant", "site", "placement"]
    scope_ref: str = Field(default="", max_length=200)
    enabled: Optional[bool] = None
    holdout_pct: Optional[float] = Field(default=None, ge=0, le=100)
    consent_mode: Optional[Literal["opt_in", "opt_out"]] = None
    new_measurement_period: bool = False


def _actor(request: Request) -> str:
    """Who is making the change, for the audit column."""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        for claim in ("email", "preferred_username", "sub"):
            if user.get(claim):
                return str(user[claim])
    user_id = getattr(request.state, "user_id", None)
    return str(user_id) if user_id else "unknown"


def _resolve_ad_tenant(request: Request, repository: AdPersonalizationRepository) -> int:
    """The caller's ad tenant id, from the token only (404 when unmapped)."""
    platform_tenant = require_tenant(request)
    try:
        return repository.resolve_ad_tenant_id(platform_tenant)
    except AdTenantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/settings", response_model=SettingsResponse)
def list_settings(request: Request, db: Session = Depends(get_ads_db)) -> SettingsResponse:
    """List the tenant's personalisation settings (any authenticated user)."""
    repository = AdPersonalizationRepository(db)
    ad_tenant_id = _resolve_ad_tenant(request, repository)
    return SettingsResponse(settings=repository.list_settings(ad_tenant_id))


@router.put("/settings", response_model=Setting)
def update_setting(
    body: SettingUpdate, request: Request, db: Session = Depends(get_ads_db)
) -> Setting:
    """Create or update one scope's settings (tenant admin).

    A tenant-scope row must exist before a site or placement row; consent mode
    and a new measurement period can only be set on the tenant scope. A new
    measurement period replaces the hold-out salt, which reshuffles who is held
    out, so use it sparingly.
    """
    require_tenant_admin(request, "ad personalisation settings")
    repository = AdPersonalizationRepository(db)
    ad_tenant_id = _resolve_ad_tenant(request, repository)
    try:
        row = repository.upsert_setting(
            ad_tenant_id,
            scope_type=body.scope_type,
            scope_ref=body.scope_ref,
            enabled=body.enabled,
            holdout_pct=body.holdout_pct,
            consent_mode=body.consent_mode,
            new_measurement_period=body.new_measurement_period,
            updated_by=_actor(request),
        )
    except AdPersonalizationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Setting(**row)


GroupCode = Literal[
    "personalised",
    "holdout",
    "default_no_history",
    "default_no_consent",
    "default_disabled",
    "default_fallback",
]


class Signal(BaseModel):
    name: Literal[
        "objective_rate_lift",
        "visitor_affinity",
        "cohort_pattern",
        "exploration",
        "base_weight",
        "page_context",
    ]
    contribution: Optional[float] = Field(
        default=None, description="Multiplicative factor this signal applied to the score."
    )
    note: str = ""


class ChosenAd(BaseModel):
    rank: int = Field(ge=1)
    ad_key: str
    objective: Optional[str] = None
    score: Optional[float] = None
    signals: list[Signal] = []


class Decision(BaseModel):
    request_id: uuid.UUID
    decided_at: datetime
    placement_key: Optional[str] = None
    group: GroupCode
    visitor_fingerprint: Optional[str] = Field(
        default=None, description="Truncated fingerprint for correlation only."
    )
    candidate_count: int = 0
    chosen: list[ChosenAd]


class DecisionSummary(BaseModel):
    request_id: uuid.UUID
    decided_at: datetime
    group: str
    rank: Optional[int] = Field(default=None, description="Rank of the requested ad in that decision.")


class DecisionList(BaseModel):
    decisions: list[DecisionSummary]


class GroupTotals(BaseModel):
    group: Literal["personalised", "holdout", "default"]
    impressions: int = Field(ge=0)
    views: int = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: int = Field(ge=0)


class ObjectiveComparison(BaseModel):
    objective: str
    metric: Literal["view_rate", "click_through_rate", "conversion_rate"]
    personalised_rate: Optional[float] = None
    holdout_rate: Optional[float] = None
    lift: Optional[float] = Field(default=None, description="(personalised - holdout) / holdout.")
    insufficient_data: bool


class ComparisonReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    placement_key: Optional[str] = None
    groups: list[GroupTotals]
    by_objective: list[ObjectiveComparison]


def _as_utc(value: datetime) -> datetime:
    """Treat a date without a zone as UTC (the contract's timestamps are UTC)."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.get("/decisions/{request_id}", response_model=Decision)
def get_decision(
    request_id: uuid.UUID, request: Request, db: Session = Depends(get_ads_db)
) -> Decision:
    """Explain one serving decision of the caller's tenant (any authenticated user).

    404 when there is no such decision for this tenant (another tenant's request
    id is indistinguishable from an unknown one) or it is past the retention period.
    """
    repository = AdPersonalizationRepository(db)
    ad_tenant_id = _resolve_ad_tenant(request, repository)
    decision = repository.get_decision(ad_tenant_id, str(request_id))
    if decision is None:
        raise HTTPException(status_code=404, detail="No such decision")
    return Decision(**decision)


@router.get("/decisions", response_model=DecisionList)
def list_decisions(
    request: Request,
    ad_key: str = Query(..., min_length=1, max_length=200),
    date_from: Optional[datetime] = Query(default=None, alias="from"),
    date_to: Optional[datetime] = Query(default=None, alias="to"),
    group: Optional[GroupCode] = None,
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_ads_db),
) -> DecisionList:
    """Recent decisions that served an ad, newest first, to find a request id to inspect."""
    repository = AdPersonalizationRepository(db)
    ad_tenant_id = _resolve_ad_tenant(request, repository)
    if date_from and date_to and _as_utc(date_from) > _as_utc(date_to):
        raise HTTPException(status_code=422, detail="'from' must not be after 'to'")
    rows = repository.list_decisions(
        ad_tenant_id,
        ad_key,
        date_from=_as_utc(date_from) if date_from else None,
        date_to=_as_utc(date_to) if date_to else None,
        group=group,
        limit=limit,
    )
    return DecisionList(decisions=[DecisionSummary(**row) for row in rows])


@router.get("/reports/comparison", response_model=ComparisonReport, response_model_by_alias=True)
def comparison_report(
    request: Request,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    placement_key: Optional[str] = Query(default=None, max_length=200),
    db: Session = Depends(get_ads_db),
) -> ComparisonReport:
    """Personalised versus hold-out delivery, per group and per campaign objective.

    Each objective is judged on its own metric (view rate for awareness,
    click-through rate for traffic, conversion rate for conversions). A group
    too small to compare reports ``insufficient_data`` instead of a rate.
    """
    repository = AdPersonalizationRepository(db)
    ad_tenant_id = _resolve_ad_tenant(request, repository)
    start, end = _as_utc(date_from), _as_utc(date_to)
    if start >= end:
        raise HTTPException(status_code=422, detail="'from' must be before 'to'")
    report = repository.comparison_report(
        ad_tenant_id, start, end, placement_key=placement_key
    )
    return ComparisonReport(
        from_=report["from"],
        to=report["to"],
        placement_key=report["placement_key"],
        groups=report["groups"],
        by_objective=report["by_objective"],
    )


class ErasureRequestCreate(BaseModel):
    """Body of ``POST /erasure-requests``: exactly one of ``leovid`` or ``profile_ref``."""

    model_config = ConfigDict(extra="ignore")

    leovid: Optional[str] = Field(default=None, min_length=1, max_length=128)
    profile_ref: Optional[str] = Field(default=None, min_length=1, max_length=255)
    urgent: bool = False

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> "ErasureRequestCreate":
        if (self.leovid is None) == (self.profile_ref is None):
            raise ValueError("Provide exactly one of 'leovid' or 'profile_ref'")
        return self


class ErasureRequest(BaseModel):
    erasure_id: int
    status: Literal["pending", "redis_purged", "completed", "failed"]
    urgent: bool
    requested_at: datetime
    completed_at: Optional[datetime] = None
    deleted_counts: Optional[dict[str, int]] = None
    redis_purged: bool = Field(
        default=False,
        description="Whether the visitor's fast-access state has been purged (false means "
        "the purge could not be done now; the deletion job still removes the stored history).",
    )


def _erasure_response(row: dict, redis_purged: Optional[bool] = None) -> ErasureRequest:
    purged = row["status"] in ("redis_purged", "completed") if redis_purged is None else redis_purged
    return ErasureRequest(**row, redis_purged=purged)


def _purge_hot_state(ad_tenant_id: int, keys: list[bytes]) -> bool:
    """Purge each key's Redis state now. False if Redis is absent or fails (never raises)."""
    try:
        client = ad_hot_state.get_hot_state_client()
        if client is None:
            return False
        for key in keys:
            ad_hot_state.purge_visitor(client, ad_tenant_id, key)
        return True
    except Exception:  # noqa: BLE001 - the request is still recorded; the job removes stored data
        logger.warning("erasure: could not purge hot state; the request stays pending", exc_info=True)
        return False


@router.post("/erasure-requests", response_model=ErasureRequest, status_code=202)
def create_erasure_request(
    body: ErasureRequestCreate, request: Request, db: Session = Depends(get_ads_db)
) -> ErasureRequest:
    """Request erasure of a visitor's ad-behaviour history (tenant admin).

    Records an audit row, purges the visitor's Redis state at once and queues the
    deletion of the stored history for the erasure job. A master profile reference
    covers every linked visitor key: one request is recorded per key and the first
    is returned. If Redis cannot be reached the request is still recorded and stays
    ``pending`` (``redis_purged`` false); nothing is lost, the job deletes the stored data.
    """
    require_tenant_admin(request, "ad personalisation erasure")
    repository = AdPersonalizationRepository(db)
    ad_tenant_id = _resolve_ad_tenant(request, repository)

    if body.leovid is not None:
        try:
            keys = [ad_visitor_key.visitor_key(ad_tenant_id, body.leovid)]
        except ad_visitor_key.VisitorKeyUnavailable as exc:
            raise HTTPException(status_code=503, detail="Visitor keys are not available") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        keys = repository.profile_keys(ad_tenant_id, body.profile_ref)
        if not keys:
            raise HTTPException(status_code=422, detail="Unknown profile reference")

    actor = _actor(request)
    rows = [
        repository.create_erasure_request(ad_tenant_id, key, urgent=body.urgent, requested_by=actor)
        for key in keys
    ]
    purged = _purge_hot_state(ad_tenant_id, keys)
    if purged:
        rows = [repository.mark_redis_purged(ad_tenant_id, r["erasure_id"]) or r for r in rows]
    logger.info(
        "erasure: tenant=%s keys=%d urgent=%s redis_purged=%s",
        ad_tenant_id, len(keys), body.urgent, purged,
    )
    return _erasure_response(rows[0], purged)


@router.get("/erasure-requests/{erasure_id}", response_model=ErasureRequest)
def get_erasure_request(
    erasure_id: int, request: Request, db: Session = Depends(get_ads_db)
) -> ErasureRequest:
    """Status and evidence of an erasure request of the caller's tenant (any authenticated user)."""
    repository = AdPersonalizationRepository(db)
    ad_tenant_id = _resolve_ad_tenant(request, repository)
    row = repository.get_erasure_request(ad_tenant_id, erasure_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such erasure request")
    return _erasure_response(row)


all_ad_personalization_routers = [router]
