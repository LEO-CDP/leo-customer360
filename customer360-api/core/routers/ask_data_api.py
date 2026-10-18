"""POST /ai/ask-data: ask a question about the tenant's own data.

The agent service proposes one SELECT and writes the summary; this API runs the SQL
as the restricted `customer360_ask_data` role, so Postgres enforces the tenant, the
column allow-list and read-only access (see core/repositories/ask_data_repository.py
and customer360-database/migrations/012_ask_data_role.sql).

200 covers every answer, including `not_found`, `refused` and `error`; 422 is a bad
request; 502 is an agent failure; 503 means the feature is not configured or the
database role is not usable here.
"""

import logging
import uuid
from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.database import get_db
from core.repositories.ask_data_repository import AskDataAgentError, AskDataRepository, AskDataUnavailable
from leo_customer360_dao.config import settings

logger = logging.getLogger(__name__)

ask_data_router = APIRouter(prefix="/ai", tags=["AI - Ask your data"])


class AskDataTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=4000)


class AskDataRequest(BaseModel):
    """The tenant is never part of the request: it comes from the authenticated caller."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    # Earlier turns of the same conversation, held by the client, so follow-ups such as
    # "now split that by city" work. The server keeps no conversation state.
    history: list[AskDataTurn] = Field(default_factory=list, max_length=5)


class AskDataResponse(BaseModel):
    status: str  # ok | not_found | refused | error
    sql: Optional[str] = None
    rows: Optional[list[dict[str, Any]]] = None
    truncated: bool = False
    narrative: Optional[str] = None
    ungrounded: list[str] = Field(default_factory=list)
    chart: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    narrative_error: Optional[str] = None


@ask_data_router.post("/ask-data", response_model=AskDataResponse)
def ask_data(payload: AskDataRequest, request: Request, db: Session = Depends(get_db)) -> AskDataResponse:
    """Question (plus earlier turns) to a checked SELECT, its rows and a plain-English summary."""
    tenant_id = uuid.UUID(require_tenant(request))
    role = (settings.ask_data_db_role or "").strip()
    if not role:
        raise HTTPException(status_code=503, detail="Ask-data is not configured")

    try:
        result = AskDataRepository(db, role).ask(
            tenant_id, payload.question, [turn.model_dump() for turn in payload.history]
        )
    except AskDataUnavailable as exc:
        # The detail names database roles and privileges: log it, do not return it.
        logger.warning("ask-data unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Ask-data is not available") from exc
    except AskDataAgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AskDataResponse(**asdict(result))


all_ask_data_routers = [ask_data_router]
