"""CLI entry point for one ingestion run: ``python -m app.ingest``.

Opens a pool, runs the ingestion service, closes. For local runs and (v2) a
GitHub Actions job. The Vercel cron uses GET /internal/refresh instead.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.core.db import create_pool
from app.core.logging import get_logger, setup_logging
from app.persistence.repository import PostgresMarketRepository
from app.services.gateway import HttpGateway
from app.services.ingestion_service import run_ingestion

logger = get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    pool = await create_pool(settings.database_url)
    if pool is None:
        raise SystemExit("DATABASE_URL not set or unreachable; cannot ingest.")
    gateway = HttpGateway(settings)
    try:
        repo = PostgresMarketRepository(pool)
        result = await run_ingestion(repo=repo, gateway=gateway, settings=settings)
        logger.info("ingest.cli.done", extra=result.model_dump())
    finally:
        await gateway.aclose()
        await pool.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
