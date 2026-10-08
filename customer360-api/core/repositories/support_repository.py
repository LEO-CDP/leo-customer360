"""Customer lookup and contact logging for the support assistant (``/support/ask``)."""

import re
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings

# Digits only, with the Vietnamese country code folded to the local leading 0, so
# 0901234567, +84901234567 and "090 123 4567" all compare equal. The same expression is
# applied to stored values, which are not normalized at write time.
_PHONE_SQL = (
    "CASE WHEN regexp_replace({v}, '[^0-9]', '', 'g') ~ '^84[0-9]{{9}}$' "
    "THEN '0' || substr(regexp_replace({v}, '[^0-9]', '', 'g'), 3) "
    "ELSE regexp_replace({v}, '[^0-9]', '', 'g') END"
)

_BY_EMAIL = """
    lower(btrim(m.email)) = :email
    OR EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(m.secondary_emails, '[]'::jsonb)) e
               WHERE lower(btrim(e->>'email')) = :email)
"""
_BY_PHONE = f"""
    {_PHONE_SQL.format(v='m.phone_number')} = :phone
    OR EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(m.secondary_phones, '[]'::jsonb)) e
               WHERE {_PHONE_SQL.format(v="e->>'phone'")} = :phone)
"""


def _profiles_table() -> str:
    """Schema-qualified table name; the ORM models are qualified the same way (settings.db_schema)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", settings.db_schema):
        raise ValueError("Invalid DB_SCHEMA")
    return f'"{settings.db_schema}".cdp_master_profiles'


class InvalidIdentityError(ValueError):
    """The identifier is neither an email address nor a phone number."""


def normalize_identity(value: str) -> tuple[str, str]:
    """Return ("email", lowercased address) or ("phone", canonical digits)."""
    raw = (value or "").strip()
    if "@" in raw:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
            raise InvalidIdentityError("Not a valid email address")
        return "email", raw.lower()
    if re.fullmatch(r"[+0-9 ().-]+", raw):
        digits = re.sub(r"\D", "", raw)
        if re.fullmatch(r"84[0-9]{9}", digits):
            digits = "0" + digits[2:]
        if 8 <= len(digits) <= 15:
            return "phone", digits
    raise InvalidIdentityError("Not a valid email address or phone number")


class SupportRepository:
    """Resolve a customer by contact detail; contact writes go through RelationsRepository."""

    def __init__(self, session: Session):
        self.session = session

    def find_master_profile_ids(self, tenant_id: uuid.UUID, identity: str) -> list[uuid.UUID]:
        """Active profiles of the tenant whose primary or secondary email/phone equals the value.

        Matching is exact after normalization, never a substring match, and returns at most
        two ids: one means resolved, two means ambiguous.
        """
        kind, value = normalize_identity(identity)
        condition = _BY_EMAIL if kind == "email" else _BY_PHONE
        rows = self.session.execute(
            text(
                f"""SELECT m.master_profile_id FROM {_profiles_table()} m
                    WHERE m.tenant_id = CAST(:tenant_id AS uuid) AND m.status_code = 1 AND ({condition})
                    ORDER BY m.master_profile_id LIMIT 2"""
            ),
            {"tenant_id": str(tenant_id), kind: value},
        )
        return [uuid.UUID(str(row[0])) for row in rows]
