"""Async Postgres connection-pool helper. The pool is opened once at lifespan
startup and injected via ``app.state.pool``; persistence is the only layer that
runs SQL against it.

``create_pool`` returns ``None`` (rather than crashing) when no DSN is configured
or the database is unreachable, so the app still boots and ``/health`` can report
the degraded state. asyncpg is imported lazily so the package can be inspected
and tested without it installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg

logger = get_logger(__name__)


async def create_pool(dsn: str) -> asyncpg.Pool | None:
    if not dsn:
        logger.warning("db.pool.skip", extra={"reason": "DATABASE_URL not set"})
        return None
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        logger.error("db.pool.import_failed", extra={"reason": "asyncpg not installed"})
        return None
    try:
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
        logger.info("db.pool.open")
        return pool
    except Exception as exc:  # noqa: BLE001 - boot must not fail on DB unavailability
        logger.error("db.pool.open_failed", extra={"error": str(exc)})
        return None
