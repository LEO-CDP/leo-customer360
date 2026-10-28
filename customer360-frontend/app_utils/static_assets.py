"""Static asset mounting for customer360-frontend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import FrontendSettings


def mount_static_assets(app: FastAPI, settings: FrontendSettings) -> None:
    """Mount frontend and tracking assets at their public route aliases."""
    static_dir = settings.base_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        if settings.root_path and settings.root_path != "/":
            app.mount(
                f"{settings.root_path}/static",
                StaticFiles(directory=str(static_dir)),
                name="prefixed-static",
            )

    tracking_sdk_dir = (
        settings.base_dir.parent / "customer360-event-api" / "static" / "c360-web-sdk"
    )
    if tracking_sdk_dir.exists():
        app.mount("/cdp-sdk", StaticFiles(directory=str(tracking_sdk_dir)), name="cdp-sdk")
        app.mount(
            "/static/c360-web-sdk",
            StaticFiles(directory=str(tracking_sdk_dir)),
            name="c360-web-sdk",
        )
        if settings.root_path and settings.root_path != "/":
            app.mount(
                f"{settings.root_path}/cdp-sdk",
                StaticFiles(directory=str(tracking_sdk_dir)),
                name="prefixed-cdp-sdk",
            )
            app.mount(
                f"{settings.root_path}/static/c360-web-sdk",
                StaticFiles(directory=str(tracking_sdk_dir)),
                name="prefixed-c360-web-sdk",
            )
    elif (static_dir / "c360-tracker").exists():
        app.mount(
            "/cdp-sdk",
            StaticFiles(directory=str(static_dir / "c360-tracker")),
            name="cdp-sdk",
        )
