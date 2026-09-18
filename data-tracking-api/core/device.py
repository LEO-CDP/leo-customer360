"""User-agent parsing and normalized device-type classification."""

from typing import Any, Optional

from ua_parser import parse


def parse_device_type(user_agent: Optional[str]) -> str:
    """Classify a user agent as mobile, tablet, desktop, or unknown."""
    if not user_agent or not user_agent.strip():
        return "unknown"

    try:
        parsed = parse(user_agent)
    except (TypeError, ValueError):
        return "unknown"

    device = getattr(parsed, "device", None)
    operating_system = getattr(parsed, "os", None)
    device_family = _normalized_family(device)
    os_family = _normalized_family(operating_system)

    if _contains_any(device_family, "ipad", "tablet", "kindle", "silk"):
        return "tablet"
    if _contains_any(device_family, "iphone", "ipod", "phone", "mobile"):
        return "mobile"
    if os_family in {"ios", "android", "windows phone", "windows mobile"}:
        return "mobile"
    if _contains_any(os_family, "windows", "mac os", "macos", "linux", "chrome os"):
        return "desktop"
    if device_family and device_family != "other":
        return "mobile"
    return "unknown"


def _normalized_family(value: Any) -> str:
    family = getattr(value, "family", "") if value is not None else ""
    return str(family or "").strip().lower()


def _contains_any(value: str, *needles: str) -> bool:
    return any(needle in value for needle in needles)
