"""The README's endpoint table is the contract callers read, so every route the
app serves must be listed there, and every listed route must exist."""

import re
from pathlib import Path

import app as app_module

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _served() -> set[tuple[str, str]]:
    # The OpenAPI schema is the reliable list: newer FastAPI registers included routers lazily,
    # so walking app.routes would miss them.
    paths = app_module.app.openapi()["paths"]
    return {(method.upper(), path) for path, ops in paths.items() for method in ops if method in _HTTP_METHODS}


def _documented() -> set[tuple[str, str]]:
    return set(re.findall(r"^\|\s*(GET|POST)\s*\|\s*`(/[^`]*)`", README, flags=re.MULTILINE))


def test_every_served_route_is_in_the_readme_endpoint_table():
    missing = sorted(_served() - _documented())

    assert missing == [], f"routes served but not documented in README.md: {missing}"


def test_every_documented_route_is_served():
    stale = sorted(_documented() - _served())

    assert stale == [], f"routes documented in README.md but not served: {stale}"
