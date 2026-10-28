"""Page cards: one short doc per admin-app page, pinned ahead of the retrieved chunks.

A card is a markdown file under ``CORPUS_DIR/pages/`` whose frontmatter names the route it
describes (``page: "/profiles/:id"``). When a caller says which page the user is on, the card
goes first in the prompt, so "what is this page?" can be answered even though no search query
would find it. Cards are read per request (a handful of small files); no restart is needed
after editing one.
"""
from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from .config import CORPUS_DIR

_log = logging.getLogger(__name__)

CARD_MAX_CHARS = 4000  # a card must stay short; anything longer is cut, not trusted to fit


def page_card(page: str | None, root: Path | None = None) -> tuple[str, str] | None:
    """(title, text) of the card for this route pattern, or None when there is none."""
    wanted = (page or "").strip()
    cards = (root or CORPUS_DIR / "pages")
    if not wanted or not cards.is_dir():
        return None
    for path in sorted(cards.glob("*.md")):
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - one broken card must not fail every answer
            _log.warning("Skipping unreadable page card %s (%s)", path.name, exc)
            continue
        if str(post.get("page", "")).strip() == wanted:
            body = post.content.strip()
            if len(body) > CARD_MAX_CHARS:
                _log.warning(
                    "Page card %s is %d chars; truncating to %d",
                    path.name,
                    len(body),
                    CARD_MAX_CHARS,
                )
            return str(post.get("title") or path.stem), body[:CARD_MAX_CHARS]
    return None
