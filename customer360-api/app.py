"""FastAPI application entrypoint for the Customer 360 / Identity Resolution API.

Connects to PostgreSQL via SQLAlchemy 2 ORM using a pooled engine (see
core/database.py: pool_size/max_overflow/pool_recycle/pool_pre_ping configured
from .env). Run with:

    uvicorn app:app --reload

or simply:

    python app.py
"""

import logging
from core.apps.http_api_app import create_http_api_app
from core.apps.mcp_app import create_mcp_app

logging.basicConfig(level=logging.INFO)

mcp_app = create_mcp_app()
app = create_http_api_app(mcp_app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8008, reload=True)