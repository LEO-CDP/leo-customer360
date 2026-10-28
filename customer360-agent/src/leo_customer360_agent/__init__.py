"""Client library for the customer360-agent service.

Consumers (e.g. customer360-api) install this package and call the agent over
HTTP via `leo_customer360_agent.client` (planning routes) and
`leo_customer360_agent.agents` (use-case routes). The agent owns its own client
contract, the same way customer360-dao owns the shared data models. This package
does NOT contain the agent server (that runs from customer360-agent/src,
unpackaged).

The client names below are exported lazily. `client` imports the DAO, and the
pure shared modules here (`sql_guard`, `schema_docs`) are also used by the agent
server, which must not depend on the DAO. Importing a submodule therefore does
not import the client."""

from __future__ import annotations

import importlib
from typing import Any

_CLIENT_EXPORTS = (
    "AIProviderError",
    "CampaignPlanBrief",
    "GeneratedCampaignPlan",
    "GeneratedZnsCampaignPlan",
    "ZnsCampaignPlanBrief",
    "generate_campaign_plan",
    "generate_zalo_campaign_plan",
)

__all__ = list(_CLIENT_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _CLIENT_EXPORTS:
        return getattr(importlib.import_module("leo_customer360_agent.client"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
