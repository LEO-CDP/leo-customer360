"""Settings resolution: precedence, defaults, caching and failure behaviour."""

from personalization.settings import (
    DEFAULT_SETTINGS,
    SettingsResolver,
    resolve_settings,
)


def _row(scope_type, scope_ref="", *, enabled=True, holdout_pct=10.0, salt="s1", mode="opt_in"):
    return {
        "scope_type": scope_type,
        "scope_ref": scope_ref,
        "enabled": enabled,
        "holdout_pct": holdout_pct,
        "holdout_salt": salt,
        "consent_mode": mode,
    }


# --------------------------------------------------------------------------
# Pure resolution
# --------------------------------------------------------------------------


def test_no_rows_means_off_with_the_documented_defaults():
    result = resolve_settings([], "homepage_top", "site-1")
    assert result == DEFAULT_SETTINGS
    assert result.enabled is False
    assert result.holdout_pct == 10.0
    assert result.consent_mode == "opt_in"


def test_the_tenant_row_applies_when_nothing_more_specific_exists():
    rows = [_row("tenant", enabled=True, holdout_pct=20.0, mode="opt_out")]
    result = resolve_settings(rows, "homepage_top", "site-1")
    assert (result.enabled, result.holdout_pct, result.consent_mode) == (True, 20.0, "opt_out")


def test_a_placement_row_beats_a_site_row_which_beats_the_tenant_row():
    rows = [
        _row("tenant", enabled=True, holdout_pct=10.0),
        _row("site", "site-1", enabled=True, holdout_pct=20.0),
        _row("placement", "homepage_top", enabled=True, holdout_pct=30.0),
    ]
    assert resolve_settings(rows, "homepage_top", "site-1").holdout_pct == 30.0
    assert resolve_settings(rows, "sidebar", "site-1").holdout_pct == 20.0
    assert resolve_settings(rows, "sidebar", "other-site").holdout_pct == 10.0
    assert resolve_settings(rows, None, None).holdout_pct == 10.0


def test_one_placement_can_be_switched_off_while_the_tenant_stays_on():
    rows = [_row("tenant", enabled=True), _row("placement", "sidebar", enabled=False)]
    assert resolve_settings(rows, "sidebar", None).enabled is False
    assert resolve_settings(rows, "homepage_top", None).enabled is True


def test_a_site_can_override_the_tenant_switch():
    rows = [_row("tenant", enabled=True), _row("site", "site-1", enabled=False)]
    assert resolve_settings(rows, "homepage_top", "site-1").enabled is False
    assert resolve_settings(rows, "homepage_top", "site-2").enabled is True


def test_salt_and_consent_mode_always_come_from_the_tenant_row():
    rows = [
        _row("tenant", salt="period-A", mode="opt_out"),
        _row("placement", "homepage_top", salt="ignored", mode="opt_in"),
    ]
    result = resolve_settings(rows, "homepage_top", None)
    assert result.holdout_salt == "period-A"
    assert result.consent_mode == "opt_out"


def test_a_specific_row_without_a_tenant_row_still_applies_with_safe_defaults():
    result = resolve_settings([_row("placement", "homepage_top", enabled=True)], "homepage_top", None)
    assert result.enabled is True
    assert result.holdout_salt == DEFAULT_SETTINGS.holdout_salt
    assert result.consent_mode == "opt_in"


# --------------------------------------------------------------------------
# Resolver: caching and failure behaviour
# --------------------------------------------------------------------------


class FakeRepository:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls = 0

    def list_for_tenant(self, tenant_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


def test_rows_are_cached_so_the_database_is_read_once(hot_state):
    repo = FakeRepository([_row("tenant", enabled=True)])
    resolver = SettingsResolver(repo, hot_state, ttl_seconds=60)

    assert resolver.resolve(1).enabled is True
    assert resolver.resolve(1).enabled is True
    assert repo.calls == 1


def test_an_empty_result_is_cached_too(hot_state):
    repo = FakeRepository([])
    resolver = SettingsResolver(repo, hot_state, ttl_seconds=60)
    resolver.resolve(1)
    resolver.resolve(1)
    assert repo.calls == 1


def test_the_cache_expires(hot_state, clock):
    repo = FakeRepository([_row("tenant", enabled=True)])
    resolver = SettingsResolver(repo, hot_state, ttl_seconds=60)
    resolver.resolve(1)
    clock.advance(61)
    resolver.resolve(1)
    assert repo.calls == 2


def test_tenants_do_not_share_cached_settings(hot_state):
    repo = FakeRepository([_row("tenant", enabled=True)])
    resolver = SettingsResolver(repo, hot_state, ttl_seconds=60)
    resolver.resolve(1)
    resolver.resolve(2)
    assert repo.calls == 2


def test_the_database_is_used_directly_when_redis_is_down(hot_state):
    hot_state.available = False
    repo = FakeRepository([_row("tenant", enabled=True)])
    resolver = SettingsResolver(repo, hot_state, ttl_seconds=60)

    assert resolver.resolve(1).enabled is True
    assert resolver.resolve(1).enabled is True
    assert repo.calls == 2  # nothing is cached, but nothing fails


def test_a_failing_database_means_personalisation_is_off_not_an_error(hot_state):
    repo = FakeRepository(error=RuntimeError("relation does not exist"))
    resolver = SettingsResolver(repo, hot_state, ttl_seconds=60)

    result = resolver.resolve(1)

    assert result == DEFAULT_SETTINGS
    assert result.enabled is False
