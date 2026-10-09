"""LEO Assistant: answer a staff question knowing which page the user is on, and log the ask.

``POST /assistant/ask`` forwards the question and the page (a route pattern such as
``/profiles/:id``) to the docs RAG service, which pins that page's card ahead of the retrieved
documents. Every ask is recorded in ``sys_audit_log``. The tenant and user always come from the
authenticated session, never from the request body.
"""

import logging
import re
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.database import get_db
from core.repositories.assistant_repository import ConversationRepository
from core.repositories.screen_facts import PROFILE_PAGE, SCREEN_FACTS, ScreenFacts
from core.routers.support_api import SupportSource, ask_docs
from core.utils.text_safety import collapse_whitespace, mask_text
from leo_customer360_dao.models.system import SysAuditLog

logger = logging.getLogger(__name__)

assistant_router = APIRouter(prefix="/assistant", tags=["C360 - Assistant"])

QUESTION_MAX_LEN = 2000
LOGGED_QUESTION_MAX_LEN = 500
STORED_MESSAGE_MAX_LEN = 2000
STORED_SUMMARY_MAX_LEN = 800  # the column's CHECK
VIEW_MAX_LEN = 60
DIALOG_MAX_LEN = 80

_PAGE_PATTERN = r"^/[A-Za-z0-9_:/\-]*$"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class AssistantAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUESTION_MAX_LEN)
    # The admin route pattern the user is on, e.g. "/profiles/:id" (never a concrete URL).
    page: Optional[str] = Field(default=None, max_length=120, pattern=_PAGE_PATTERN)
    master_profile_id: Optional[uuid.UUID] = None
    # The route's id parameter on an id page (segments, campaigns, personas). On the profile page
    # this and master_profile_id are the same id.
    entity_id: Optional[uuid.UUID] = None
    # The chat this message belongs to, as returned by the previous answer; omit to start a new one.
    conversation_id: Optional[uuid.UUID] = None
    # The selected period on a dashboard page, in days (1-400).
    period_days: Optional[int] = Field(default=None, ge=1, le=400)
    # The visible sub-tab on the page, one short line (e.g. "Timeline").
    view: Optional[str] = Field(default=None, max_length=VIEW_MAX_LEN)
    # The form or dialog open on the page, one short line (e.g. "Add Data Source").
    dialog: Optional[str] = Field(
        default=None,
        max_length=DIALOG_MAX_LEN,
        description="Title of the form or dialog open on the page, e.g. 'Add Data Source'",
    )


class AssistantBasis(BaseModel):
    """Which screen context the answer used; the panel shows it as a "Based on" line."""

    page_guide: bool = False
    profile_data: bool = False
    screen_data: bool = False
    screen_label: Optional[str] = None


class AssistantAskResponse(BaseModel):
    answer: str
    found: bool
    status: str
    missing: list[str] = []
    clarify: Optional[str] = None
    sources: list[SupportSource] = []
    basis: Optional[AssistantBasis] = None
    # None when the caller has no user (an API key): such asks are not remembered.
    conversation_id: Optional[uuid.UUID] = None


class ConversationMessage(BaseModel):
    role: str
    text: str
    status: Optional[str] = None
    clarify: Optional[str] = None
    sources: list[SupportSource] = []
    created_at: Optional[datetime] = None


class ConversationResponse(BaseModel):
    conversation_id: Optional[uuid.UUID] = None
    messages: list[ConversationMessage] = []


def sanitize_label(text: Optional[str], cap: int) -> Optional[str]:
    """Make a tab or dialog title safe to send to the model and the audit log.

    Control characters (including newlines) become spaces, runs of whitespace collapse, and the
    text is masked and capped. A label is only a hint, so a blank or unusable one becomes ``None``
    rather than failing the ask. Unicode letters (Vietnamese) are kept.
    """
    if not text:
        return None
    cleaned = collapse_whitespace(_CONTROL_CHARS.sub(" ", text))
    cleaned = mask_text(cleaned, cap)
    return cleaned or None


