"""FastAPI entrypoint for the Customer 360 AI Agent service.

Exposes the AI campaign-planning logic (moved out of customer360-api) over
HTTP so any caller can use it, provider-agnostic and local-LLM capable via
config (see config.py). Run with:

    uvicorn app:app --app-dir src --port 8009
"""

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ai_providers.base import AIProviderError
from campaign_planner import generate_campaign_plan, generate_zalo_campaign_plan
from config import settings
from models import (
    EmailCampaignPlanRequest,
    EmailCampaignPlanResponse,
    ZnsCampaignPlanRequest,
    ZnsCampaignPlanResponse,
)

# Setup module-level loggers
logging.basicConfig(level=logging.INFO)
agent_logger = logging.getLogger("agent")
prompts_logger = logging.getLogger("prompts")

# HTTPBearer enables the "Authorize" button in Swagger UI (/docs)
# auto_error=False allows us to handle missing tokens manually for dev environments
security = HTTPBearer(auto_error=False)


def require_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> None:
    """Gate /plan/* on the shared bearer token (`AGENT_API_TOKEN`). 
    Uses HTTPBearer for Swagger UI support and constant-time comparison to avoid timing leaks. 
    Blank config => auth disabled (dev only).
    """
    expected = settings.api_token
    if not expected:
        return
    
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid or missing agent API token")
        
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Invalid agent API token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern FastAPI lifecycle manager (replaces @app.on_event('startup')).
    Loads the published prompt snapshot from the DB. Best-effort: a DB hiccup 
    must not block startup (get() then raises PromptNotFound and the /plan 
    call returns 502).
    """
    if settings.api_token is None:
        agent_logger.warning("AGENT_API_TOKEN not set — /plan/* is UNAUTHENTICATED (dev only).")

    if settings.database_url:
        from prompts import get_store
        try:
            get_store().refresh()
            prompts_logger.info("Prompt store snapshot loaded (Postgres)")
        except Exception as exc:  # noqa: BLE001 -- never block startup on the prompt DB
            prompts_logger.warning("Prompt store refresh failed (%s)", exc)
            
    yield  # Application runs while yielded
    
    # Teardown logic (e.g., closing DB connections) would go here if needed


# Initialize FastAPI with the lifespan context manager
app = FastAPI(
    title="Customer 360 - AI Agent", 
    version=settings.api_version,
    root_path=settings.root_path,
    lifespan=lifespan
)


@app.exception_handler(AIProviderError)
async def ai_provider_exception_handler(request: Request, exc: AIProviderError):
    """Globally catch AIProviderError and convert it to a 502 Bad Gateway.
    This keeps the route functions clean of repetitive try/except blocks.
    """
    agent_logger.error(f"AI Provider error during request {request.url.path}: {exc}")
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health", tags=["Health"])
def health() -> dict:
    """Basic health check endpoint."""
    return {"service": "customer360-agent", "status": "ok", "model": settings.llm_model}


@app.post("/plan/email", response_model=EmailCampaignPlanResponse, tags=["Planning"],
          dependencies=[Depends(require_token)])
def plan_email(req: EmailCampaignPlanRequest) -> EmailCampaignPlanResponse:
    """Generates a campaign plan for Email channels."""
    # Exception handling is now managed by the global ai_provider_exception_handler
    plan = generate_campaign_plan(req.to_brief(), req.candidate_content_items)
    return EmailCampaignPlanResponse(**vars(plan))


@app.post("/plan/zalo", response_model=ZnsCampaignPlanResponse, tags=["Planning"],
          dependencies=[Depends(require_token)])
def plan_zalo(req: ZnsCampaignPlanRequest) -> ZnsCampaignPlanResponse:
    """Generates a campaign plan for Zalo ZNS channels."""
    # Exception handling is now managed by the global ai_provider_exception_handler
    plan = generate_zalo_campaign_plan(req.to_brief(), req.candidate_templates)
    return ZnsCampaignPlanResponse(**vars(plan))


if __name__ == "__main__":
    import uvicorn
    # Kept for local development. In production, run via CLI: uvicorn app:app ...
    uvicorn.run("app:app", host="0.0.0.0", port=8009, reload=True)