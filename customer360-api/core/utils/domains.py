"""Helpers for business-domain validation and lookups."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.system import SysDomain


def get_active_domain_codes(db: Session) -> set[str]:
    """Returns the active business-domain codes from ``sys_domain``."""
    rows = db.execute(select(SysDomain.domain_code).where(SysDomain.is_active.is_(True))).scalars().all()
    return {str(code) for code in rows if code}


def validate_domain_value(
    db: Session,
    domain: str | None,
    *,
    field_name: str = "domain",
    allow_all: bool = False,
) -> None:
    """Raises ``ValueError`` when ``domain`` is not a known active code."""
    if domain is None:
        return

    if allow_all and domain == "all":
        return

    allowed_domains = get_active_domain_codes(db)
    if domain not in allowed_domains:
        allowed_list = ", ".join(sorted(allowed_domains)) or "<none>"
        raise ValueError(f"{field_name} '{domain}' is not enabled; allowed values come from sys_domain: {allowed_list}")