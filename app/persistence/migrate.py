"""Apply schema.sql explicitly: ``python -m app.persistence.migrate``.

This is the only place DDL runs. It is never invoked on a request/ingestion path.
"""

from __future__ import annotations

import asyncio
from importlib.resources import files

from app.config import get_settings
from app.core.db import create_pool
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    pool = await create_pool(settings.database_url)
    if pool is None:
        raise SystemExit("DATABASE_URL not set or database unreachable; cannot migrate.")
    sql = (files("app.persistence") / "schema.sql").read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(sql)
    await pool.close()
    logger.info("migrate.done")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
