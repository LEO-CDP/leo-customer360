"""External-model access — OpenAI only (chat + embeddings).

`chat()` and `embed()` are the single integration seam for the rest of the
package; everything else is plain compute over your own data. To add another
provider later, branch inside these two functions.
"""
from __future__ import annotations

import functools
import json

from .config import CHAT_MODEL, EMBED_MODEL, OPENAI_API_KEY


@functools.lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    return OpenAI(api_key=OPENAI_API_KEY)  # api_key=None → SDK falls back to OPENAI_API_KEY env


def chat(system: str, user: str, *, schema: dict | None = None, max_tokens: int = 4096):
    """With `schema`: force JSON structured output and return the parsed object.
    Without: return the answer text."""
    kwargs = {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # max_completion_tokens (not the deprecated max_tokens) — the former is
        # required by newer models (o-series / gpt-5.x) and accepted by gpt-4.x.
        "max_completion_tokens": max_tokens,
    }
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "extraction", "schema": schema, "strict": True},
        }
    content = _client().chat.completions.create(**kwargs).choices[0].message.content
    return json.loads(content) if schema is not None else content


_MAX_EMBED_TOKENS = 8000  # text-embedding-3 accepts up to 8192 — leave headroom


@functools.lru_cache(maxsize=1)
def _encoder():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")  # text-embedding-3 tokenizer


def _truncate(text: str) -> str:
    tokens = _encoder().encode(text)
    if len(tokens) <= _MAX_EMBED_TOKENS:
        return text
    return _encoder().decode(tokens[:_MAX_EMBED_TOKENS])


def embed(texts: list[str], *, task: str = "document") -> list[list[float]]:
    """Embed texts with OpenAI. `task` is accepted for call-site symmetry, but OpenAI
    uses one endpoint for documents and queries. The same model must embed both.
    Inputs are truncated to the model's token limit so long docs don't 400."""
    if not texts:
        return []
    resp = _client().embeddings.create(model=EMBED_MODEL, input=[_truncate(t) for t in texts])
    return [d.embedding for d in resp.data]
