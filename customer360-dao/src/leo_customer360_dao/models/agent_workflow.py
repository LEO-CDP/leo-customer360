"""Ordered AI-agent steps assigned to tenant-owned segments."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from leo_customer360_dao.models.base import Base


class CdpAgentWorkflow(Base):
    """One configured agent step in a segment's sequential workflow."""

    __tablename__ = "cdp_agent_workflow"
    __table_args__ = (
        UniqueConstraint("tenant_id", "segment_id", "agent_code", name="uq_cdp_agent_workflow_segment_agent"),
        UniqueConstraint(
            "tenant_id",
            "segment_id",
            "execution_order",
            name="uq_cdp_agent_workflow_execution_order",
            deferrable=True,
        ),
    )

    agent_workflow_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_segments.segment_id", ondelete="CASCADE"), nullable=False
    )
    agent_code: Mapped[str] = mapped_column(
        Text, ForeignKey("cdp_ai_agents.agent_code", ondelete="RESTRICT"), nullable=False
    )
    execution_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    schedule_definition: Mapped[str | None] = mapped_column(Text)
    candidate_content_item_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default=text("ARRAY[]::uuid[]")
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
