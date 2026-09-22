"""Per-recipient template rendering + tracking-link injection.

Templates use ``{{ variable }}`` placeholders (spaces optional). Rendering is a
plain substitution over a known context dict -- deliberately NOT a full template
engine (no Jinja dependency, no arbitrary expression eval), which keeps an
AI-authored template from ever executing code. Unknown placeholders render empty
rather than leaking ``{{ raw }}`` markup to a recipient.

Tracking helpers (open pixel, click-link rewrite) embed the tracking URLs
built from ``tracking.encode_tracking_token``; the receiving endpoints live in
customer360-api.
"""

import re
from typing import Optional
from urllib.parse import quote

from .tracking import sign_click_url

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
_HREF_PATTERN = re.compile(r'href=(["\'])(.*?)\1', re.IGNORECASE)


def render_string(template: Optional[str], context: dict) -> str:
    """Substitute ``{{ key }}`` placeholders from ``context`` (missing -> '')."""
    if not template:
        return ""
    return _PLACEHOLDER_PATTERN.sub(lambda m: str(context.get(m.group(1), "")), template)


def inject_tracking_pixel(html_body: str, pixel_url: Optional[str]) -> str:
    """Append a 1x1 open-tracking pixel just before </body> (or at the end)."""
    if not pixel_url:
        return html_body or ""
    pixel = f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none" />'
    if html_body and "</body>" in html_body.lower():
        # Preserve original casing of the tag while inserting before it.
        idx = html_body.lower().rindex("</body>")
        return html_body[:idx] + pixel + html_body[idx:]
    return (html_body or "") + pixel


def rewrite_links_for_click_tracking(
    html_body: str,
    click_base_url: Optional[str],
    token: str,
) -> str:
    """Rewrite each ``href`` to route through the click-redirect endpoint,
    carrying the tracking token + the URL-encoded original destination. Leaves
    anchors, mailto:, and already-wrapped links untouched."""
    if not click_base_url or not html_body:
        return html_body or ""

    def _wrap(match: re.Match) -> str:
        quote_char, url = match.group(1), match.group(2)
        if not url or url.startswith("#") or url.lower().startswith("mailto:"):
            return match.group(0)
        if url.startswith(click_base_url):
            return match.group(0)
        # Sign the destination (k=) so the click endpoint can reject a swapped
        # URL -- without this the redirect is an open redirect.
        k = sign_click_url(url)
        wrapped = f"{click_base_url}?u={quote(token, safe='')}&url={quote(url, safe='')}&k={quote(k, safe='')}"
        return f"href={quote_char}{wrapped}{quote_char}"

    return _HREF_PATTERN.sub(_wrap, html_body)
