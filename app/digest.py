"""CLI entry point for the daily digest (no re-ingestion): ``python -m app.digest``.

Opens a pool, builds the digest from the current store, renders it, and sends
(or prints via ConsoleEmailSender when SMTP is not configured).
Use this for local digest testing without repeating an ingestion run.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.core.db import create_pool
from app.core.logging import get_logger, setup_logging
from app.notifications.email import make_email_sender
from app.notifications.telegram import make_telegram_sender
from app.persistence.repository import PostgresMarketRepository
from app.services import notify
from app.services.digest_render import render_digest
from app.services.digest_service import build_digest

logger = get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    pool = await create_pool(settings.database_url)
    if pool is None:
        raise SystemExit("DATABASE_URL not set or unreachable; cannot build digest.")

    try:
        repo = PostgresMarketRepository(pool)
        digest = await build_digest(repo, settings)
        subject, html, text = render_digest(digest)
        logger.info(
            "digest.cli.rendered",
            extra={
                "mover_count": digest.mover_count,
                "tracked_count": digest.tracked_count,
                "subject": subject,
            },
        )

        await notify.fan_out(
            sender=make_email_sender(settings),
            tg_sender=make_telegram_sender(settings),
            subject=subject,
            html=html,
            text=text,
            recipients=settings.digest_recipients,
            context="digest_cli",
        )
    finally:
        await pool.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
