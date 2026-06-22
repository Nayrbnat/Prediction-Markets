"""Email sender abstraction: Protocol, SMTP implementation, Console fallback, factory.

The SMTP call runs inside ``asyncio.to_thread`` so it never blocks the event loop.
Credentials are NEVER logged.
"""

from __future__ import annotations

import asyncio
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Protocol, runtime_checkable

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class EmailSender(Protocol):
    """Send an email; concrete implementations handle transport."""

    async def send(
        self,
        *,
        subject: str,
        html: str,
        text: str,
        to: list[str],
    ) -> None: ...


class SmtpEmailSender:
    """SMTP sender using STARTTLS. Runs the blocking smtplib call via asyncio.to_thread."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = from_addr

    def _send_sync(
        self, *, subject: str, html: str, text: str, to: list[str]
    ) -> None:
        """Synchronous send — called inside asyncio.to_thread."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(self._username, self._password)  # credentials NOT logged
            smtp.sendmail(self._from, to, msg.as_string())

    async def send(
        self,
        *,
        subject: str,
        html: str,
        text: str,
        to: list[str],
    ) -> None:
        logger.info(
            "email.sending",
            extra={"subject": subject, "recipients": len(to)},
        )
        await asyncio.to_thread(
            self._send_sync, subject=subject, html=html, text=text, to=to
        )
        logger.info(
            "email.sent",
            extra={"subject": subject, "recipients": len(to)},
        )


class ConsoleEmailSender:
    """Fallback sender: logs/prints subject + text body. Used when SMTP not configured."""

    async def send(
        self,
        *,
        subject: str,
        html: str,
        text: str,
        to: list[str],
    ) -> None:
        logger.info(
            "email.console",
            extra={"subject": subject, "to": to},
        )
        separator = "=" * 60
        block = "\n".join(
            [separator, f"SUBJECT: {subject}", f"TO: {', '.join(to)}", separator, text, separator]
        )
        # Encode-safe write: the digest (and external event titles) may contain
        # non-ASCII (e.g. "→"); a cp1252 console would otherwise raise
        # UnicodeEncodeError. Replace unmappable chars rather than crash.
        enc = sys.stdout.encoding or "utf-8"
        sys.stdout.write("\n" + block.encode(enc, "replace").decode(enc, "replace") + "\n")


def make_email_sender(settings: Settings) -> EmailSender:
    """Factory: return SmtpEmailSender when SMTP host + credentials present, else Console."""
    if settings.smtp_host and settings.smtp_username and settings.smtp_password:
        logger.info(
            "email.factory",
            extra={"sender": "smtp", "host": settings.smtp_host, "port": settings.smtp_port},
        )
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_addr=settings.digest_from or settings.smtp_username,
        )
    logger.info(
        "email.factory",
        extra={"sender": "console", "reason": "smtp_host or credentials not configured"},
    )
    return ConsoleEmailSender()
