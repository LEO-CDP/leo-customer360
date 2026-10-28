"""ZNS template-param binding.

ZNS content is fixed by the approved template; only its typed parameters are
filled. The campaign supplies a base ``template_data`` map; each value may carry
simple ``{{merge}}`` tokens (first_name/last_name/name/phone) resolved per
recipient -- so a param can be a literal or a personalization token.
"""

import re

_TOKEN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_value(value, context: dict) -> str:
    if not isinstance(value, str):
        return value
    return _TOKEN.sub(lambda m: str(context.get(m.group(1), m.group(0))), value)


def render_params(template_data: dict, context: dict) -> dict:
    """Bind every param value against the recipient context (non-str values pass through)."""
    return {key: render_value(val, context) for key, val in (template_data or {}).items()}
