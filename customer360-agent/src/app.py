"""FastAPI entrypoint for the Customer 360 AI Agent service.

Exposes the AI campaign-planning logic (moved out of customer360-api) over
HTTP so any caller can use it, provider-agnostic and local-LLM capable via
config (see config.py). Run with:

    uvicorn app:app --app-dir src --port 8009
"""

import hmac
import logging

from fastapi import Depends, FastAPI, Header, HTTPException

from ai_providers.base import AIProviderError
from campaign_planner import generate_campaign_plan, generate_zalo_campaign_plan
from config import settings
from models import (
    EmailCampaignPlanRequest,
    EmailCampaignPlanResponse,
    ZnsCampaignPlanRequest,
    ZnsCampaignPlanResponse,
)

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Customer 360 - AI Agent", version=settings.api_version,
              root_path=settings.root_path)


def require_token(authorization: str = Header(default="")) -> None:
    """Gate /plan/* on the shared bearer token (`AGENT_API_TOKEN`). Constant-time
    compare to avoid timing leaks. Blank config => auth disabled (dev only)."""
    expected = settings.api_token
    if not expected:
        return
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing agent API token")


if not settings.api_token:
    logging.getLogger("agent").warning(
        "AGENT_API_TOKEN not set — /plan/* is UNAUTHENTICATED (dev only; set it in prod)."
    )


@app.on_event("startup")
def _init_prompt_store() -> None:
    """Load the published prompt snapshot from the DB. Schema + seed are owned by
    database-init. Best-effort: a DB hiccup must not block startup (get() then
    raises PromptNotFound and the /plan call returns 502)."""
    if not settings.database_url:
        return
    from prompts import get_store

    try:
        get_store().refresh()
        logging.getLogger("prompts").info("prompt store snapshot loaded (Postgres)")
    except Exception as exc:  # noqa: BLE001 -- never block startup on the prompt DB
        logging.getLogger("prompts").warning("prompt store refresh failed (%s)", exc)


@app.get("/health", tags=["Health"])
def health() -> dict:
    return {"service": "customer360-agent", "status": "ok", "model": settings.llm_model}


@app.post("/plan/email", response_model=EmailCampaignPlanResponse, tags=["Planning"],
          dependencies=[Depends(require_token)])
def plan_email(req: EmailCampaignPlanRequest) -> EmailCampaignPlanResponse:
    try:
        plan = generate_campaign_plan(req.to_brief(), req.candidate_content_items)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return EmailCampaignPlanResponse(**vars(plan))


@app.post("/plan/zalo", response_model=ZnsCampaignPlanResponse, tags=["Planning"],
          dependencies=[Depends(require_token)])
def plan_zalo(req: ZnsCampaignPlanRequest) -> ZnsCampaignPlanResponse:
    try:
        plan = generate_zalo_campaign_plan(req.to_brief(), req.candidate_templates)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ZnsCampaignPlanResponse(**vars(plan))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8009, reload=True)
