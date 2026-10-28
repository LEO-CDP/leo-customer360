"""HTTP routes for the nine use-case agents.

All routes are stateless: customer360-api resolves the tenant, owns the
database, runs any SQL and executes any tool. This service only turns the data
it is sent into a checked answer. `provider_for` picks the model per use case
(`LLM_USE_CASE_MODELS`, else `LLM_MODEL`); tests replace it.

Auth and the 502 mapping for provider failures are applied in app.py, the same
as for the planning routes."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from ai_providers.provider import LLMProvider, build_state
from config import settings
from models.use_cases import (
    DraftCopyRequest,
    DraftCopyResponse,
    MapSchemaRequest,
    MapSchemaResponse,
    NarrativeRequest,
    NarrativeResponse,
    NextStepResponse,
    OrchestrateStepRequest,
    PersonaDecideRequest,
    PersonaDecisionResponse,
    SqlRequest,
    SqlResponse,
    SupportAnswerRequest,
    SupportAnswerResponse,
    TicketsRequest,
    TicketsResponse,
)
from use_cases.analytics_chat import chart_spec
from use_cases.campaign_orchestrator import Step, Tool, plan_next_step
from use_cases.copy_drafter import draft_copy
from use_cases.performance_narrative import narrate
from use_cases.persona_decision_loop import ALLOWED_ACTIONS, decide
from use_cases.schema_mapper import propose_mappings
from use_cases.support_answerer import answer_support_question
from use_cases.text_to_sql import generate_sql
from use_cases.ticket_classifier import TEAMS, classify_batch

router = APIRouter()


def provider_for(use_case: str) -> LLMProvider:
    return LLMProvider(build_state(settings.model_for(use_case)))


@router.post("/analytics/sql", response_model=SqlResponse, tags=["Analytics"])
def analytics_sql(req: SqlRequest) -> SqlResponse:
    """Question (and earlier turns) to one checked SELECT. The caller runs it."""
    proposal = generate_sql(
        req.question, provider_for("sql"), req.schema_docs, req.k, [t.model_dump() for t in req.history]
    )
    return SqlResponse(status=proposal.status, sql=proposal.sql, error=proposal.error)


@router.post("/analytics/narrative", response_model=NarrativeResponse, tags=["Analytics"])
def analytics_narrative(req: NarrativeRequest) -> NarrativeResponse:
    """Result rows to a plain-English summary, with any number that is not in the data listed."""
    story = narrate(req.question, req.rows, provider_for("narrative"))
    return NarrativeResponse(narrative=story.text, ungrounded=story.ungrounded, chart=chart_spec(req.rows))


@router.post("/marketing/orchestrate/step", response_model=NextStepResponse, tags=["Marketing"])
def orchestrate_step(req: OrchestrateStepRequest) -> NextStepResponse:
    """The next validated action for a campaign workflow. The caller runs tools and approvals."""
    tools = {
        t.name: Tool(t.name, t.description, None, tuple(t.required), t.side_effect) for t in req.tools
    }
    steps = [Step(s.tool, s.args, s.observation, s.executed) for s in req.steps]
    step = plan_next_step(req.goal, tools, provider_for("orchestrator"), steps, req.context)
    return NextStepResponse(**asdict(step))


@router.post("/tickets/classify", response_model=TicketsResponse, tags=["Support"])
def classify_tickets(req: TicketsRequest) -> TicketsResponse:
    """Urgency, sentiment and routing team per ticket; doubtful labels are flagged for review."""
    teams = tuple(req.teams) if req.teams else TEAMS
    results = classify_batch([t.model_dump() for t in req.tickets], provider_for("tickets"), teams)
    return TicketsResponse(results=[asdict(r) for r in results])


@router.post("/onboarding/map-schema", response_model=MapSchemaResponse, tags=["Onboarding"])
def map_schema(req: MapSchemaRequest) -> MapSchemaResponse:
    """Proposed field mappings; anything uncertain is flagged for a person to review."""
    mappings = propose_mappings(
        [c.model_dump() for c in req.source_columns],
        req.target_schema,
        provider_for("schema_mapping"),
        include_samples=req.include_samples,
        max_targets=req.max_targets,
    )
    return MapSchemaResponse(mappings=[asdict(m) for m in mappings])


@router.post("/marketing/draft-copy", response_model=DraftCopyResponse, tags=["Marketing"])
def draft_marketing_copy(req: DraftCopyRequest) -> DraftCopyResponse:
    """Brand-grounded copy with safety checks. A draft with unsafe HTML comes back blocked."""
    draft = draft_copy(
        req.brief.model_dump(),
        req.guidelines,
        provider_for("copy"),
        max_guidelines=req.max_guidelines,
        max_subject=req.max_subject,
        max_preview=req.max_preview,
        max_body=req.max_body,
        max_caption=req.max_caption,
    )
    return DraftCopyResponse(**asdict(draft))


@router.post("/support/answer", response_model=SupportAnswerResponse, tags=["Support"])
def support_answer(req: SupportAnswerRequest) -> SupportAnswerResponse:
    """Answer only from the passages sent; web passages are used only from the allowed domain."""
    kb = [p.model_dump() for p in req.kb_passages]
    web = [p.model_dump() for p in req.web_passages]
    answer = answer_support_question(
        req.question,
        provider_for("support"),
        kb_search=lambda question: kb,
        web_search=(lambda question, domain: web) if web else None,
        allowed_domain=req.allowed_domain,
    )
    return SupportAnswerResponse(**asdict(answer))


@router.post("/persona/decide", response_model=PersonaDecisionResponse, tags=["Persona"])
def persona_decide(req: PersonaDecideRequest) -> PersonaDecisionResponse:
    """Perception, interpretation and a next best action from one customer's behavioural evidence."""
    decision = decide(
        req.evidence.model_dump(),
        provider_for("persona"),
        tuple(req.allowed_actions) if req.allowed_actions else ALLOWED_ACTIONS,
        set(req.suppressed),
    )
    return PersonaDecisionResponse(**asdict(decision))
