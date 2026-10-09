"""Planner selection from the global ``cdp_ai_agents`` registry.

Campaign planning runs only through an agent row the caller names by
``agent_code``. The row is validated before any campaign write (active,
generative, allow-listed model, consistent prompt state, every declared
variable supplied, provider-safe hyperparameters), and an immutable snapshot of
it is returned for ``crm_campaign.metadata.agent_provenance``. The registry is
read-only here: planning never mutates it.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from leo_customer360_dao.schemas.system import AiAgentRead

from core.repositories.ai_agent_repository import AiAgentRepository
from core.repositories.metadata_repository import MetadataNotFoundError

ALLOWED_PLANNER_MODELS = frozenset({"openai/gpt-5.6-luna", "google/gemini-3.5-flash-lite"})
SUPPORTED_PROMPT_ENGINES = frozenset({"none"})
# Provider parameters a registry row may set; anything else is refused rather
# than forwarded to the model client.
_HYPERPARAMETER_TYPES: dict[str, tuple[type, ...]] = {
    "temperature": (int, float),
    "top_p": (int, float),
    "max_tokens": (int,),
    "max_output_tokens": (int,),  # registry spelling (database-schema.sql); sent as max_tokens
    "response_format": (dict,),
}
# ponytail: every allowed model is reached through OpenRouter (one gateway key);
# add a per-provider map here if direct OpenAI/Gemini keys are ever needed.
_LITELLM_PREFIX = "openrouter/"


class AgentConfigurationInvalidError(ValueError):
    """The selected planner cannot be used; nothing may be persisted."""

    def __init__(self, agent_code: str, reasons: list[str]):
        self.agent_code = agent_code
        self.reasons = reasons
        super().__init__(f"Planner agent '{agent_code}' cannot be used: {'; '.join(reasons)}")


@dataclass(frozen=True)
class ResolvedPlanner:
    """What the agent service needs for one plan, plus the provenance to store."""

    model: str
    extra_config: dict[str, Any]
    instructions: str
    snapshot: dict[str, Any]


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _hyperparameter_problems(hyperparameters: Any) -> list[str]:
    if not isinstance(hyperparameters, dict):
        return ["hyperparameters must be a JSON object"]
    problems = []
    for key, value in hyperparameters.items():
        allowed = _HYPERPARAMETER_TYPES.get(key)
        if allowed is None:
            problems.append(f"hyperparameter '{key}' is not supported")
        elif isinstance(value, bool) or not isinstance(value, allowed):
            problems.append(f"hyperparameter '{key}' has an invalid value")
    return problems


def _provider_parameters(hyperparameters: dict[str, Any]) -> dict[str, Any]:
    """Validated registry parameters in the model client's (LiteLLM) spelling."""
    params = dict(hyperparameters)
    if "max_output_tokens" in params:
        params.setdefault("max_tokens", params.pop("max_output_tokens"))
    return params


def resolve_planner(session, agent_code: str, context: dict[str, Any]) -> ResolvedPlanner:
    """Validate the registry row for ``agent_code`` against ``context`` (the
    assembled, tenant-safe planning inputs keyed by variable name)."""
    try:
        agent = AiAgentRepository(session).get_ai_agent(agent_code)
    except MetadataNotFoundError as exc:
        raise AgentConfigurationInvalidError(agent_code, ["agent is not registered in cdp_ai_agents"]) from exc

    reasons = []
    if agent.status != "ACTIVE":
        reasons.append(f"status is {agent.status!r}, expected 'ACTIVE'")
    if agent.model_type != "generative_llm":
        reasons.append(f"model_type is {agent.model_type!r}, expected 'generative_llm'")
    if agent.model_name not in ALLOWED_PLANNER_MODELS:
        reasons.append(
            f"model_name {agent.model_name!r} is not an allowed planner model ({', '.join(sorted(ALLOWED_PLANNER_MODELS))})"
        )
    if agent.prompt_engine not in SUPPORTED_PROMPT_ENGINES:
        reasons.append(f"prompt_engine {agent.prompt_engine!r} is not supported")
    if not agent.prompt_key:
        reasons.append("prompt_key is missing")

    prompt_version = next(
        (entry for entry in (agent.prompt_versions or []) if isinstance(entry, dict) and entry.get("version") == agent.instruction_version),
        None,
    )
    if not agent.system_instructions:
        reasons.append("system_instructions are missing")
    elif prompt_version is None:
        reasons.append(f"prompt_versions has no entry for instruction_version {agent.instruction_version}")
    elif prompt_version.get("body") != agent.system_instructions:
        reasons.append(f"system_instructions do not match prompt_versions entry {agent.instruction_version}")

    declared = list(dict.fromkeys([*(agent.required_variables or []), *(agent.input_features or [])]))
    missing = [name for name in declared if _is_missing(context.get(name))]
    if missing:
        reasons.append(f"planning context does not supply required variables: {', '.join(missing)}")

    reasons.extend(_hyperparameter_problems(agent.hyperparameters or {}))

    if reasons:
        raise AgentConfigurationInvalidError(agent_code, reasons)

    snapshot = AiAgentRead.model_validate(agent).model_dump(mode="json")
    snapshot["resolved_prompt_version"] = prompt_version
    snapshot["run_at"] = datetime.now(timezone.utc).isoformat()
    return ResolvedPlanner(
        model=_LITELLM_PREFIX + agent.model_name,
        extra_config=_provider_parameters(agent.hyperparameters or {}),
        instructions=agent.system_instructions,
        snapshot=snapshot,
    )
