"""Application entrypoint.

Run locally with: uvicorn main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from core.config.settings import get_settings
from core.utils.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings)
logger = get_logger(__name__)

app = FastAPI(title=settings.app_name)

# Vite's default dev server port - the dashboard frontend runs separately
# from this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness/readiness probe."""
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting %s in %s mode", settings.app_name, settings.env)
    uvicorn.run(app, host="127.0.0.1", port=8000)
