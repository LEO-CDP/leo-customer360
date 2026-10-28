"""Use case 1: customer support answers from a knowledge base, with a web
fallback restricted to the support domain.

Retrieval is injected (`kb_search`, `web_search`) so the existing docs service
or any search API can plug in. This module owns the policy:

- answer only from the passages given, or say it was not found;
- the fallback runs only when a domain is configured, and any result whose host
  is not that domain or a subdomain is dropped here, whatever the search API
  returned;
- cited sources are exactly the passages the model was shown.

The answering rules come from the published instruction (prompt store); the
passages and question are appended here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol
from urllib.parse import urlparse

from use_cases.instructions import instructions
from use_cases.text_clean import strip_em_dashes

NOT_FOUND = "NOT_FOUND_ANSWERS"
NOT_FOUND_MESSAGE = "I could not find this in the help centre."
MAX_PASSAGES = 5
MAX_PASSAGE_CHARS = 1500

_DATA = """Context:
{context}

Question:
{question}
"""

Passage = dict[str, str]


class Provider(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class SupportAnswer:
    answer: str
    found: bool
    source_kind: Optional[str] = None  # "kb" | "web"
    sources: list[str] = field(default_factory=list)


def _on_domain(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def _ask(question: str, passages: list[Passage], provider: Provider) -> Optional[str]:
    context = "\n\n".join(
        f"[{i}] {p.get('title', '')} ({p['url']})\n{p.get('text', '')[:MAX_PASSAGE_CHARS]}"
        for i, p in enumerate(passages, 1)
    )
    prompt = f"{instructions('support')}\n\n" + _DATA.format(context=context, question=question)
    reply = provider.complete(prompt).strip()
    if reply == NOT_FOUND:
        return None
    return strip_em_dashes(reply)


def answer_support_question(
    question: str,
    provider: Provider,
    kb_search: Callable[[str], list[Passage]],
    web_search: Optional[Callable[[str, str], list[Passage]]] = None,
    allowed_domain: Optional[str] = None,
) -> SupportAnswer:
    kb_passages = kb_search(question)[:MAX_PASSAGES]
    if kb_passages:
        answer = _ask(question, kb_passages, provider)
        if answer:
            return SupportAnswer(answer, True, "kb", [p["url"] for p in kb_passages])

    if web_search and allowed_domain:
        hits = [p for p in web_search(question, allowed_domain) if _on_domain(p["url"], allowed_domain)]
        hits = hits[:MAX_PASSAGES]
        if hits:
            answer = _ask(question, hits, provider)
            if answer:
                return SupportAnswer(answer, True, "web", [p["url"] for p in hits])

    return SupportAnswer(NOT_FOUND_MESSAGE, False)
