"""FastAPI application: logging, middleware, error handlers, router wiring, and a
lifespan that opens/closes the Postgres pool. Exposes ``app`` for uvicorn / Vercel.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.config import get_settings
from app.core.db import create_pool
from app.core.errors import install_error_handlers
from app.core.logging import get_logger, setup_logging
from app.core.middleware import CorrelationIdMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.pool = await create_pool(settings.database_url)
    logger.info("app.startup", extra={"version": __version__})
    try:
        yield
    finally:
        if app.state.pool is not None:
            await app.state.pool.close()
            logger.info("db.pool.closed")
        logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Prediction-Market Analysis API",
        version=__version__,
        description="Read-only cross-venue prediction-market probabilities with provenance.",
        lifespan=lifespan,
    )
    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
