"""Support assistant: answer a customer question from the docs and log the contact.

``POST /support/ask`` calls the docs RAG service (tools/docs-vector-search), resolves the
customer by email or phone inside the caller's tenant, and records the answer in
``crm_customer_contacts`` only when exactly one active profile matches.
"""

import logging
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.database import get_db
from core.repositories.relations_repository import RelationsRepository
from core.repositories.support_repository import InvalidIdentityError, SupportRepository
from leo_customer360_dao.config import settings

logger = logging.getLogger(__name__)

support_router = APIRouter(prefix="/support", tags=["C360 - Support Assistant"])

QUESTION_MAX_LEN = 2000


class SupportAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUESTION_MAX_LEN)
    identity: Optional[str] = Field(
        default=None, max_length=254, description="Customer email address or phone number"
    )


class SupportSource(BaseModel):
    path: str
    title: str
    heading: str


class SupportAskResponse(BaseModel):
    answer: str
    found: bool
    status: str
    missing: list[str] = []
    clarify: Optional[str] = None
    sources: list[SupportSource] = []
    # not_provided, matched, not_found, or ambiguous (more than one active profile matches).
    identity: str
    contact_id: Optional[uuid.UUID] = None


def ask_docs(
    question: str,
    page: Optional[str] = None,
    context: Optional[list[str]] = None,
    context_title: Optional[str] = None,
    view: Optional[str] = None,
    dialog: Optional[str] = None,
    history: Optional[list[dict]] = None,
    summary: Optional[str] = None,
) -> dict:
    """Call the docs service's /ask; any failure is a 502 so nothing is logged.

    ``page`` (a route pattern) selects a page card; ``context`` carries facts about the object on
    screen and ``context_title`` heads that block (e.g. "Segment on screen"); ``view`` and ``dialog``
    name the open sub-tab and form/dialog; ``history`` carries the earlier messages of the chat
    ({role, text, clarify}, oldest first). The docs service only honours all of them because of the
    internal-auth header below. ``summary`` is the chat's running summary from the previous answer.
    """
    headers = {"X-Internal-Auth": settings.docs_internal_secret} if settings.docs_internal_secret else None
    body: dict = {"question": question}
    if page:
        body["page"] = page
    if context:
        body["context"] = context
    if context_title:
        body["context_title"] = context_title
    if view:
        body["view"] = view
    if dialog:
        body["dialog"] = dialog
    if history:
        body["history"] = history
    if summary:
        body["summary"] = summary
    try:
        with httpx.Client(timeout=settings.docs_search_timeout) as client:
            response = client.post(f"{settings.docs_search_url.rstrip('/')}/ask", json=body, headers=headers)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Docs service /ask failed: %s", exc)
        raise HTTPException(status_code=502, detail="The documentation service is unavailable.") from exc


def _log_contact(db: Session, tenant_id, user_id, master_profile_id, answer: str) -> Optional[uuid.UUID]:
    """Record the exchange in crm_customer_contacts. The contact's staff user must belong to the same
    tenant (composite FK); a session user that does not (e.g. a root user viewing another tenant) is
    recorded as no user rather than failing the request. A logging failure never costs the answer."""
    staff = uuid.UUID(str(user_id)) if user_id else None
    for candidate in dict.fromkeys([staff, None]):
        try:
            contact = RelationsRepository(db).create_customer_contact(
                {
                    "tenant_id": tenant_id,
                    "user_id": candidate,
                    "master_profile_id": master_profile_id,
                    "contact_type": "support",
                    "contact_channel": "chat",
                    "contact_content": answer,
                }
            )
            return contact.contact_id
        except IntegrityError:
            db.rollback()
    logger.warning("Could not record the support contact for profile %s", master_profile_id, exc_info=True)
    return None


@support_router.post("/ask", response_model=SupportAskResponse)
def support_ask(payload: SupportAskRequest, request: Request, db: Session = Depends(get_db)) -> SupportAskResponse:
    tenant_id = uuid.UUID(require_tenant(request))
    user_id = getattr(request.state, "user_id", None)
    identity = (payload.identity or "").strip()

    profile_ids: list[uuid.UUID] = []
    if identity:
        try:
            profile_ids = SupportRepository(db).find_master_profile_ids(tenant_id, identity)
        except InvalidIdentityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = ask_docs(payload.question)
    answer = str(result.get("answer") or "")

    contact_id = None
    # A clarifying question is not a finished exchange, so it is not recorded.
    if len(profile_ids) == 1 and answer and not result.get("clarify"):
        contact_id = _log_contact(db, tenant_id, user_id, profile_ids[0], answer)

    return SupportAskResponse(
        answer=answer,
        found=bool(result.get("found", True)),
        status=str(result.get("status") or ("answered" if result.get("found", True) else "not_found")),
        missing=[str(m) for m in result.get("missing") or []],
        clarify=result.get("clarify"),
        sources=result.get("sources") or [],
        identity=(
            "not_provided" if not identity else "matched" if len(profile_ids) == 1
            else "ambiguous" if len(profile_ids) > 1 else "not_found"
        ),
        contact_id=contact_id,
    )


all_support_routers = (support_router,)
