"""Tests for the Telegram sender: payload shape, topic id, chunking, factory, no-leak.

The httpx boundary is mocked with respx — no network. asyncio_mode="auto", so async
tests need no decorator (matches tests/sources/test_kalshi.py).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from app.config import Settings
from app.core.errors import SourceError
from app.notifications.telegram import (
    ConsoleTelegramSender,
    HttpTelegramSender,
    _chunk,
    make_telegram_sender,
)

BASE = "https://telegram.test"
TOKEN = "123456:SECRET-TOKEN"  # noqa: S105  (test fixture, not a real credential)
SEND_URL = f"{BASE}/bot{TOKEN}/sendMessage"


def _http_sender(topic_id: str = "") -> HttpTelegramSender:
    return HttpTelegramSender(
        api_base_url=BASE,
        token=TOKEN,
        chat_id="-1001234567890",
        topic_id=topic_id,
        rate_per_sec=1000.0,  # keep tests fast; no real pacing needed
    )


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------

async def test_posts_chat_id_and_text_without_topic_when_blank() -> None:
    async with respx.mock:
        route = respx.post(SEND_URL).mock(return_value=Response(200, json={"ok": True}))
        await _http_sender(topic_id="").send(text="hello")

    assert route.called
    body = route.calls.last.request.read().decode()
    import json

    payload = json.loads(body)
    assert payload["chat_id"] == "-1001234567890"
    assert payload["text"] == "hello"
    assert "message_thread_id" not in payload  # blank topic -> omitted
    # Plain text: we never set parse_mode (unescaped $/_/→ must not 400).
    assert "parse_mode" not in payload


async def test_includes_message_thread_id_when_topic_configured() -> None:
    async with respx.mock:
        route = respx.post(SEND_URL).mock(return_value=Response(200, json={"ok": True}))
        await _http_sender(topic_id="42").send(text="hello")

    import json

    payload = json.loads(route.calls.last.request.read().decode())
    assert payload["message_thread_id"] == 42  # int, per the Bot API


async def test_non_numeric_topic_id_is_omitted() -> None:
    """Fail-safe: a malformed topic id posts to General rather than crashing."""
    async with respx.mock:
        route = respx.post(SEND_URL).mock(return_value=Response(200, json={"ok": True}))
        await _http_sender(topic_id="not-a-number").send(text="hi")

    import json

    payload = json.loads(route.calls.last.request.read().decode())
    assert "message_thread_id" not in payload


# ---------------------------------------------------------------------------
# Chunking (> 4096 chars must not raise)
# ---------------------------------------------------------------------------

def test_chunk_returns_single_chunk_when_short() -> None:
    assert _chunk("short") == ["short"]


def test_chunk_splits_on_newline_boundaries() -> None:
    # Three 2000-char lines -> can't fit all in one 4096 chunk; splits on "\n".
    lines = ["A" * 2000, "B" * 2000, "C" * 2000]
    chunks = _chunk("\n".join(lines), limit=4096)
    assert len(chunks) >= 2
    assert all(len(c) <= 4096 for c in chunks)
    # Every original line survives intact across the joined chunks.
    rejoined = "\n".join(chunks)
    for line in lines:
        assert line in rejoined


def test_chunk_hard_splits_a_single_overlong_line() -> None:
    chunks = _chunk("X" * 9000, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == "X" * 9000


async def test_long_text_is_sent_as_multiple_posts() -> None:
    async with respx.mock:
        route = respx.post(SEND_URL).mock(return_value=Response(200, json={"ok": True}))
        await _http_sender().send(text="Y" * 9000)  # > 2x the 4096 limit

    assert route.call_count >= 3
    for call in route.calls:
        import json

        payload = json.loads(call.request.read().decode())
        assert len(payload["text"]) <= 4096


# ---------------------------------------------------------------------------
# Error handling — never leaks the token
# ---------------------------------------------------------------------------

async def test_http_error_raises_sourceerror_without_token() -> None:
    async with respx.mock:
        respx.post(SEND_URL).mock(return_value=Response(400, json={"ok": False}))
        with pytest.raises(SourceError) as exc_info:
            await _http_sender().send(text="boom")

    message = str(exc_info.value)
    assert TOKEN not in message
    assert "400" in message


async def test_transport_error_raises_sourceerror_without_token() -> None:
    async with respx.mock:
        respx.post(SEND_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(SourceError) as exc_info:
            await _http_sender().send(text="boom")

    assert TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_returns_console_when_unconfigured() -> None:
    settings = Settings(telegram_bot_token="", telegram_chat_id="")
    assert isinstance(make_telegram_sender(settings), ConsoleTelegramSender)


def test_factory_returns_console_when_only_token_set() -> None:
    settings = Settings(telegram_bot_token="abc", telegram_chat_id="")
    assert isinstance(make_telegram_sender(settings), ConsoleTelegramSender)


def test_factory_returns_http_when_configured() -> None:
    settings = Settings(telegram_bot_token="abc", telegram_chat_id="-100999")
    assert isinstance(make_telegram_sender(settings), HttpTelegramSender)
