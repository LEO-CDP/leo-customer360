"""Resolve the effective personalisation settings for a request.

Settings rows exist at three scopes: tenant, site and placement. The most
specific row wins (placement, then site, then tenant). Defaults when there is
no tenant row: personalisation OFF, hold-out 10%, consent mode ``opt_in``.

Two details:

* ``holdout_salt`` and ``consent_mode`` always come from the **tenant** row. The
  salt defines the tenant-wide measurement period (a visitor must not be held
  out on one placement but personalised on another), and consent mode is a
  tenant-level legal choice.
* ``enabled`` and ``holdout_pct`` come from the winning row, so a placement can
  be switched off (or given its own hold-out share) independently.

Resolution never raises: if the settings cannot be read, personalisation is
treated as off and serving falls back to default ranking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from personalization.consent import OPT_IN
from personalization.hot_state import HotStateUnavailable

logger = logging.getLogger(__name__)

_CACHE_NAME = ("settings",)


@dataclass(frozen=True)
class EffectiveSettings:
    """The settings that apply to one request."""

    enabled: bool
    holdout_pct: float
    holdout_salt: str
    consent_mode: str


DEFAULT_SETTINGS = EffectiveSettings(
    enabled=False, holdout_pct=10.0, holdout_salt="default", consent_mode=OPT_IN
)


def resolve_settings(
    rows: Iterable[Mapping[str, Any]],
    placement_key: Optional[str] = None,
    site_key: Optional[str] = None,
) -> EffectiveSettings:
    """Pick the effective settings from a tenant's rows (pure function)."""
    index = {(row["scope_type"], row["scope_ref"]): row for row in rows}

    tenant_row = index.get(("tenant", ""))
    winner = None
    if placement_key:
        winner = index.get(("placement", placement_key))
    if winner is None and site_key:
        winner = index.get(("site", site_key))
    if winner is None:
        winner = tenant_row
    if winner is None:
        return DEFAULT_SETTINGS

    return EffectiveSettings(
        enabled=bool(winner["enabled"]),
        holdout_pct=float(winner["holdout_pct"]),
        holdout_salt=tenant_row["holdout_salt"] if tenant_row else DEFAULT_SETTINGS.holdout_salt,
        consent_mode=tenant_row["consent_mode"] if tenant_row else DEFAULT_SETTINGS.consent_mode,
    )


class SettingsResolver:
    """Reads a tenant's settings rows (cached briefly) and resolves them per request."""

    def __init__(self, repository: Any, hot_state: Any, ttl_seconds: int) -> None:
        self._repository = repository
        self._hot_state = hot_state
        self._ttl_seconds = ttl_seconds

    def resolve(
        self,
        tenant_id: int,
        placement_key: Optional[str] = None,
        site_key: Optional[str] = None,
    ) -> EffectiveSettings:
        rows = self._load_rows(tenant_id)
        if rows is None:
            return DEFAULT_SETTINGS
        return resolve_settings(rows, placement_key, site_key)

    def _load_rows(self, tenant_id: int) -> Optional[list[dict[str, Any]]]:
        try:
            cached = self._hot_state.get_json(tenant_id, *_CACHE_NAME)
        except HotStateUnavailable:
            cached = None
        if cached is not None:
            return cached

        try:
            rows = self._repository.list_for_tenant(tenant_id)
        except Exception:  # noqa: BLE001 - e.g. table missing or database down
            logger.exception("settings: could not read personalisation settings")
            return None

        try:
            self._hot_state.set_json(tenant_id, _CACHE_NAME, rows, self._ttl_seconds)
        except HotStateUnavailable:
            pass
        return rows
