"""Published instruction text for each use-case agent, from the prompt store.

Same arrangement as the campaign planners: the bodies live in the database
(seeded by customer360-database/init-cdp-ai-agents.sql), versioned and editable
by operators, with no in-code fallback. A body holds only static instructions.
Each agent appends its own data (schema, rows, evidence and so on) in code, so a
stored prompt never needs `$placeholders`. A key that is not published raises
PromptNotFound, which the app reports as a 502."""

from __future__ import annotations

from prompts import get_store

# Use case (see config.USE_CASES) to prompt-store key.
KEYS = {
    "sql": "analytics.sql.instructions",
    "narrative": "analytics.narrative.instructions",
    "support": "support.answer.instructions",
    "tickets": "tickets.classify.instructions",
    "schema_mapping": "onboarding.map_schema.instructions",
    "copy": "marketing.draft_copy.instructions",
    "orchestrator": "marketing.orchestrate.instructions",
    "persona": "persona.decide.instructions",
}


def instructions(use_case: str) -> str:
    """The published instruction body for `use_case`."""
    return get_store().get(KEYS[use_case]).render().strip()