def _record_ask(
    db: Session,
    tenant_id,
    user_id,
    payload: AssistantAskRequest,
    result: dict,
    latency_ms: int,
    fields: list[str],
    facts_page: Optional[str],
    question: str,
    view: Optional[str],
    dialog: Optional[str],
    conversation_id: Optional[uuid.UUID],
    history_messages: int,
) -> None:
    """The saved chat messages and one sys_audit_log row per ask, in one transaction. A problem here
    must never cost the user their answer."""
    try:
        found = bool(result.get("found", True))
        if conversation_id is not None and user_id is not None:
            try:
                # A savepoint: if the chat cannot be saved (e.g. the session user is not a member of the
                # session tenant) only the messages are skipped, the audit row below still lands.
                with db.begin_nested():
                    ConversationRepository(db).add_exchange(
                        tenant_id,
                        user_id,
                        conversation_id,
                        payload.page,
                        payload.master_profile_id or payload.entity_id,
                        question,
                        mask_text(str(result.get("answer") or ""), STORED_MESSAGE_MAX_LEN),
                        str(result.get("status") or ("answered" if found else "not_found")),
                        result.get("clarify"),
                        [
                            {k: str(src.get(k) or "") for k in ("path", "title", "heading")}
                            for src in (result.get("sources") or [] if found else [])
                        ],
                        mask_text(str(result.get("summary") or ""), STORED_SUMMARY_MAX_LEN) or None,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("Could not save the assistant chat messages", exc_info=True)
        db.add(
            SysAuditLog(
                tenant_id=tenant_id,
                user_id=user_id,
                action="ASK",
                resource_type="leo_assistant",
                resource_id=str(payload.master_profile_id) if payload.master_profile_id else payload.page,
                after_data={
                    "page": payload.page,
                    "view": view,
                    "dialog": dialog,
                    "question": question[:LOGGED_QUESTION_MAX_LEN],
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "history_messages": history_messages,
                    "follows_up": result.get("follows_up"),
                    "rewritten": mask_text(str(result.get("rewritten") or ""), LOGGED_QUESTION_MAX_LEN) or None,
                    "status": result.get("status"),
                    "found": result.get("found"),
                    "clarify": result.get("clarify"),
                    "source_paths": [s.get("path") for s in result.get("sources") or []],
                    "latency_ms": latency_ms,
                    # Which screen facts were sent: names only, never the values, plus the pattern
                    # whose loader ran.
                    "fact_fields": fields,
                    "facts_page": facts_page,
                },
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Could not write the assistant audit row", exc_info=True)
        db.rollback()


def _session_user(request: Request) -> Optional[uuid.UUID]:
    raw_user = getattr(request.state, "user_id", None)
    return uuid.UUID(str(raw_user)) if raw_user else None


def _build_basis(raw: Optional[dict], screen: Optional[ScreenFacts]) -> Optional[dict]:
    """The docs service's basis, with the screen label and the backwards-compatible screen_data.

    The docs service already returns ``screen_data``; until it does, an answer that used the facts
    block reports it through ``profile_data`` (the two are the same value).
    """
    if raw is None:
        return None
    basis = dict(raw)
    if "screen_data" not in basis:
        basis["screen_data"] = bool(basis.get("profile_data", False))
    if screen is not None:
        basis["screen_label"] = screen.label
    return basis


def _load_screen(db: Session, tenant_id, payload: AssistantAskRequest) -> Optional[ScreenFacts]:
    """Run the loader for the page the user is on, or None when there is nothing to send.

    A 404 is raised for an id page whose object is not in the tenant, before anything reaches the
    docs service. An id page without an id, or a page without a loader, is answered without facts.
    """
    loader = SCREEN_FACTS.get(payload.page) if payload.page else None
    if loader is None:
        return None
    entity_id = payload.entity_id or payload.master_profile_id
    is_id_page = ":" in payload.page
    if is_id_page and entity_id is None:
        return None  # no id on an id page: answer the question without facts
    loaded = loader(db, tenant_id, entity_id, payload.period_days)
    if loaded is None and is_id_page:
        # The profile page keeps its historical message; other id pages share the generic one.
        detail = "Profile not found." if payload.page == PROFILE_PAGE else "Not found."
        raise HTTPException(status_code=404, detail=detail)
    return loaded


@assistant_router.post("/ask", response_model=AssistantAskResponse)
def assistant_ask(payload: AssistantAskRequest, request: Request, db: Session = Depends(get_db)) -> AssistantAskResponse:
    tenant_id = uuid.UUID(require_tenant(request))
    user_id = _session_user(request)

    # On the profile page the frontend may send the same id as either field; two different ids
    # cannot both be the customer on screen.
    if (
        payload.page == PROFILE_PAGE
        and payload.entity_id is not None
        and payload.master_profile_id is not None
        and payload.entity_id != payload.master_profile_id
    ):
        raise HTTPException(status_code=422, detail="entity_id and master_profile_id must match on the profile page.")

    screen = _load_screen(db, tenant_id, payload)

    # Masked first: the model, the chat history and the audit log only ever see the masked text.
    question = mask_text(payload.question, QUESTION_MAX_LEN)
    view = sanitize_label(payload.view, VIEW_MAX_LEN)
    dialog = sanitize_label(payload.dialog, DIALOG_MAX_LEN)

    # The chat is keyed by the object on screen: master_profile_id on the profile page, entity_id on
    # any other id page (segments, campaigns, personas). Without a user (an API key) it is not remembered.
    conversation_id, history, summary = None, [], None
    if user_id is not None:
        conversation_id, history, summary = ConversationRepository(db).window(
            tenant_id, user_id, payload.conversation_id, payload.page,
            payload.master_profile_id or payload.entity_id,
        )

    started = time.perf_counter()
    result = ask_docs(
        question,
        page=payload.page,
        context=(screen.lines or None) if screen else None,
        context_title=screen.title if screen else None,
        view=view,
        dialog=dialog,
        history=history or None,
        summary=summary,
    )
    _record_ask(
        db, tenant_id, user_id, payload, result, int((time.perf_counter() - started) * 1000),
        screen.fields if screen else [],
        payload.page if screen else None,
        question, view, dialog,
        conversation_id, len(history),
    )

    found = bool(result.get("found", True))
    return AssistantAskResponse(
        answer=str(result.get("answer") or ""),
        found=found,
        status=str(result.get("status") or ("answered" if found else "not_found")),
        missing=[str(m) for m in result.get("missing") or []],
        clarify=result.get("clarify"),
        sources=result.get("sources") or [],
        basis=_build_basis(result.get("basis"), screen),
        conversation_id=conversation_id,
    )


@assistant_router.get("/conversation", response_model=ConversationResponse)
def assistant_conversation(
    request: Request,
    page: Optional[str] = Query(default=None, max_length=120, pattern=_PAGE_PATTERN),
    master_profile_id: Optional[uuid.UUID] = None,
    entity_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
) -> ConversationResponse:
    """The caller's latest saved chat on this page and object (so the panel can restore it).

    The object is ``master_profile_id`` on the profile page and ``entity_id`` on any other id page,
    the same key ``assistant_ask`` uses.
    """
    tenant_id = uuid.UUID(require_tenant(request))
    user_id = _session_user(request)
    if user_id is None:
        return ConversationResponse()
    conversation_id, rows = ConversationRepository(db).latest(
        tenant_id, user_id, page, master_profile_id or entity_id
    )
    return ConversationResponse(
        conversation_id=conversation_id,
        messages=[
            ConversationMessage(
                role=row.role,
                text=row.message_text,
                status=row.status,
                clarify=row.clarify,
                sources=row.sources or [],
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


all_assistant_routers = (assistant_router,)
