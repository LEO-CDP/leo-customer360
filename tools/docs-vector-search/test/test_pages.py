from __future__ import annotations

import logging
import re
from pathlib import Path

import frontmatter
import pytest

from src.pages import CARD_MAX_CHARS, page_card


def _repo_root() -> Path:
    # test/test_pages.py -> test -> docs-vector-search -> tools -> <repo root>
    return Path(__file__).resolve().parents[3]


def _card(directory, name, page, title="Profile detail", body="This page shows one customer."):
    (directory / name).write_text(f'---\ntitle: {title}\npage: "{page}"\n---\n\n{body}\n', encoding="utf-8")


def test_card_is_found_by_the_route_pattern_in_its_frontmatter(tmp_path):
    _card(tmp_path, "profile-detail.md", "/profiles/:id")
    _card(tmp_path, "segments.md", "/segments", title="Segments", body="Segment list.")

    assert page_card("/profiles/:id", tmp_path) == ("Profile detail", "This page shows one customer.")
    assert page_card("  /segments ", tmp_path) == ("Segments", "Segment list.")


def test_unknown_page_empty_page_and_missing_folder_give_no_card(tmp_path):
    _card(tmp_path, "profile-detail.md", "/profiles/:id")

    assert page_card("/nope", tmp_path) is None
    assert page_card("", tmp_path) is None
    assert page_card(None, tmp_path) is None
    assert page_card("/profiles/:id", tmp_path / "does-not-exist") is None


def test_files_without_a_page_key_are_ignored_and_title_falls_back_to_the_file_name(tmp_path):
    (tmp_path / "notes.md").write_text("# just notes\n", encoding="utf-8")
    (tmp_path / "overview.md").write_text('---\npage: "/overview"\n---\n\nOverview.\n', encoding="utf-8")

    assert page_card("/overview", tmp_path) == ("overview", "Overview.")


def test_a_long_card_is_cut_to_the_limit(tmp_path):
    _card(tmp_path, "long.md", "/long", body="x" * (CARD_MAX_CHARS + 500))
    assert len(page_card("/long", tmp_path)[1]) == CARD_MAX_CHARS


def test_a_truncated_card_logs_a_warning(tmp_path, caplog):
    _card(tmp_path, "long.md", "/long", body="x" * (CARD_MAX_CHARS + 500))

    with caplog.at_level(logging.WARNING, logger="src.pages"):
        page_card("/long", tmp_path)

    assert "long.md" in caplog.text and "truncating" in caplog.text


def test_every_real_page_card_fits_the_limit():
    pages_dir = _repo_root() / "docs" / "pages"
    if not pages_dir.is_dir():
        pytest.skip(f"no page cards at {pages_dir}")

    for path in sorted(pages_dir.glob("*.md")):
        body = frontmatter.loads(path.read_text(encoding="utf-8")).content.strip()
        assert len(body) <= CARD_MAX_CHARS, (
            f"{path.name} body is {len(body)} chars, over the {CARD_MAX_CHARS} limit"
        )


# `C360.router.define("<pattern>", ...)` with a literal route string. The placeholder detail
# route `/admin/users/:id` is registered as `C360.router.define(entry.path, ...)` in
# placeholder-view.js, so this regex never finds it; it is listed as exempt for clarity.
_ROUTE_DEFINE = re.compile(r'C360\.router\.define\(\s*"([^"]+)"')
_EXEMPT_ROUTES = {"/admin/users/:id"}


def _frontend_routes() -> set[str]:
    js_dir = _repo_root() / "customer360-frontend" / "static" / "js"
    if not js_dir.is_dir():
        pytest.skip(f"frontend js not present at {js_dir}")
    routes: set[str] = set()
    for path in sorted(js_dir.rglob("*.js")):
        routes.update(_ROUTE_DEFINE.findall(path.read_text(encoding="utf-8")))
    return routes - _EXEMPT_ROUTES


def _cards_by_page() -> dict[str, Path]:
    pages_dir = _repo_root() / "docs" / "pages"
    cards: dict[str, Path] = {}
    for path in sorted(pages_dir.glob("*.md")):
        page = str(frontmatter.loads(path.read_text(encoding="utf-8")).get("page", "")).strip()
        if page:
            cards[page] = path
    return cards


def test_every_frontend_route_has_a_page_card():
    routes = _frontend_routes()
    assert routes, "no C360.router.define routes found in the frontend"

    cards = _cards_by_page()
    missing = sorted(route for route in routes if route not in cards)
    assert not missing, "routes without a page card: " + ", ".join(missing)


def test_no_two_page_cards_share_a_route():
    pages_dir = _repo_root() / "docs" / "pages"
    if not pages_dir.is_dir():
        pytest.skip(f"no page cards at {pages_dir}")

    seen: dict[str, Path] = {}
    duplicates = []
    for path in sorted(pages_dir.glob("*.md")):
        page = str(frontmatter.loads(path.read_text(encoding="utf-8")).get("page", "")).strip()
        if not page:
            continue
        if page in seen:
            duplicates.append(f"{page} ({seen[page].name}, {path.name})")
        seen[page] = path
    assert not duplicates, "duplicate page: values across cards: " + ", ".join(duplicates)


def test_a_broken_card_is_skipped_and_the_other_cards_still_work(tmp_path):
    (tmp_path / "a-broken.md").write_text('---\ntitle: [unclosed\npage: "/x"\n  bad: : :\n---\nbody\n', encoding="utf-8")
    (tmp_path / "good.md").write_text('---\ntitle: Good page\npage: "/good"\n---\nA fine card.\n', encoding="utf-8")

    assert page_card("/x", root=tmp_path) is None  # the broken card does not match, and does not raise
    assert page_card("/good", root=tmp_path) == ("Good page", "A fine card.")
