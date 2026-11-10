"""Tenant-admin endpoints for explicit promotion conversion."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.auth import require_tenant, require_tenant_admin
from core.database import get_db
from core.services.promotion_catalog import creative_options
from core.services.promotion_service import PromotionService

router = APIRouter(prefix="/promotions", tags=["Promotions"])


class CreativeConversion(BaseModel):
    """Promotion rendering attributes supplied by the caller."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ad_type: str = Field(min_length=1, max_length=80)
    format_code: str = Field(min_length=1, max_length=80)


class CreativeOption(BaseModel):
    """One user-selectable promotion option."""

    value: str
    label: str


class CreativeOptions(BaseModel):
    """Ad type and format options for promotion conversion."""

    ad_types: list[CreativeOption]
    format_codes: list[CreativeOption]


def _tenant(request: Request) -> uuid.UUID:
    require_tenant_admin(request, "promotion conversion")
    return uuid.UUID(require_tenant(request))


@router.get("/creative-options", response_model=CreativeOptions)
def list_creative_options(request: Request) -> dict[str, list[dict[str, str]]]:
    """List selectable creative options for the current tenant administrator."""
    _tenant(request)
    return creative_options()


@router.put("/campaigns/{campaign_id}")
def convert_campaign(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> dict:
    """Create or refresh the promotion linked to this tenant's campaign."""
    return PromotionService(db).campaign(_tenant(request), campaign_id)


@router.put("/content/{content_id}")
def convert_content(content_id: uuid.UUID, payload: CreativeConversion, request: Request, db: Session = Depends(get_db)) -> dict:
    """Create or refresh the creative linked to this tenant's content."""
    return PromotionService(db).content(_tenant(request), content_id, payload.ad_type, payload.format_code)


@router.put("/products/{product_id}")
def convert_product(product_id: Annotated[str, Field(min_length=1, max_length=255)], payload: CreativeConversion, request: Request, db: Session = Depends(get_db)) -> dict:
    """Convert a product via the same creative as its linked content."""
    return PromotionService(db).product(_tenant(request), product_id, payload.ad_type, payload.format_code)


all_promotion_routers = [router]
