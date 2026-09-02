"""Telegram sender abstraction: Protocol, HTTP implementation, Console fallback, factory.

Mirrors ``app/notifications/email.py``. The bot token is NEVER logged; it appears only
in the request URL path, so failures are re-raised with a sanitized message (status code
only) and ``from None`` so the token cannot leak through a chained traceback either.

Verified against the official Telegram Bot API ``sendMessage`` (core.telegram.org/bots/api):
POST ``https://api.telegram.org/bot<token>/sendMessage`` with JSON
``{chat_id, text, message_thread_id?, parse_mode?}``. We send plain text (no ``parse_mode``)
so a digest containing ``$``/``_``/``→`` never 400s on unescaped Markdown entities.
"""

from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

import httpx

from app.config import Settings
from app.core.errors import RateLimitError, SourceError
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter, with_backoff

logger = get_logger(__name__)

# Telegram's hard limit on a single text message.
_MAX_MESSAGE_CHARS = 4096
# Conservative pace: Telegram allows ~20 messages/minute to one group. A daily digest
# is only a handful of chunks, so 1 msg/s stays well under the cap without slowing sends.
_RATE_PER_SEC = 1.0
_TIMEOUT_SECONDS = 15.0


@runtime_checkable
class TelegramSender(Protocol):
    """Send a plain-text Telegram message; implementations handle transport."""

    async def send(self, *, text: str) -> None: ...


def _parse_thread_id(topic_id: str) -> int | None:
    """Parse a forum topic id into an int, or None when blank/non-numeric.

    Fail-safe: an unset or malformed topic id means "omit message_thread_id" (post to
    General) rather than crash the cron.
    """
    token = (topic_id or "").strip()
    if not token:
        return None
    try:
        return int(token)
    except ValueError:
        logger.warning("telegram.bad_topic_id", extra={"topic_id": token})
        return None


def _chunk(text: str, limit: int = _MAX_MESSAGE_CHARS) -> list[str]:
    """Split ``text`` into ``<=limit`` chunks, preferring newline boundaries.

    A single line longer than ``limit`` is hard-split. Guarantees every returned chunk
    is at most ``limit`` characters so a long digest never raises.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # Hard-split any single over-long line first.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class HttpTelegramSender:
    """Async httpx sender: POSTs each chunk to the Bot API ``sendMessage`` endpoint."""

    def __init__(
        self,
        *,
        api_base_url: str,
        token: str,
        chat_id: str,
        topic_id: str = "",
        rate_per_sec: float = _RATE_PER_SEC,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._token = token
        self._chat_id = chat_id
        self._thread_id = _parse_thread_id(topic_id)
        self._limiter = AsyncRateLimiter(rate_per_sec=rate_per_sec)

    @property
    def _url(self) -> str:
        # Token lives in the path — never log this string.
        return f"{self._api_base_url}/bot{self._token}/sendMessage"

    async def _post(self, payload: dict[str, object]) -> None:
        async def _do() -> None:
            await self._limiter.acquire()
            async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_SECONDS)) as client:
                resp = await client.post(self._url, json=payload)
            if resp.status_code == 429:
                logger.warning("telegram.throttled", extra={"chat_id": self._chat_id})
                raise RateLimitError("telegram returned 429 for sendMessage")
            resp.raise_for_status()

        try:
            await with_backoff(_do, venue="telegram")
        except RateLimitError:
            # Sanitized message; the token is never in a RateLimitError.
            raise SourceError("telegram sendMessage throttled after retries") from None
        except httpx.HTTPStatusError as exc:
            # `from None`: the httpx exception carries the token-bearing URL — drop it.
            raise SourceError(
                f"telegram sendMessage returned {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise SourceError(f"telegram request failed: {type(exc).__name__}") from None

    async def send(self, *, text: str) -> None:
        chunks = _chunk(text)
        logger.info(
            "telegram.sending",
            extra={
                "chat_id": self._chat_id,
                "chunks": len(chunks),
                "has_topic": self._thread_id is not None,
            },
        )
        for chunk in chunks:
            payload: dict[str, object] = {"chat_id": self._chat_id, "text": chunk}
            if self._thread_id is not None:
                payload["message_thread_id"] = self._thread_id
            await self._post(payload)
        logger.info("telegram.sent", extra={"chat_id": self._chat_id, "chunks": len(chunks)})


class ConsoleTelegramSender:
    """Fallback sender: logs/prints the text body. Used when Telegram is not configured."""

    async def send(self, *, text: str) -> None:
        logger.info("telegram.console", extra={"chars": len(text)})
        separator = "=" * 60
        block = "\n".join([separator, "TELEGRAM", separator, text, separator])
        # Encode-safe write (digest may contain non-ASCII like "→"); mirror the email
        # console fallback so a cp1252 stdout never raises UnicodeEncodeError.
        enc = sys.stdout.encoding or "utf-8"
        sys.stdout.write("\n" + block.encode(enc, "replace").decode(enc, "replace") + "\n")


def make_telegram_sender(settings: Settings) -> TelegramSender:
    """Factory: HttpTelegramSender when bot token + chat id are set, else Console no-op."""
    if settings.telegram_bot_token and settings.telegram_chat_id:
        logger.info(
            "telegram.factory",
            extra={
                "sender": "http",
                "has_topic": bool(settings.telegram_topic_polymarket.strip()),
            },
        )
        return HttpTelegramSender(
            api_base_url=settings.telegram_api_base_url,
            token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            topic_id=settings.telegram_topic_polymarket,
        )
    logger.info(
        "telegram.factory",
        extra={"sender": "console", "reason": "bot token or chat id not configured"},
    )
    return ConsoleTelegramSender()
