"""
Customer 360 Promotions API entrypoint.

Run:

    uvicorn app:app --reload --port 9009

or:

    python app.py
"""

from core.application import PromotionsApplication


# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

promotions_application = PromotionsApplication()

app = promotions_application.create_app()


# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=9009,
        reload=True,
    )
