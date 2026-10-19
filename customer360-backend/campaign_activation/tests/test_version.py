"""Deployment fingerprint tests for campaign activation operational logs."""

import importlib


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
    assert module.version_info() == {
        "service": "campaign_activation",
        "build_date_time": "unknown",
        "git_commit": "unknown",
    }
