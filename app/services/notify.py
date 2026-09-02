"""Fan-out delivery of a rendered notification to every configured sink.

One orchestration seam shared by the daily digest and the company-bet scan: email and
Telegram are sent independently so a failure (or absence of config) in one sink never
blocks the other. Keeps this cross-cutting concern out of the route handlers.
"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.notifications.email import EmailSender
from app.notifications.telegram import TelegramSender

logger = get_logger(__name__)


async def fan_out(
    *,
    sender: EmailSender,
    tg_sender: TelegramSender,
    subject: str,
    html: str,
    text: str,
    recipients: list[str],
    context: str,
) -> None:
    """Send ``subject/html/text`` to email (when recipients configured) and Telegram.

    Both sinks run concurrently; each failure is logged at WARNING and swallowed so the
    other sink still delivers. Telegram receives only the plain-text rendering.
    """

    async def _email() -> None:
        if not recipients:
            logger.warning(
                "notify.email_skipped",
                extra={"context": context, "reason": "no recipients configured"},
            )
            return
        await sender.send(subject=subject, html=html, text=text, to=recipients)
        logger.info(
            "notify.email_sent",
            extra={"context": context, "recipients": len(recipients)},
        )

    async def _telegram() -> None:
        await tg_sender.send(text=text)
        logger.info("notify.telegram_sent", extra={"context": context})

    results = await asyncio.gather(_email(), _telegram(), return_exceptions=True)
    for sink, result in zip(("email", "telegram"), results, strict=False):
        if isinstance(result, BaseException):
            # Log the exception TYPE only — a Telegram error can carry the token-bearing
            # URL in its string form, so never format the exception itself into a log.
            logger.warning(
                "notify.sink_failed",
                extra={"context": context, "sink": sink, "error": type(result).__name__},
            )
