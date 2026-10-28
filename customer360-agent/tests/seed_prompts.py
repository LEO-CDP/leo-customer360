"""Read prompt bodies straight from the database seed, so tests exercise exactly
what operators will get. The seed is customer360-database/init-cdp-ai-agents.sql;
service prompts there are `('agent_code', 'Display name', 'prompt.key', $prompt$body$prompt$)`."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

SEED_SQL = Path(__file__).resolve().parents[2] / "customer360-database" / "init-cdp-ai-agents.sql"

_ROW = re.compile(
    r"\(\s*'(?P<code>[a-z_0-9]+)',\s*'(?P<name>[^']+)',\s*'(?P<key>[a-z_0-9.]+)',\s*\$prompt\$(?P<body>.*?)\$prompt\$\s*\)",
    re.DOTALL,
)


@lru_cache(maxsize=1)
def load_seeded_prompts() -> dict[str, dict[str, str]]:
    """prompt key -> {"agent_code", "display_name", "body"}. Empty if the seed file is absent."""
    if not SEED_SQL.exists():
        return {}
    text = SEED_SQL.read_text(encoding="utf-8")
    return {
        m["key"]: {"agent_code": m["code"], "display_name": m["name"], "body": m["body"]}
        for m in _ROW.finditer(text)
    }
