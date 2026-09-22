"""The prompt-store port (storage-agnostic). Imports neither the SDK nor a DB.

Mirrors the pattern in ai-agentic-framework/test-agent-v2 (common/prompts).

Why ``$``-substitution and not ``str.format``: prompt bodies are full of LITERAL
braces -- the JSON shapes we demand back (``{"content_item_ids": [ ... ]}``),
field lists, ``{var}`` examples. ``format``/``format_map`` would treat every one
as a placeholder and raise (or silently eat them). ``string.Template`` uses
``$name``, which appears nowhere in these prompts, so the literal braces survive.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template
from typing import Protocol

#: The only rendering mode: ``$name`` substitution, literal braces preserved.
NONE = "none"

_VAR = re.compile(r"\$(\w+)|\$\{(\w+)\}")


class PromptNotFound(KeyError):
    """No template is published under this key (and no default backs it)."""


def declared_vars(body: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for m in _VAR.finditer(body):
        seen.setdefault(m.group(1) or m.group(2), None)
    return tuple(seen)


@dataclass(frozen=True)
class PromptTemplate:
    key: str
    body: str
    version: int = 1          # DB-published versions start at 1
    engine: str = NONE
    required_vars: tuple[str, ...] = ()

    def render(self, params: Mapping[str, object] | None = None) -> str:
        p = dict(params or {})
        # required_vars also carries the agent's declared runtime context. Only
        # variables that are actual ``$name`` placeholders in this body must be
        # supplied to render; prompt bodies without placeholders can be used by
        # callers that append context separately.
        missing = [v for v in declared_vars(self.body) if v not in p]
        if missing:
            raise ValueError(f"prompt {self.key!r} missing params: {', '.join(missing)}")
        return Template(self.body).safe_substitute(p)


class PromptStore(Protocol):

    def get(self, key: str) -> PromptTemplate: ...

    def refresh(self) -> None: ...

    def pinned(self) -> dict[str, int]: ...

    def publish(
        self,
        key: str,
        body: str,
        *,
        engine: str = NONE,
        note: str = "",
        created_by: str = "admin",
        required_vars: tuple[str, ...] | None = None,
    ) -> int: ...

    def rollback(self, key: str, version: int) -> int: ...

    def history(self, key: str, limit: int = 20) -> list[dict]: ...
