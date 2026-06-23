"""GET /health — process liveness plus a Postgres round-trip."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.logging import get_logger
from app.models.responses import HealthStatus

router = APIRouter()
logger = get_logger(__name__)


@router.get("/health", response_model=HealthStatus, tags=["ops"])
async def health(request: Request) -> HealthStatus:
    pool = getattr(request.app.state, "pool", None)
    db_ok = False
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            db_ok = True
        except Exception as exc:  # noqa: BLE001 - health must report, not raise
            logger.warning("health.db_unreachable", extra={"error": str(exc)})
    return HealthStatus(status="ok" if db_ok else "degraded", database=db_ok)
