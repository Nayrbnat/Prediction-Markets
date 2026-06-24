"""GET /internal/refresh — the Vercel-cron ingestion trigger.

Vercel issues cron requests as GET with an ``Authorization: Bearer <CRON_SECRET>``
header (auto-injected when CRON_SECRET is set in the project). We verify it here.

Calls ``run_daily`` which ingests and (when digest_enabled) sends the digest email.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings
from app.core.errors import PersistenceError
from app.core.http import make_client
from app.core.logging import get_logger
from app.models.company import CompanyScan
from app.models.responses import RefreshResult
from app.notifications.email import make_email_sender
from app.services import company_scan, ingestion_service

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


@router.get("/internal/company-scan", response_model=CompanyScan, tags=["ops"])
async def company_scan_run(authorization: str | None = Header(default=None)) -> CompanyScan:
    """Discover + list specific-company bets (Kalshi 'Companies' + Polymarket searches).

    Its own (5-day) cron; discovery/listing only — no DB, no relative value. Emails the
    listing when digest recipients are configured. No-op (empty) when disabled.
    """
    settings = get_settings()
    if authorization != f"Bearer {settings.cron_secret}":
        logger.warning("company_scan.unauthorized")
        raise HTTPException(status_code=401, detail="unauthorized")

    generated_for = datetime.now(timezone.utc).date()
    if not settings.company_scan_enabled:
        logger.info("company_scan.disabled")
        return CompanyScan(generated_for=generated_for)

    gamma = make_client(settings.gamma_base_url)
    kalshi = make_client(settings.kalshi_base_url)
    try:
        result = await company_scan.scan(
            gamma, kalshi, settings=settings, generated_for=generated_for
        )
    finally:
        await gamma.aclose()
        await kalshi.aclose()

    recipients = settings.digest_recipients
    if recipients:
        sender = make_email_sender(settings)
        subject, html, text = company_scan.render_company_scan(result)
        await sender.send(subject=subject, html=html, text=text, to=recipients)
        logger.info("company_scan.emailed", extra={"recipients": len(recipients)})
    return result
