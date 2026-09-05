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
        "max_tokens": max_tokens,
    }
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "extraction", "schema": schema, "strict": True},
        }
    content = _client().chat.completions.create(**kwargs).choices[0].message.content
    return json.loads(content) if schema is not None else content


def embed(texts: list[str], *, task: str = "document") -> list[list[float]]:
    """Embed texts with OpenAI. `task` is accepted for call-site symmetry, but OpenAI
    uses one endpoint for documents and queries. The same model must embed both."""
    if not texts:
        return []
    resp = _client().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]
