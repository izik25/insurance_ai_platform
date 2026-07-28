"""Application entrypoint.

Run locally with: uvicorn main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.public_routes import router as public_api_router
from api.routes import router as api_router
from core.config.settings import get_settings
from core.database.session import init_db
from core.utils.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Schema is managed only via Base.metadata.create_all() (no Alembic -
    # see PROJECT_OVERVIEW.md) - creates missing tables on a fresh database,
    # a no-op against one that already has them.
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Vite's default dev server port, plus its next few fallback ports - if
# another project on the machine is already using 5173, Vite silently picks
# 5174/5175/etc instead, so the dashboard frontend could be on any of these
# during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{port}" for port in range(5173, 5178)],
    allow_methods=["GET", "PATCH"],
    allow_headers=["*"],
    # Content-Disposition isn't in the CORS-safelisted response headers, so
    # without this the browser silently hides it from fetch() even though the
    # request itself succeeds - the dashboard's public-API demo reads it to
    # recover the served file's name.
    expose_headers=["Content-Disposition"],
)

app.include_router(api_router)
app.include_router(public_api_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness/readiness probe."""
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting %s in %s mode", settings.app_name, settings.env)
    uvicorn.run(app, host="127.0.0.1", port=8000)
