"""GET /internal/refresh — the Vercel-cron ingestion trigger.

Vercel issues cron requests as GET with an ``Authorization: Bearer <CRON_SECRET>``
header (auto-injected when CRON_SECRET is set in the project). We verify it here.

Calls ``run_daily`` which ingests and (when digest_enabled) sends the digest email.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings
from app.core.errors import PersistenceError
from app.core.logging import get_logger
from app.models.responses import RefreshResult
from app.notifications.email import make_email_sender
from app.services import ingestion_service

router = APIRouter()
logger = get_logger(__name__)


@router.get("/internal/refresh", response_model=RefreshResult, tags=["ops"])
async def refresh(
    request: Request, authorization: str | None = Header(default=None)
) -> RefreshResult:
    settings = get_settings()
    if authorization != f"Bearer {settings.cron_secret}":
        logger.warning("refresh.unauthorized")
        raise HTTPException(status_code=401, detail="unauthorized")
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise PersistenceError("no database configured; cannot ingest")
    sender = make_email_sender(settings)
    return await ingestion_service.run_daily(
        repo=repo,
        gateway=request.app.state.gateway,
        sender=sender,
        settings=settings,
    )
