"""Deployment fingerprint tests for campaign activation operational logs."""

import importlib
from datetime import datetime, timezone


def test_version_info_uses_non_secret_deployment_environment(monkeypatch):
    monkeypatch.setenv("BUILD_DATE_TIME", "2026-10-19T15:37:00Z")
    monkeypatch.setenv("GIT_COMMIT", "abc1234")

    import campaign_activation

    module = importlib.reload(campaign_activation)
    assert module.version_info() == {
        "service": "campaign_activation",
        "build_date_time": "2026-10-19T15:37:00Z",
        "git_commit": "abc1234",
    }
    assert module.version_log_message() == (
        "campaign_activation build service=campaign_activation "
        "build_date_time=2026-10-19T15:37:00Z git_commit=abc1234"
    )


def test_version_info_falls_back_without_build_metadata(monkeypatch):
    monkeypatch.delenv("BUILD_DATE_TIME", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)

    import campaign_activation

    module = importlib.reload(campaign_activation)
    info = module.version_info()
    assert info["service"] == "campaign_activation"
    assert info["build_date_time"].endswith("Z")
    assert datetime.fromisoformat(
        info["build_date_time"].replace("Z", "+00:00")
    ).tzinfo == timezone.utc
    assert info["git_commit"] == "unknown"


def test_version_info_uses_startup_time_when_build_date_time_is_unknown(monkeypatch):
    monkeypatch.setenv("BUILD_DATE_TIME", "unknown")

    import campaign_activation

    started_before_reload = datetime.now(timezone.utc)
    module = importlib.reload(campaign_activation)
    started_after_reload = datetime.now(timezone.utc)
    build_time = datetime.fromisoformat(
        module.version_info()["build_date_time"].replace("Z", "+00:00")
    )
    assert started_before_reload.replace(microsecond=0) <= build_time
    assert build_time <= started_after_reload.replace(microsecond=0)
