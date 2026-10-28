"""Read-only ORM mapping for the customer journey report view."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
import uuid

from sqlalchemy import Boolean, Date, Integer, Numeric, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from leo_customer360_dao.models.base import Base


class CdpJourneyMetric(Base):
    """Catalog definition for one journey-stage KPI in ``cdp_journey_metric``."""

    __tablename__ = "cdp_journey_metric"

    metric_code: Mapped[str] = mapped_column(String(60), primary_key=True)
    journey_stage: Mapped[Optional[str]] = mapped_column(String(30))
    label: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False, server_default="count")
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    applicable_business_models: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY['B2B', 'B2C', 'B2B2C']::text[]")
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    higher_is_better: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    rule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class JourneyStageReportRow(Base):
    """One row from ``vw_journey_stage_report``.

    The database view has no surrogate key, so its complete report scope is
    used as a read-only composite identity for SQLAlchemy.
    """

    __tablename__ = "vw_journey_stage_report"
    __table_args__ = {"info": {"is_view": True}}

    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    window_days: Mapped[int] = mapped_column(Integer, primary_key=True)
    journey_stage: Mapped[str] = mapped_column(String(30), primary_key=True)
    metric_code: Mapped[str] = mapped_column(String(60), primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(20))
    is_primary: Mapped[bool] = mapped_column(Boolean)
    higher_is_better: Mapped[bool] = mapped_column(Boolean)
    display_order: Mapped[int] = mapped_column(Integer)
    dim_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    dim_value: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    previous_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    change_: Mapped[Optional[Decimal]] = mapped_column("change", Numeric(18, 4))
    change_kind: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    business_model: Mapped[str] = mapped_column(String(10), primary_key=True)
    domain_code: Mapped[Optional[str]] = mapped_column(String(50), primary_key=True)
